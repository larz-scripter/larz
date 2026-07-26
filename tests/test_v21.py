"""v2.1 tests — the project scaffolder (larz new) and real-world examples.
Plain python3, no pytest."""
import os, sys, io, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import cli

P = [0]; F = [0]
def ck(name, cond):
    if cond: P[0] += 1; print("  ok   " + name)
    else: F[0] += 1; print("  FAIL " + name)


def test_scaffolder():
    cwd = os.getcwd()
    tmp = tempfile.mkdtemp()
    os.chdir(tmp)
    try:
        for t in ["minimal", "api", "saas", "ai", "marketplace"]:
            name = "proj_" + t
            cli.cmd_new([name, "--template", t])
            # all project files present
            for f in ["app.py", "requirements.txt", ".env.example", ".gitignore",
                      "tests/test_app.py", "README.md"]:
                ck("%s: has %s" % (t, f), os.path.isfile(os.path.join(name, f)))
            # generated app.py is valid Python and name-substituted
            src = io.open(os.path.join(name, "app.py"), encoding="utf-8").read()
            try:
                compile(src, "app.py", "exec")
                valid = True
            except SyntaxError:
                valid = False
            ck("%s: app.py compiles" % t, valid)
            ck("%s: name substituted" % t, "@@NAME@@" not in src and name in src)
            ck("%s: README names template" % t,
               t in io.open(os.path.join(name, "README.md"), encoding="utf-8").read())
        # unknown template is rejected
        try:
            cli.cmd_new(["x", "--template", "nope"])
            rejected = False
        except SystemExit:
            rejected = True
        ck("unknown template rejected", rejected)
        # refuses to overwrite
        try:
            cli.cmd_new(["proj_api"])
            overwrote = True
        except SystemExit:
            overwrote = False
        ck("refuses to overwrite existing dir", not overwrote)
    finally:
        os.chdir(cwd)


def test_examples_import():
    """Every example file is valid Python (compiles)."""
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
    for fn in sorted(os.listdir(root)):
        if not fn.endswith(".py"):
            continue
        src = io.open(os.path.join(root, fn), encoding="utf-8").read()
        try:
            compile(src, fn, "exec")
            ok = True
        except SyntaxError as e:
            ok = False
            print("     %s: %s" % (fn, e))
        ck("example compiles: %s" % fn, ok)


def test_root_path():
    """Mount under a subpath: redirects + in-page links get the prefix."""
    from larz import Larz, Response
    from larz.testing import Client
    app = Larz(secret="x", root_path="/larz/demo")

    @app.get("/")
    def home(req):
        return ('<a href="/pricing">p</a> <a href="//cdn/x">c</a> '
                '<a href="http://a/b">a</a> <form action="/submit">'
                '<img src="/logo.png"></form>')

    @app.get("/go")
    def go(req):
        return Response.redirect("/dashboard")

    @app.get("/pricing")
    def pricing(req):
        return "priced"

    c = Client(app)
    h = c.get("/").text
    ck("root_path prefixes href", 'href="/larz/demo/pricing"' in h)
    ck("root_path prefixes action", 'action="/larz/demo/submit"' in h)
    ck("root_path prefixes src", 'src="/larz/demo/logo.png"' in h)
    ck("root_path leaves protocol-relative", 'href="//cdn/x"' in h)
    ck("root_path leaves absolute", 'href="http://a/b"' in h)
    ck("root_path prefixes redirect", c.get("/go").redirect == "/larz/demo/dashboard")
    ck("root_path route matches at root", c.get("/pricing").text == "priced")
    ck("app.url helper", app.url("/x") == "/larz/demo/x" and app.url("http://y") == "http://y")
    # no root_path -> unchanged
    a2 = Larz(secret="x")
    @a2.get("/")
    def h2(req): return '<a href="/x">y</a>'
    ck("no root_path leaves links", 'href="/x"' in Client(a2).get("/").text)


def main():
    for t in [test_scaffolder, test_examples_import, test_root_path]:
        print("\n# " + t.__name__)
        t()
    print("\n%d passed, %d failed" % (P[0], F[0]))
    return 1 if F[0] else 0


if __name__ == "__main__":
    sys.exit(main())
