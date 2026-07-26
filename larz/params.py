"""
larz.params — typed request binding & dependency injection.

Annotate a handler's parameters and Larz validates, coerces and injects them —
FastAPI-style, zero dependencies:

    @app.post("/items")
    def create(req, name: str, qty: int = 1, tag: str = Query(None)):
        ...                       # name from body/form, coerced; qty optional

    @dataclass
    class Item:
        name: str
        price: float
        qty: int = 1

    @app.post("/order")
    def order(req, item: Item):   # the whole JSON body -> validated Item
        return {"total": item.price * item.qty}

    def current_user(req):
        return db_lookup(req.session.get("user"))

    @app.get("/me")
    def me(req, user=Depends(current_user)):   # resolved & cached per request
        return user.email

Bad input yields a 422 with per-field errors. Handlers that take only `req`
are untouched — binding is opt-in by adding annotated parameters.
"""

import json
import inspect

try:                                     # dataclasses is 3.7+ (Larz's floor); the
    import dataclasses                   # rest of params works without it on 3.6
except ImportError:                      # pragma: no cover
    dataclasses = None

from .core import Response

__all__ = ["Depends", "Query", "Path", "Body", "Form", "bind", "needs_binding"]

_EMPTY = inspect.Parameter.empty


class Depends:
    def __init__(self, dependency):
        self.dependency = dependency


class _Source:
    def __init__(self, default=_EMPTY, alias=None):
        self.default = default
        self.alias = alias


class Query(_Source): where = "query"
class Path(_Source):  where = "path"
class Body(_Source):  where = "body"
class Form(_Source):  where = "form"


_TRUTHY = {"1", "true", "yes", "on", "t"}


def needs_binding(handler):
    """True if the handler declares parameters beyond `req` (so it wants binding)."""
    cached = getattr(handler, "_larz_bind", None)
    if cached is not None:
        return cached
    try:
        params = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        params = []
    result = len(params) > 1
    try:
        handler._larz_bind = result
    except (AttributeError, TypeError):
        pass
    return result


def _coerce(raw, ann):
    """Coerce a raw value to the annotated type. Returns (value, ok)."""
    if ann is _EMPTY or ann is str:
        return (raw if isinstance(raw, str) else str(raw)), True
    try:
        if ann is int:
            return int(raw), True
        if ann is float:
            return float(raw), True
        if ann is bool:
            if isinstance(raw, bool):
                return raw, True
            return str(raw).strip().lower() in _TRUTHY, True
        if ann in (list, dict):
            val = json.loads(raw) if isinstance(raw, str) else raw
            return (val, True) if isinstance(val, ann) else (None, False)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None, False
    return raw, True


def _build_dataclass(ann, data, errors, prefix=""):
    if not isinstance(data, dict):
        errors[prefix.rstrip(".") or "body"] = "expected an object"
        return None
    kwargs = {}
    for f in dataclasses.fields(ann):
        key = prefix + f.name
        if f.name in data:
            if dataclasses and dataclasses.is_dataclass(f.type):
                kwargs[f.name] = _build_dataclass(f.type, data[f.name], errors, key + ".")
            else:
                val, ok = _coerce(data[f.name], f.type)
                if ok:
                    kwargs[f.name] = val
                else:
                    errors[key] = "expected %s" % getattr(f.type, "__name__", f.type)
        elif f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING:  # noqa
            pass                                    # optional field
        else:
            errors[key] = "required"
    try:
        return ann(**kwargs)
    except TypeError:
        return None


def _request_data(req):
    """Best-effort dict of the request body (json or form)."""
    if req.method in ("POST", "PUT", "PATCH", "DELETE"):
        j = req.json()
        if isinstance(j, dict):
            return j
        if req.form:
            return dict(req.form)
    return {}


def _resolve_dep(dep, req, cache):
    key = id(dep)
    if key in cache:
        return cache[key]
    try:
        takes = len(inspect.signature(dep).parameters)
    except (TypeError, ValueError):
        takes = 0
    val = dep(req) if takes else dep()
    cache[key] = val
    return val


def bind(handler, req):
    """Resolve a handler's annotated parameters from the request.
    Returns (kwargs, None) on success or (None, Response) on a 422."""
    sig = inspect.signature(handler)
    params = list(sig.parameters.values())[1:]     # skip `req`
    body = None
    kwargs, errors = {}, {}
    cache = req.__dict__.setdefault("_dep_cache", {})

    for p in params:
        name, ann, default = p.name, p.annotation, p.default

        if isinstance(default, Depends):
            kwargs[name] = _resolve_dep(default.dependency, req, cache)
            continue

        # dataclass annotation -> validate the whole JSON body
        if ann is not _EMPTY and dataclasses and dataclasses.is_dataclass(ann):
            if body is None:
                body = _request_data(req)
            obj = _build_dataclass(ann, body, errors)
            if obj is not None:
                kwargs[name] = obj
            continue

        src = default if isinstance(default, _Source) else None
        alias = (src.alias if src else None) or name

        # locate the raw value
        raw = None
        if src and src.where == "path":
            raw = req.params.get(alias)
        elif src and src.where == "query":
            raw = req.query.get(alias)
        elif src and src.where == "form":
            raw = req.form.get(alias)
        else:
            if alias in req.params:
                raw = req.params.get(alias)
            elif req.method in ("POST", "PUT", "PATCH"):
                if body is None:
                    body = _request_data(req)
                raw = body.get(alias, req.query.get(alias))
            else:
                raw = req.query.get(alias)

        if raw is None:
            if src is not None and src.default is not _EMPTY:
                kwargs[name] = src.default
            elif default is not _EMPTY and not isinstance(default, _Source):
                kwargs[name] = default
            else:
                errors[alias] = "required"
            continue

        val, ok = _coerce(raw, ann)
        if not ok:
            errors[alias] = "expected %s" % getattr(ann, "__name__", str(ann))
        else:
            kwargs[name] = val

    if errors:
        return None, Response.json(
            {"error": "validation_failed", "fields": errors}, status=422)
    return kwargs, None
