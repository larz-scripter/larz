"""
larz.ai — monetize AI endpoints. The other half of Larz's identity: build a
product AND charge for the tokens it burns. Zero dependencies.

    import larz.ai as ai
    ai.enable(app)                                   # requires money.enable(app, ...)
    app.ai.price("gpt-4o", input="$2.50/1M", output="$10/1M")

    @app.ai_metered("gpt-4o", per_minute=20)         # rate-limit + require credit
    @app.post("/api/chat")
    def chat(req):
        reply, usage = call_openai(req.data["prompt"])       # your LLM call
        app.ai.charge(req, "gpt-4o", usage["in"], usage["out"])   # bill the tokens
        return {"reply": reply}

Features:
  * per-model token pricing with **sub-cent accuracy** (µ$ accumulator)
  * a rate-limited, credit-checked route guard (@app.ai_metered)
  * **BYOK** — customers store their own provider key (encrypted at rest); when
    present, Larz skips metering (they pay the provider directly)
  * usage reporting per customer
  * an optional metered proxy to any OpenAI-compatible endpoint (opt-in)
"""

import time
import json
import urllib.request

from .core import Response
from .models import Model, StrField, IntField, connect  # noqa
from .crypto import Cipher

__all__ = ["enable", "parse_token_price"]

_MICROS_PER_CENT = 10_000          # 1 cent = $0.01 = 10,000 micro-dollars
_MICROS_PER_USD = 1_000_000


def parse_token_price(price):
    """'$2.50/1M' -> micro-dollars per token. Supports /1M, /1k, /1000, /token."""
    s = str(price).strip().lstrip("$")
    amount, _, unit = s.partition("/")
    usd = float(amount)
    unit = (unit or "1M").lower()
    per = {"1m": 1_000_000, "1k": 1_000, "1000": 1_000, "token": 1, "": 1_000_000}.get(unit, 1_000_000)
    return usd * _MICROS_PER_USD / per      # micro-$ per single token


class AIUsage(Model):
    subject = StrField(index=True)
    model = StrField(default="")
    in_tokens = IntField(default=0)
    out_tokens = IntField(default=0)
    cost_micros = IntField(default=0)
    ts = IntField(default=0)


class AIResidual(Model):
    subject = StrField(unique=True, index=True)
    micros = IntField(default=0)           # sub-cent carry not yet debited


class AIKey(Model):
    subject = StrField(unique=True, index=True)
    provider = StrField(default="openai")
    enc = StrField(default="")             # BYOK key, encrypted at rest


class _AI:
    def __init__(self, app, cipher):
        self.app = app
        self.cipher = cipher
        self.prices = {}                   # model -> (in_micros_per_tok, out_micros_per_tok)
        self._hits = {}                    # (subject) -> [timestamps]  (rate limit)
        AIUsage.create_table(); AIResidual.create_table(); AIKey.create_table()

    # -- pricing ----------------------------------------------------------- #
    def price(self, model, input, output=None):
        self.prices[model] = (parse_token_price(input),
                              parse_token_price(output if output is not None else input))
        return self

    def cost_micros(self, model, in_tokens, out_tokens=0):
        pin, pout = self.prices.get(model, (0.0, 0.0))
        return int(round(in_tokens * pin + out_tokens * pout))

    # -- billing ----------------------------------------------------------- #
    def charge(self, req, model, in_tokens, out_tokens=0):
        """Bill a completed AI call to the caller's credit. Sub-cent costs
        accumulate in a µ$ residual and are debited as whole cents accrue.
        Returns the cost in micro-dollars. BYOK callers are not metered."""
        subject = req.subject
        if self.has_byok(subject):
            return 0
        micros = self.cost_micros(model, in_tokens, out_tokens)
        row = AIResidual.where(subject=subject).first() or AIResidual(subject=subject, micros=0)
        row.micros += micros
        cents = row.micros // _MICROS_PER_CENT
        if cents > 0:
            self.app.money.store.add_credit(subject, -cents)     # debit
            row.micros -= cents * _MICROS_PER_CENT
        row.save()
        AIUsage(subject=subject, model=model, in_tokens=in_tokens,
                out_tokens=out_tokens, cost_micros=micros, ts=int(time.time())).save()
        return micros

    def balance_micros(self, subject):
        """Spendable balance in µ$ (credit cents minus un-debited residual)."""
        cents = self.app.money.store.balance(subject)
        row = AIResidual.where(subject=subject).first()
        return cents * _MICROS_PER_CENT - (row.micros if row else 0)

    def usage(self, subject):
        rows = AIUsage.where(subject=subject).all()
        return {"calls": len(rows),
                "in_tokens": sum(r.in_tokens for r in rows),
                "out_tokens": sum(r.out_tokens for r in rows),
                "spent_micros": sum(r.cost_micros for r in rows),
                "spent_cents": round(sum(r.cost_micros for r in rows) / _MICROS_PER_CENT)}

    # -- BYOK -------------------------------------------------------------- #
    def set_byok(self, subject, key, provider="openai"):
        row = AIKey.where(subject=subject).first() or AIKey(subject=subject)
        row.provider = provider
        row.enc = self.cipher.encrypt(key)
        row.save()

    def get_byok(self, subject):
        row = AIKey.where(subject=subject).first()
        return self.cipher.decrypt(row.enc) if row and row.enc else None

    def has_byok(self, subject):
        row = AIKey.where(subject=subject).first()
        return bool(row and row.enc)

    def clear_byok(self, subject):
        row = AIKey.where(subject=subject).first()
        if row:
            row.delete()

    # -- rate limiting ----------------------------------------------------- #
    def _rate_ok(self, subject, per_minute):
        if not per_minute:
            return True
        now = time.time()
        hits = [t for t in self._hits.get(subject, []) if now - t < 60]
        if len(hits) >= per_minute:
            self._hits[subject] = hits
            return False
        hits.append(now)
        self._hits[subject] = hits
        return True

    # -- opt-in metered proxy (OpenAI-compatible chat completions) --------- #
    def proxy(self, req, model, messages, base_url="https://api.openai.com/v1",
              api_key=None, **params):
        """Call an OpenAI-compatible /chat/completions endpoint and auto-charge the
        caller for the tokens used. Uses the caller's BYOK key if set, else
        `api_key` (your platform key, metered). Returns the provider JSON.

        Opt-in: this performs a live network request. Metering-only apps should
        call the LLM themselves and use charge() instead.
        """
        key = self.get_byok(req.subject) or api_key
        if not key:
            return {"error": "no api key"}
        body = json.dumps(dict(model=model, messages=messages, **params)).encode()
        r = urllib.request.Request(base_url.rstrip("/") + "/chat/completions",
                                   data=body, headers={"Content-Type": "application/json",
                                                       "Authorization": "Bearer " + key})
        with urllib.request.urlopen(r, timeout=60) as resp:
            out = json.loads(resp.read().decode())
        u = out.get("usage", {})
        self.charge(req, model, u.get("prompt_tokens", 0), u.get("completion_tokens", 0))
        return out


def enable(app, secret=None):
    if not getattr(app, "money", None):
        raise RuntimeError("call money.enable(app, ...) before ai.enable(app)")
    cipher = Cipher(secret or getattr(app.sessions, "secret", b"larz-ai"))
    mgr = _AI(app, cipher)
    app.ai = mgr

    def ai_metered(model=None, per_minute=None, min_balance="$0.01"):
        from .money import parse_price
        min_cents = parse_price(min_balance)[0]

        def deco(fn):
            def guard(req):
                subject = req.subject
                if mgr.has_byok(subject):
                    return None                       # they pay their provider
                # unlimited-AI plans skip the credit check
                if not (app.money and app.money.feature(req, "ai_unlimited")):
                    if app.money.store.balance(subject) < min_cents:
                        return Response.json(
                            {"error": "payment_required",
                             "message": "Add credit to use this AI endpoint.",
                             "top_up": app.money.base_url + "/larz/credits"}, status=402)
                if not mgr._rate_ok(subject, per_minute):
                    return Response.json(
                        {"error": "rate_limited",
                         "message": "Too many requests — slow down."}, status=429)
                return None
            guards = getattr(fn, "_larz_guards", [])
            guards.append(guard)
            fn._larz_guards = guards
            return fn
        return deco

    app.ai_metered = ai_metered
    return mgr
