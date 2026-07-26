"""
larz.contrib.agents — tool-calling AI agents, powered by larzagent.

The framework's ``ai`` module talks to an LLM; this adds a full tool-calling
*agent* loop. With the `ai` extra, ``app.agent(...)`` builds an agent that can
call your Python functions as tools, and ``app.ask(prompt)`` runs a one-shot.

    from larz.contrib import agents
    agents.enable(app, base_url="https://gateway.larzpay.com/v1")

    from larzagent import tool
    @tool
    def lookup_order(order_id: str) -> str:
        \"\"\"Look up an order's status.\"\"\"
        return db.status(order_id)

    app.ask("Where is order 123?", tools=[lookup_order])
"""
from . import require


def _lib():
    require("larzagent", "ai")
    import larzagent
    return larzagent


def enable(app, model="claude-sonnet-5", api_key=None, base_url=None,
           system=None):
    """Attach ``app.agent(...)`` (build a configured Agent) and ``app.ask(...)``
    (one-shot run). LLM config is captured once here."""
    larzagent = _lib()
    cfg = {"model": model, "api_key": api_key}
    if base_url:
        cfg["base_url"] = base_url

    def agent(system=system, tools=None, **kw):
        llm = larzagent.LLM(**cfg)
        return larzagent.Agent(llm, system=system, tools=tools or [], **kw)

    def ask(prompt, system=system, tools=None):
        return agent(system=system, tools=tools).run(prompt)

    app.agent = agent
    app.ask = ask
    return app
