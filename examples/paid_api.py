"""
A monetized JSON API — API keys, per-call metering, validation, and auto docs.

    python3 examples/paid_api.py
    #  POST /api/summarize  (needs an API key; billed per call)
    #  GET  /docs           (interactive OpenAPI)

Shows: larz.auth API keys (plan-gated) + @app.metered + @app.validate + OpenAPI.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from larz import Larz, Response
from larz.models import connect
import larz.money as money, larz.auth as auth, larz.api as api

app = Larz(secret="paid-api-demo", debug=True)
connect("paid_api.db")
money.enable(app, base_url="http://127.0.0.1:8000")
auth.enable(app)
api.enable(app)
app.enable_docs(title="Summarizer API", version="1.0")


@app.get("/")
def home(req):
    # issue a demo key + credit ITS billing subject so you can try it immediately
    from larz.auth import ApiKey, _hash_key
    key = app.auth.issue_api_key(plan="pro", label="demo")
    ak = ApiKey.where(key_hash=_hash_key(key)).first()
    app.money.store.add_credit("apikey:%s" % ak.id, 500)     # $5 prepaid
    return ("<h1>Summarizer API</h1><p>Your demo API key (has $5 credit):</p>"
            "<pre>%s</pre><p>Try it:</p>"
            "<pre>curl -X POST http://127.0.0.1:8000/api/summarize \\\n"
            "  -H 'Authorization: Bearer %s' \\\n"
            "  -H 'Content-Type: application/json' \\\n"
            "  -d '{\"text\":\"the quick brown fox jumps over the lazy dog\"}'</pre>"
            "<p><a href='/docs'>Interactive docs →</a></p>" % (key, key))


@app.api_key_required(plan="pro")
@app.validate({"text": {"type": str, "required": True, "maxlen": 5000},
               "sentences": {"type": int, "min": 1, "max": 5}})
@app.metered("$0.01/call")
@app.post("/api/summarize")
def summarize(req):
    words = req.data["text"].split()
    n = req.data.get("sentences", 1)
    summary = " ".join(words[:12 * n]) + ("…" if len(words) > 12 * n else "")
    # bill the api key itself
    return Response.json({"summary": summary, "words_in": len(words),
                          "charged_cents": 1})


if __name__ == "__main__":
    app.run()
