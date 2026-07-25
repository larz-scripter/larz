"""
A blog — ORM (relationships), template inheritance + filters, and an admin panel.

    python3 examples/blog.py
    #  /                list posts
    #  /post/<id>       read a post
    #  /admin?token=admin123   write/edit posts

Shows: larz.models (ForeignKey, DateTimeField, queries), templating inheritance
and filters, and larz.admin auto-CRUD — no manual forms.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.models import Model, StrField, TextField, DateTimeField, BoolField, connect
from larz.templating import Environment
import larz.admin as admin

app = Larz(secret="blog-demo", debug=True)
connect("blog.db")

class Post(Model):
    title = StrField()
    body = TextField(default="")
    published = BoolField(default=True)
    created = DateTimeField(auto_now=True)

Post.create_table()
admin.enable(app, [Post], token="admin123")

# inline templates via an Environment (normally these are files in templates/)
env = Environment(directory=".")
BASE = ("<!doctype html><title>{% block title %}Blog{% endblock %}</title>"
        "<style>body{font:17px/1.6 Georgia,serif;max-width:640px;margin:2rem auto;padding:0 1rem}"
        "a{color:#0a7}h1{font-family:system-ui}</style>"
        "<header><a href='/'>← Blog</a> · <a href='/admin?token=admin123'>write</a></header>"
        "<main>{% block main %}{% endblock %}</main>")

def render(child, **ctx):
    # merge child into BASE (inheritance) and render
    return Response(env.from_string(child.replace("__BASE__", BASE)).render(**ctx))

@app.get("/")
def index(req):
    posts = Post.where(published=True).order("-created").all()
    tpl = ('{% extends "__BASE__inline" %}{% block title %}My Blog{% endblock %}'
           '{% block main %}<h1>My Blog</h1>'
           '{% for p in posts %}<article><h2><a href="/post/{{p.id}}">{{ p.title }}</a></h2>'
           '<p>{{ p.created | date }} — {{ p.body | truncate(120) }}</p></article>{% endfor %}'
           '{% endblock %}')
    return _render_inherit(tpl, BASE, posts=posts)

@app.get("/post/<id>")
def read(req):
    p = Post.get(int(req.params["id"]))
    if not p:
        return Response("not found", status=404)
    tpl = ('{% extends "__BASE__inline" %}{% block title %}{{ p.title }}{% endblock %}'
           '{% block main %}<h1>{{ p.title }}</h1><p><i>{{ p.created | date }}</i></p>'
           '<div>{{ p.body }}</div>{% endblock %}')
    return _render_inherit(tpl, BASE, p=p)

def _render_inherit(child, base, **ctx):
    # tiny helper: register base under a name so {% extends %} finds it
    class _Env(Environment):
        def get_source(self, name): return base
    e = _Env(directory=".")
    return Response(e.from_string(child.replace("__BASE__inline", "base")).render(**ctx))

if __name__ == "__main__":
    app.run()
