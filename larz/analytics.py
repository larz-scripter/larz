"""
larz.analytics — a self-hosted revenue dashboard, built from your own payment
data. Zero dependencies; charts are inline SVG.

    import larz.analytics as analytics
    analytics.enable(app, token="secret")      # -> /larz/admin/revenue?token=secret

Shows MRR, ARR, active subscribers, ARPU, estimated LTV & churn, a 30-day revenue
chart, a per-plan breakdown, and recent payments. The same numbers are available
programmatically via app.money.metrics() for your own dashboards.
"""

from .core import Response

__all__ = ["enable"]


def _fmt(cents):
    if cents is None:
        return "—"
    return "$%.2f" % (cents / 100.0)


def _sparkline(series, w=680, h=120, pad=6):
    if not series:
        return ""
    mx = max(series) or 1
    n = len(series)
    step = (w - 2 * pad) / max(1, n - 1)
    pts = []
    for i, v in enumerate(series):
        x = pad + i * step
        y = h - pad - (v / mx) * (h - 2 * pad)
        pts.append((x, y))
    line = " ".join("%.1f,%.1f" % p for p in pts)
    area = "%.1f,%.1f " % (pad, h - pad) + line + " %.1f,%.1f" % (pad + (n - 1) * step, h - pad)
    bars = ""
    return (
        "<svg viewBox='0 0 %d %d' width='100%%' style='max-width:%dpx;height:auto'>"
        "<polygon points='%s' fill='rgba(74,222,128,.12)'/>"
        "<polyline points='%s' fill='none' stroke='#4ade80' stroke-width='2.5' "
        "stroke-linejoin='round' stroke-linecap='round'/>"
        "</svg>" % (w, h, w, area, line))


def enable(app, token=None):
    if not getattr(app, "money", None):
        raise RuntimeError("call money.enable(app, ...) before analytics.enable(app)")
    token = token or getattr(app.money, "admin_token", None)

    @app.get("/larz/admin/revenue", sitemap=False)
    def _revenue(req):
        if token and req.query.get("token") != token:
            return Response("forbidden", status=403)
        if req.query.get("format") == "json":
            return Response.json(app.money.metrics())
        m = app.money.metrics()
        series = app.money.store.revenue_series(30)

        def card(label, value, sub=""):
            return ("<div class='mc'><div class='mc-k'>%s</div>"
                    "<div class='mc-v'>%s</div>%s</div>"
                    % (label, value, "<div class='mc-s'>%s</div>" % sub if sub else ""))

        cards = "".join([
            card("MRR", _fmt(m["mrr_cents"]), "monthly recurring"),
            card("ARR", _fmt(m["arr_cents"]), "annual run-rate"),
            card("Active subscribers", "%d" % m["active_subscribers"]),
            card("ARPU", _fmt(m["arpu_cents"]), "per subscriber / mo"),
            card("Est. LTV", _fmt(m["ltv_cents"]), "ARPU ÷ churn"),
            card("Churn (30d)", "%.1f%%" % (m["churn_rate"] * 100), "%d lost" % m["churned_30d"]),
            card("Revenue (30d)", _fmt(m["revenue_30d_cents"]), "%d payments" % m["new_payments_30d"]),
            card("Total revenue", _fmt(m["total_revenue_cents"]), "%d payments all-time" % m["payments"]),
        ])
        plan_rows = "".join(
            "<tr><td>%s</td><td>%d</td><td>%s</td></tr>"
            % (p.get("name") or "—", p["count"], _fmt(round(p["mrr_cents"])))
            for p in m["per_plan"]) or "<tr><td colspan=3>No active plans yet</td></tr>"
        recent = "".join(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % ((r["subject"] or "")[:16], r["sku"], _fmt(r["cents"]), r["provider"])
            for r in m["recent"]) or "<tr><td colspan=4>No payments yet</td></tr>"
        css = (
            "body{font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;"
            "background:#0b0e14;color:#e6eaf2;margin:0;padding:32px 18px}"
            ".wrap{max-width:860px;margin:0 auto}h1{font-size:26px;margin:0 0 4px}"
            ".sub{color:#9aa5b8;margin:0 0 24px}"
            ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}"
            ".mc{background:#151a26;border:1px solid #232a3a;border-radius:12px;padding:14px 16px}"
            ".mc-k{color:#9aa5b8;font-size:12px;font-weight:600}"
            ".mc-v{font-size:24px;font-weight:800;color:#4ade80;margin-top:2px}"
            ".mc-s{color:#6b7688;font-size:11px;margin-top:2px}"
            ".panel{background:#151a26;border:1px solid #232a3a;border-radius:14px;"
            "padding:18px;margin-top:20px}.panel h2{font-size:15px;margin:0 0 12px}"
            "table{width:100%;border-collapse:collapse;font-size:13.5px}"
            "th,td{text-align:left;padding:7px 10px;border-bottom:1px solid #232a3a}"
            "th{color:#9aa5b8;font-weight:600}")
        return Response(
            "<!doctype html><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Revenue — Larz</title><style>%s</style><div class='wrap'>"
            "<h1>Revenue</h1><p class='sub'>Live metrics from your own payment data.</p>"
            "<div class='grid'>%s</div>"
            "<div class='panel'><h2>Revenue — last 30 days</h2>%s</div>"
            "<div class='panel'><h2>By plan</h2><table>"
            "<tr><th>Plan</th><th>Subscribers</th><th>MRR</th></tr>%s</table></div>"
            "<div class='panel'><h2>Recent payments</h2><table>"
            "<tr><th>Customer</th><th>Item</th><th>Amount</th><th>Provider</th></tr>%s</table></div>"
            "<p class='sub' style='margin-top:20px'>Churn &amp; LTV are estimates from "
            "dunning-recovered losses over the trailing 30 days.</p></div>"
            % (css, cards, _sparkline(series), plan_rows, recent))

    return app
