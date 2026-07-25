"""
larz.cli — the `larz` command.

    larz new <name>     scaffold a new money-native app
    larz run [file]     run an app (defaults to app.py)
    larz routes [file]  print the route table
    larz version

Invoked via `python -m larz ...` (see larz/__main__.py) or a console script.
"""

import os
import sys
import importlib.util

from . import __version__

_TEMPLATE = '''\
from larz import Larz, Response
import larz.money as money

app = Larz(secret="change-me-in-prod")
money.enable(app, base_url="http://127.0.0.1:8000")
app.money.plan("pro", "$9/mo", features=["Everything in free", "Pro reports"],
               trial_days=7)


@app.get("/")
def home(req):
    return ("<h1>%s</h1><p>A money-native Larz app.</p>"
            "<p><a href='/pro'>/pro</a> (7-day trial) &middot; "
            "<a href='/larz/pricing'>pricing</a></p>" % {name!r})


@app.plan("pro")
@app.get("/pro")
def pro(req):
    return "<h1>Pro area</h1><p>Unlocked for %s.</p>" % req.subject


if __name__ == "__main__":
    app.run()
'''


def _load_app(path):
    path = path or "app.py"
    if not os.path.isfile(path):
        sys.exit("larz: no such file: %s" % path)
    spec = importlib.util.spec_from_file_location("_larz_user_app", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = getattr(mod, "app", None)
    if app is None:
        sys.exit("larz: %s defines no `app`" % path)
    return app


def cmd_new(args):
    name = args[0] if args else "myapp"
    os.makedirs(name, exist_ok=True)
    with open(os.path.join(name, "app.py"), "w") as f:
        f.write(_TEMPLATE.format(name=name))
    print("Created %s/app.py" % name)
    print("  cd %s && larz run" % name)


def cmd_run(args):
    app = _load_app(args[0] if args else None)
    host, port = "127.0.0.1", 8000
    app.run(host=host, port=port)


def cmd_routes(args):
    app = _load_app(args[0] if args else None)
    print("%-8s %-32s %s" % ("METHODS", "PATTERN", "HANDLER"))
    for r in app.routes:
        tag = ""
        h = r.handler
        if getattr(h, "_larz_paid", None):
            tag = " [paid]"
        elif getattr(h, "_larz_plan", None):
            tag = " [plan:%s]" % h._larz_plan
        elif getattr(h, "_larz_metered", None):
            tag = " [metered]"
        print("%-8s %-32s %s%s" % (",".join(sorted(r.methods)), r.pattern,
                                   getattr(h, "__name__", "?"), tag))


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "version":
        print("larz " + __version__)
    elif cmd == "new":
        cmd_new(rest)
    elif cmd == "run":
        cmd_run(rest)
    elif cmd == "routes":
        cmd_routes(rest)
    else:
        sys.exit("larz: unknown command %r (try `larz help`)" % cmd)
    return 0
