"""
larz.models — a tiny active-record ORM over sqlite (zero-dep).

Enough to build a real app, not so much you need a manual:

    from larz.models import Model, Field, connect

    connect("app.db")

    class Post(Model):
        title = Field(str)
        body  = Field(str, default="")
        views = Field(int, default=0)

    Post.create_table()
    p = Post(title="Hello").save()
    Post.get(p.id)
    Post.where(views=0).all()
    Post.all(order="-id", limit=10)
    p.update(views=5)
    p.delete()

Fields: str, int, float, bool. Auto integer primary key `id`. Simple queries
(equality filters, ordering, limit). For anything fancy, drop to raw SQL via
`Model.query(sql, params)`.
"""

import sqlite3
import threading

__all__ = ["Model", "Field", "connect"]

_STATE = threading.local()
_DB_PATH = {"path": ":memory:"}
_SHARED = {"conn": None}


def connect(path):
    _DB_PATH["path"] = path
    _SHARED["conn"] = None            # reset shared in-memory conn if any


def _conn():
    # A single shared connection for :memory: (so schema persists); per-thread
    # file connections otherwise.
    if _DB_PATH["path"] == ":memory:":
        if _SHARED["conn"] is None:
            c = sqlite3.connect(":memory:", check_same_thread=False)
            c.row_factory = sqlite3.Row
            _SHARED["conn"] = c
        return _SHARED["conn"]
    c = getattr(_STATE, "conn", None)
    if c is None:
        c = sqlite3.connect(_DB_PATH["path"])
        c.row_factory = sqlite3.Row
        _STATE.conn = c
    return c


_PYTYPE_SQL = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER"}


class Field:
    def __init__(self, pytype=str, default=None, unique=False, index=False):
        self.pytype = pytype
        self.default = default
        self.unique = unique
        self.index = index
        self.name = None            # set by metaclass


class _Meta(type):
    def __new__(mcs, name, bases, ns):
        fields = {}
        for base in bases:
            fields.update(getattr(base, "_fields", {}))
        for k, v in list(ns.items()):
            if isinstance(v, Field):
                v.name = k
                fields[k] = v
                ns.pop(k)
        cls = super().__new__(mcs, name, bases, ns)
        cls._fields = fields
        cls._table = ns.get("__table__", name.lower() + "s")
        return cls


class Model(metaclass=_Meta):
    _fields = {}

    def __init__(self, **kw):
        self.id = kw.pop("id", None)
        for name, f in self._fields.items():
            setattr(self, name, kw.get(name, f.default))
        for k in kw:
            if k not in self._fields:
                raise TypeError("unknown field %r for %s" % (k, type(self).__name__))

    # -- schema ------------------------------------------------------------ #
    @classmethod
    def create_table(cls):
        cols = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for name, f in cls._fields.items():
            col = "%s %s" % (name, _PYTYPE_SQL.get(f.pytype, "TEXT"))
            if f.unique:
                col += " UNIQUE"
            cols.append(col)
        c = _conn()
        c.execute("CREATE TABLE IF NOT EXISTS %s (%s)" % (cls._table, ", ".join(cols)))
        for name, f in cls._fields.items():
            if f.index and not f.unique:
                c.execute("CREATE INDEX IF NOT EXISTS idx_%s_%s ON %s(%s)"
                          % (cls._table, name, cls._table, name))
        c.commit()

    @classmethod
    def drop_table(cls):
        c = _conn(); c.execute("DROP TABLE IF EXISTS %s" % cls._table); c.commit()

    # -- row <-> object ---------------------------------------------------- #
    @classmethod
    def _from_row(cls, row):
        obj = cls.__new__(cls)
        obj.id = row["id"]
        for name, f in cls._fields.items():
            val = row[name]
            if f.pytype is bool and val is not None:
                val = bool(val)
            setattr(obj, name, val)
        return obj

    def _values(self):
        vals = []
        for name, f in self._fields.items():
            v = getattr(self, name)
            if f.pytype is bool and v is not None:
                v = 1 if v else 0
            vals.append(v)
        return vals

    # -- persistence ------------------------------------------------------- #
    def save(self):
        c = _conn()
        names = list(self._fields.keys())
        if self.id is None:
            ph = ", ".join("?" for _ in names)
            cur = c.execute("INSERT INTO %s (%s) VALUES (%s)"
                            % (self._table, ", ".join(names), ph), self._values())
            self.id = cur.lastrowid
        else:
            setclause = ", ".join("%s=?" % n for n in names)
            c.execute("UPDATE %s SET %s WHERE id=?" % (self._table, setclause),
                      self._values() + [self.id])
        c.commit()
        return self

    def update(self, **kw):
        for k, v in kw.items():
            if k not in self._fields:
                raise TypeError("unknown field %r" % k)
            setattr(self, k, v)
        return self.save()

    def delete(self):
        c = _conn()
        c.execute("DELETE FROM %s WHERE id=?" % self._table, [self.id])
        c.commit()

    # -- queries ----------------------------------------------------------- #
    @classmethod
    def get(cls, id):
        row = _conn().execute("SELECT * FROM %s WHERE id=?" % cls._table, [id]).fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def all(cls, order=None, limit=None):
        return cls.where(order=order, limit=limit).all()

    @classmethod
    def where(cls, **filters):
        return _Query(cls).filter(**filters)

    @classmethod
    def count(cls, **filters):
        return _Query(cls).filter(**filters).count()

    @classmethod
    def query(cls, sql, params=()):
        return [cls._from_row(r) for r in _conn().execute(sql, params).fetchall()]

    def to_dict(self):
        d = {"id": self.id}
        for name in self._fields:
            d[name] = getattr(self, name)
        return d


class _Query:
    def __init__(self, model):
        self.model = model
        self._filters = {}
        self._order = None
        self._limit = None

    def filter(self, order=None, limit=None, **eq):
        self._filters.update(eq)
        if order is not None:
            self._order = order
        if limit is not None:
            self._limit = limit
        return self

    def _build(self, select="*"):
        sql = "SELECT %s FROM %s" % (select, self.model._table)
        params = []
        if self._filters:
            clause = " AND ".join("%s=?" % k for k in self._filters)
            sql += " WHERE " + clause
            params = list(self._filters.values())
        if self._order:
            col = self._order
            direction = "ASC"
            if col.startswith("-"):
                col, direction = col[1:], "DESC"
            sql += " ORDER BY %s %s" % (col, direction)
        if self._limit is not None:
            sql += " LIMIT %d" % int(self._limit)
        return sql, params

    def all(self):
        sql, params = self._build()
        return [self.model._from_row(r) for r in _conn().execute(sql, params).fetchall()]

    def first(self):
        self._limit = 1
        rows = self.all()
        return rows[0] if rows else None

    def count(self):
        sql, params = self._build("COUNT(*) AS n")
        return _conn().execute(sql, params).fetchone()["n"]
