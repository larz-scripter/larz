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


def escape(value):
    return html.escape(str(value), quote=True)


class Template:
    def __init__(self, source, env=None, name="<string>"):
        self.name = name
        self.env = env
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
                expr = tok[2:-2].strip()
                if expr.endswith("| safe") or expr.endswith("|safe"):
                    expr = expr.rsplit("|", 1)[0].strip()
                    emit("_out.append(str(%s))" % expr)
                else:
                    emit("_out.append(_esc(%s))" % expr)
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
        ns["_ctx"] = ctx
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

    def render(self, name, **ctx):
        merged = dict(self.globals)
        merged.update(ctx)
        return self.get(name).render(**merged)
