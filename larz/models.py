"""
larz.models — a small but capable active-record ORM over sqlite (zero-dep).

Fields:
    IntField, FloatField, StrField/TextField, BoolField, DateTimeField, DateField,
    JSONField, DecimalField, ForeignKey  (and the generic Field(pytype=...))

Queries:
    Post.where(views__gt=100, title__like="%larz%").order("-created").page(2)
    Post.get(id) · Post.all(order=, limit=) · .count() · .exists() · .first()
    field ops: __gt __gte __lt __lte __ne __in __like __contains

Also: relationships (ForeignKey + related access), model hooks (before_save /
after_save), transactions, unique + index, and lightweight auto-migrations.
"""

import json
import sqlite3
import decimal
import datetime
import threading

__all__ = ["Model", "Field", "IntField", "FloatField", "StrField", "TextField",
           "BoolField", "DateTimeField", "DateField", "JSONField", "DecimalField",
           "ForeignKey", "connect", "transaction"]

_STATE = threading.local()
_DB_PATH = {"path": ":memory:"}
_SHARED = {"conn": None}


def connect(path):
    """Point the ORM at a database. `path` is a sqlite file, ':memory:', or a
    'postgres://user:pass@host:port/db' URL (pure-Python driver, see larz.pg)."""
    _DB_PATH["path"] = path
    _SHARED["conn"] = None
    _STATE.conn = None


def _is_pg(path):
    return isinstance(path, str) and path.startswith(("postgres://", "postgresql://"))


def _conn():
    path = _DB_PATH["path"]
    if _is_pg(path):
        from .pg import PgAdapter, connect as _pgconnect
        c = getattr(_STATE, "conn", None)
        if not isinstance(c, PgAdapter):
            c = PgAdapter(_pgconnect(path))
            _STATE.conn = c
        return c
    if path == ":memory:":
        if _SHARED["conn"] is None:
            c = sqlite3.connect(":memory:", check_same_thread=False)
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA foreign_keys=ON")
            _SHARED["conn"] = c
        return _SHARED["conn"]
    c = getattr(_STATE, "conn", None)
    if c is None or not hasattr(c, "row_factory"):
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        _STATE.conn = c
    return c


def _txn_depth():
    return getattr(_STATE, "txn_depth", 0)


class transaction:
    """Context manager: commit on success, rollback on exception. While open,
    Model.save/delete defer their per-call commit so the whole block is atomic."""
    def __enter__(self):
        _STATE.txn_depth = _txn_depth() + 1
        self.c = _conn()
        return self.c

    def __exit__(self, exc_type, exc, tb):
        _STATE.txn_depth = _txn_depth() - 1
        if exc_type:
            self.c.rollback()
        elif _txn_depth() == 0:
            self.c.commit()
        return False


# --------------------------------------------------------------------------- #
#  Fields
# --------------------------------------------------------------------------- #
class Field:
    sql = "TEXT"

    def __init__(self, pytype=None, default=None, unique=False, index=False,
                 null=True, column=None):
        self.pytype = pytype or str
        self.default = default
        self.unique = unique
        self.index = index
        self.null = null
        self.name = None
        self.column = column
        if pytype is int:
            self.sql = "INTEGER"
        elif pytype is float:
            self.sql = "REAL"
        elif pytype is bool:
            self.sql = "INTEGER"

    def col(self):
        return self.column or self.name

    def to_db(self, v):
        if v is None:
            return None
        if self.pytype is bool:
            return 1 if v else 0
        return v

    def from_db(self, v):
        if v is None:
            return None
        if self.pytype is bool:
            return bool(v)
        return v


class IntField(Field):
    sql = "INTEGER"
    def __init__(self, **kw): super().__init__(int, **kw)

class FloatField(Field):
    sql = "REAL"
    def __init__(self, **kw): super().__init__(float, **kw)

class StrField(Field):
    sql = "TEXT"
    def __init__(self, **kw): super().__init__(str, **kw)

class TextField(StrField):
    pass

class BoolField(Field):
    sql = "INTEGER"
    def __init__(self, **kw): super().__init__(bool, **kw)

class DateTimeField(Field):
    sql = "TEXT"
    def __init__(self, auto_now=False, **kw):
        super().__init__(datetime.datetime, **kw)
        self.auto_now = auto_now
    def to_db(self, v):
        if v is None: return None
        if isinstance(v, str): return v
        return v.isoformat()
    def from_db(self, v):
        if v is None or not isinstance(v, str): return v
        fromiso = getattr(datetime.datetime, "fromisoformat", None)
        if fromiso:
            try: return fromiso(v)
            except Exception: pass
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try: return datetime.datetime.strptime(v, fmt)
            except Exception: pass
        return v

class DateField(Field):
    sql = "TEXT"
    def __init__(self, **kw): super().__init__(datetime.date, **kw)
    def to_db(self, v):
        return v.isoformat() if hasattr(v, "isoformat") else v
    def from_db(self, v):
        if v is None or not isinstance(v, str): return v
        fromiso = getattr(datetime.date, "fromisoformat", None)
        if fromiso:
            try: return fromiso(v)
            except Exception: pass
        try: return datetime.datetime.strptime(v, "%Y-%m-%d").date()
        except Exception: return v

class JSONField(Field):
    sql = "TEXT"
    def __init__(self, **kw): super().__init__(dict, **kw)
    def to_db(self, v):
        return None if v is None else json.dumps(v)
    def from_db(self, v):
        if v is None: return None
        try: return json.loads(v)
        except Exception: return v

class DecimalField(Field):
    sql = "TEXT"
    def __init__(self, **kw): super().__init__(decimal.Decimal, **kw)
    def to_db(self, v):
        return None if v is None else str(v)
    def from_db(self, v):
        return None if v is None else decimal.Decimal(v)

class ForeignKey(Field):
    sql = "INTEGER"
    def __init__(self, to, **kw):
        super().__init__(int, **kw)
        self.to = to               # target Model class
    def col(self):
        return (self.column or self.name)


_PYTYPE_SQL = {int: "INTEGER", float: "REAL", str: "TEXT", bool: "INTEGER",
               dict: "TEXT", decimal.Decimal: "TEXT"}


# --------------------------------------------------------------------------- #
#  Metaclass + Model
# --------------------------------------------------------------------------- #
class _Meta(type):
    def __new__(mcs, name, bases, ns):
        fields = {}
        fks = {}
        for base in bases:
            fields.update(getattr(base, "_fields", {}))
            fks.update(getattr(base, "_fks", {}))
        for k, v in list(ns.items()):
            if isinstance(v, Field):
                v.name = k
                fields[k] = v
                if isinstance(v, ForeignKey):
                    fks[k] = v
                ns.pop(k)
        cls = super().__new__(mcs, name, bases, ns)
        cls._fields = fields
        cls._fks = fks
        cls._table = ns.get("__table__", name.lower() + "s")
        return cls


class Model(metaclass=_Meta):
    _fields = {}
    _fks = {}

    def __init__(self, **kw):
        self.id = kw.pop("id", None)
        for name, f in self._fields.items():
            if isinstance(f, ForeignKey):
                # accept either <name>=obj or <name>_id=int
                if name in kw:
                    obj = kw.pop(name)
                    setattr(self, name + "_id", obj.id if obj is not None else None)
                else:
                    setattr(self, name + "_id", kw.pop(name + "_id", f.default))
            else:
                setattr(self, name, kw.pop(name, f.default))
        for k in list(kw):
            raise TypeError("unknown field %r for %s" % (k, type(self).__name__))

    # -- hooks (override in subclasses) ------------------------------------ #
    def before_save(self): pass
    def after_save(self): pass

    # -- schema ------------------------------------------------------------ #
    @classmethod
    def _columns(cls):
        cols = {}
        for name, f in cls._fields.items():
            cols[f.col() + ("_id" if isinstance(f, ForeignKey) else "")] = f
        return cols

    @classmethod
    def create_table(cls):
        defs = ["id INTEGER PRIMARY KEY AUTOINCREMENT"]
        for name, f in cls._fields.items():
            col = f.col() + ("_id" if isinstance(f, ForeignKey) else "")
            d = "%s %s" % (col, f.sql)
            if f.unique:
                d += " UNIQUE"
            defs.append(d)
        c = _conn()
        c.execute("CREATE TABLE IF NOT EXISTS %s (%s)" % (cls._table, ", ".join(defs)))
        # lightweight migration: add any missing columns
        existing = {r["name"] for r in c.execute("PRAGMA table_info(%s)" % cls._table)}
        for name, f in cls._fields.items():
            col = f.col() + ("_id" if isinstance(f, ForeignKey) else "")
            if col not in existing:
                c.execute("ALTER TABLE %s ADD COLUMN %s %s" % (cls._table, col, f.sql))
        for name, f in cls._fields.items():
            if f.index and not f.unique:
                col = f.col() + ("_id" if isinstance(f, ForeignKey) else "")
                c.execute("CREATE INDEX IF NOT EXISTS idx_%s_%s ON %s(%s)"
                          % (cls._table, col, cls._table, col))
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
            if isinstance(f, ForeignKey):
                setattr(obj, name + "_id", row[f.col() + "_id"])
            else:
                setattr(obj, name, f.from_db(row[f.col()]))
        return obj

    def _dbvalues(self):
        cols, vals = [], []
        for name, f in self._fields.items():
            if isinstance(f, ForeignKey):
                cols.append(f.col() + "_id")
                vals.append(getattr(self, name + "_id", None))
            else:
                cols.append(f.col())
                vals.append(f.to_db(getattr(self, name)))
        return cols, vals

    # -- persistence ------------------------------------------------------- #
    def save(self):
        for name, f in self._fields.items():
            if isinstance(f, DateTimeField) and getattr(f, "auto_now", False):
                setattr(self, name, datetime.datetime.now())
        self.before_save()
        c = _conn()
        cols, vals = self._dbvalues()
        if self.id is None:
            ph = ", ".join("?" for _ in cols)
            cur = c.execute("INSERT INTO %s (%s) VALUES (%s)"
                            % (self._table, ", ".join(cols), ph), vals)
            self.id = cur.lastrowid
        else:
            setc = ", ".join("%s=?" % col for col in cols)
            c.execute("UPDATE %s SET %s WHERE id=?" % (self._table, setc), vals + [self.id])
        if _txn_depth() == 0:
            c.commit()
        self.after_save()
        return self

    def update(self, **kw):
        for k, v in kw.items():
            if k in self._fields and isinstance(self._fields[k], ForeignKey):
                setattr(self, k + "_id", v.id if hasattr(v, "id") else v)
            elif k in self._fields:
                setattr(self, k, v)
            else:
                raise TypeError("unknown field %r" % k)
        return self.save()

    def delete(self):
        c = _conn(); c.execute("DELETE FROM %s WHERE id=?" % self._table, [self.id])
        if _txn_depth() == 0: c.commit()

    # -- relationships ----------------------------------------------------- #
    def __getattr__(self, name):
        # lazy foreign-key access: post.user -> User.get(post.user_id)
        fks = type(self).__dict__.get("_fks") or getattr(type(self), "_fks", {})
        if name in fks:
            return fks[name].to.get(getattr(self, name + "_id", None))
        raise AttributeError(name)

    # -- queries ----------------------------------------------------------- #
    @classmethod
    def get(cls, id):
        if id is None:
            return None
        row = _conn().execute("SELECT * FROM %s WHERE id=?" % cls._table, [id]).fetchone()
        return cls._from_row(row) if row else None

    @classmethod
    def all(cls, order=None, limit=None):
        return _Query(cls).filter(order=order, limit=limit).all()

    @classmethod
    def where(cls, **filters):
        return _Query(cls).filter(**filters)

    @classmethod
    def count(cls, **filters):
        return _Query(cls).filter(**filters).count()

    @classmethod
    def query(cls, sql, params=()):
        return [cls._from_row(r) for r in _conn().execute(sql, params).fetchall()]

    @classmethod
    def create(cls, **kw):
        return cls(**kw).save()

    def to_dict(self):
        d = {"id": self.id}
        for name, f in self._fields.items():
            if isinstance(f, ForeignKey):
                d[name + "_id"] = getattr(self, name + "_id", None)
            else:
                v = getattr(self, name)
                if isinstance(v, (datetime.datetime, datetime.date)):
                    v = v.isoformat()
                elif isinstance(v, decimal.Decimal):
                    v = str(v)
                d[name] = v
        return d


_OPS = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "ne": "!=",
        "like": "LIKE", "contains": "LIKE"}


class _Query:
    def __init__(self, model):
        self.model = model
        self._where = []       # (sql_fragment, params)
        self._order = None
        self._limit = None
        self._offset = None

    def filter(self, order=None, limit=None, offset=None, **conds):
        for key, val in conds.items():
            if "__" in key:
                field, op = key.rsplit("__", 1)
            else:
                field, op = key, "eq"
            col = self._col(field)
            if op == "eq":
                self._where.append(("%s = ?" % col, [self._enc(field, val)]))
            elif op == "in":
                marks = ", ".join("?" for _ in val)
                self._where.append(("%s IN (%s)" % (col, marks), list(val)))
            elif op == "contains":
                self._where.append(("%s LIKE ?" % col, ["%" + str(val) + "%"]))
            elif op in _OPS:
                self._where.append(("%s %s ?" % (col, _OPS[op]), [self._enc(field, val)]))
            else:
                raise ValueError("unknown operator %r" % op)
        if order is not None: self._order = order
        if limit is not None: self._limit = limit
        if offset is not None: self._offset = offset
        return self

    def _col(self, field):
        f = self.model._fields.get(field)
        if f is not None and isinstance(f, ForeignKey):
            return f.col() + "_id"
        return field

    def _enc(self, field, val):
        f = self.model._fields.get(field)
        if isinstance(f, ForeignKey):
            return val.id if hasattr(val, "id") else val
        return f.to_db(val) if f else val

    def order(self, col):
        self._order = col; return self

    def limit(self, n):
        self._limit = n; return self

    def offset(self, n):
        self._offset = n; return self

    def page(self, n, per_page=20):
        self._limit = per_page
        self._offset = max(0, (n - 1) * per_page)
        return self

    def _build(self, select="*"):
        sql = "SELECT %s FROM %s" % (select, self.model._table)
        params = []
        if self._where:
            sql += " WHERE " + " AND ".join(w for w, _ in self._where)
            for _, p in self._where: params += p
        if self._order:
            col, direction = self._order, "ASC"
            if col.startswith("-"): col, direction = col[1:], "DESC"
            sql += " ORDER BY %s %s" % (col, direction)
        if self._limit is not None: sql += " LIMIT %d" % int(self._limit)
        if self._offset is not None: sql += " OFFSET %d" % int(self._offset)
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

    def exists(self):
        return self.count() > 0

    def delete(self):
        sql, params = self._build()
        ids = [r.id for r in self.all()]
        for i in ids:
            _conn().execute("DELETE FROM %s WHERE id=?" % self.model._table, [i])
        _conn().commit()
        return len(ids)
