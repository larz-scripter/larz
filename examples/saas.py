"""
A complete micro-SaaS — accounts, a Pro plan with a trial, a customer portal,
background email, and an admin panel. ~60 lines.

    python3 examples/saas.py
    #  /            sign up / log in
    #  /app         free area (login required)
    #  /pro         Pro area (@app.plan, 7-day trial)
    #  /larz/account  self-serve billing portal
    #  /admin?token=admin123   admin panel over users
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.models import connect
import larz.money as money, larz.auth as auth, larz.ops as ops, larz.admin as admin

app = Larz(secret="saas-demo", debug=True)
connect("saas.db")
money.enable(app, base_url="http://127.0.0.1:8000", admin_token="admin123")
auth.enable(app)
ops.enable(app)
app.money.plan("pro", "$9/mo", features=["Unlimited projects", "Priority support"],
               trial_days=7)
admin.enable(app, [auth.User], token="admin123")

# send a welcome email in the background on grant (SMTP not configured in demo)
@app.money.on_grant
def _welcome(subject, sku):
    app.jobs.enqueue(lambda: None)          # here you'd ops.send_email(...)

def page(body):
    nav = ("<a href='/'>home</a> · <a href='/app'>app</a> · <a href='/pro'>pro</a> · "
           "<a href='/larz/account'>billing</a>")
    return Response("<style>body{font:16px system-ui;max-width:520px;margin:2rem auto;"
                    "padding:0 1rem}input{width:100%%;padding:9px;margin:5px 0}"
                    ".b{background:#0a7;color:#fff;border:0;padding:10px 16px;border-radius:8px}"
                    "</style>%s<hr>%s" % (body, nav))

@app.get("/")
def home(req):
    if req.user:
        return page("<h1>Hi %s</h1><p>You're signed in.</p>" % req.user.email)
    return page("<h1>Sign up</h1>"
        "<form method=post action=/signup><input name=email placeholder=email>"
        "<input name=password type=password placeholder=password>"
        "<button class=b>Create account</button></form>"
        "<h2>Or log in</h2><form method=post action=/login>"
        "<input name=email placeholder=email><input name=password type=password placeholder=password>"
        "<button class=b>Log in</button></form>")

@app.post("/signup")
def signup(req):
    try:
        app.auth.register(req.form["email"], req.form["password"])
    except ValueError:
        return page("<p>Email already registered.</p>")
    app.auth.login(req, req.form["email"], req.form["password"])
    return Response.redirect("/app")

@app.post("/login")
def login(req):
    if app.auth.login(req, req.form.get("email", ""), req.form.get("password", "")):
        return Response.redirect("/app")
    return page("<p>Wrong email or password.</p>")

@app.get("/logout")
def logout(req):
    app.auth.logout(req); return Response.redirect("/")

@app.login_required
@app.get("/app")
def userapp(req):
    return page("<h1>Your app</h1><p>Free tier. "
                "<a href='/pro'>Upgrade to Pro (7-day trial)</a>. "
                "<a href='/logout'>log out</a></p>")

@app.login_required
@app.plan("pro")
@app.get("/pro")
def pro(req):
    return page("<h1>⭐ Pro area</h1><p>Unlocked for %s.</p>" % req.user.email)

if __name__ == "__main__":
    app.run()
