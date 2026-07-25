"""
larz.templating — a small but real template engine (zero-dep).

Supports:
    {{ expr }}                 auto-HTML-escaped output
    {{ expr | safe }}          raw (unescaped) output
    {% if cond %}…{% elif %}…{% else %}…{% endif %}
    {% for x in items %}…{% endfor %}
    {% set name = expr %}
    {% include "other.html" %}
    {# comment #}

A template compiles once to a Python code object, then executes against the
render context each call. Expressions are plain Python evaluated against that
context (author-trusted, same trust model as Jinja/Django templates).
"""

import re
import os
import html

__all__ = ["Template", "Environment"]

_TOKEN = re.compile(r"({{.*?}}|{%.*?%}|{#.*?#})", re.DOTALL)
_BLOCK = re.compile(r"{%\s*block\s+(\w+)\s*%}(.*?){%\s*endblock\s*%}", re.DOTALL)
_EXTENDS = re.compile(r'^\s*{%\s*extends\s+["\']([^"\']+)["\']\s*%}')


def escape(value):
    return html.escape(str(value), quote=True)


# --- template filters (used as `{{ x | upper }}`) ------------------------- #
def _default(v, d=""):
    return v if v not in (None, "") else d

def _currency(v, symbol="$"):
    return "%s%.2f" % (symbol, float(v))

def _date(v, fmt="%Y-%m-%d"):
    return v.strftime(fmt) if hasattr(v, "strftime") else str(v)

FILTERS = {
    "upper": lambda s: str(s).upper(),
    "lower": lambda s: str(s).lower(),
    "title": lambda s: str(s).title(),
    "capitalize": lambda s: str(s).capitalize(),
    "trim": lambda s: str(s).strip(),
    "length": lambda s: len(s),
    "default": _default,
    "currency": _currency,
    "date": _date,
    "join": lambda s, sep=", ": sep.join(str(x) for x in s),
    "round": lambda s, n=0: round(float(s), int(n)),
    "truncate": lambda s, n=50: str(s) if len(str(s)) <= n else str(s)[:n] + "…",
}


def _resolve_inheritance(source, env):
    """Resolve {% extends %} + {% block %} by merging with the parent template."""
    m = _EXTENDS.match(source)
    if not m:
        # no inheritance: render each block's body inline (strip the tags)
        return _BLOCK.sub(lambda mo: mo.group(2), source)
    parent_name = m.group(1)
    child_blocks = {name: body for name, body in _BLOCK.findall(source)}
    parent_src = env.get_source(parent_name)
    merged = _BLOCK.sub(lambda mo: child_blocks.get(mo.group(1), mo.group(2)), parent_src)
    return _resolve_inheritance(merged, env)      # parent may extend too


class Template:
    def __init__(self, source, env=None, name="<string>"):
        self.name = name
        self.env = env
        if env is not None and (_EXTENDS.match(source) or _BLOCK.search(source)):
            source = _resolve_inheritance(source, env)
        self._code = self._compile(source)

    def _compile(self, source):
        lines = []
        indent = 0

        def emit(txt):
            lines.append("    " * indent + txt)

        for tok in _TOKEN.split(source):
            if not tok:
                continue
            if tok.startswith("{#"):
                continue
            if tok.startswith("{{"):
                parts = [p.strip() for p in tok[2:-2].split("|")]
                expr, filters = parts[0], parts[1:]
                safe = "safe" in filters
                code = expr
                for flt in filters:
                    if flt == "safe":
                        continue
                    if "(" in flt:
                        fname, args = flt.split("(", 1)
                        code = "_flt[%r](%s, %s)" % (fname.strip(), code, args.rstrip(")"))
                    else:
                        code = "_flt[%r](%s)" % (flt, code)
                emit("_out.append(str(%s))" % code if safe
                     else "_out.append(_esc(%s))" % code)
            elif tok.startswith("{%"):
                stmt = tok[2:-2].strip()
                kw = stmt.split(" ", 1)[0]
                if kw in ("if", "for"):
                    emit("%s:" % stmt); indent += 1
                elif kw == "elif":
                    indent -= 1; emit("%s:" % stmt); indent += 1
                elif kw == "else":
                    indent -= 1; emit("else:"); indent += 1
                elif kw in ("endif", "endfor"):
                    indent -= 1
                elif kw == "set":
                    emit(stmt[3:].strip())               # `set x = y` -> `x = y`
                elif kw == "include":
                    m = re.search(r'["\'](.+?)["\']', stmt)
                    if m and self.env is not None:
                        emit("_out.append(_env.render(%r, **_ctx))" % m.group(1))
                else:
                    raise SyntaxError("unknown tag {%% %s %%} in %s" % (kw, self.name))
            else:
                emit("_out.append(%r)" % tok)

        if indent != 0:
            raise SyntaxError("unbalanced block tags in %s" % self.name)
        src = "\n".join(lines) if lines else "pass"
        return compile(src, "<larz-template:%s>" % self.name, "exec")

    def render(self, **ctx):
        ns = dict(ctx)
        ns["_out"] = []
        ns["_esc"] = escape
        ns["_env"] = self.env
        ns["_flt"] = FILTERS
        ns["_ctx"] = ctx
        # undefined template variables render as empty (like Jinja), rather than
        # raising NameError — but leave builtins (str, len, …) alone.
        import builtins
        for name in self._code.co_names:
            if name not in ns and not hasattr(builtins, name):
                ns[name] = ""
        exec(self._code, ns)
        return "".join(ns["_out"])


class Environment:
    """Loads and caches templates from a directory."""

    def __init__(self, directory="templates", auto_reload=False, globals=None):
        self.directory = directory
        self.auto_reload = auto_reload
        self.globals = globals or {}
        self._cache = {}

    def from_string(self, source, name="<string>"):
        return Template(source, env=self, name=name)

    def get(self, name):
        if name in self._cache and not self.auto_reload:
            return self._cache[name]
        path = os.path.join(self.directory, name)
        with open(path, "r", encoding="utf-8") as f:
            tpl = Template(f.read(), env=self, name=name)
        self._cache[name] = tpl
        return tpl

    def get_source(self, name):
        with open(os.path.join(self.directory, name), "r", encoding="utf-8") as f:
            return f.read()

    def render(self, name, **ctx):
        merged = dict(self.globals)
        merged.update(ctx)
        return self.get(name).render(**merged)
