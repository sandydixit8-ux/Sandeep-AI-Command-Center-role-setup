"""Agent factory + registry."""
from __future__ import annotations

from .agent import Agent
from .prompts import AGENTS
from .tools import (
    APPROVAL_TOOLS,
    BROKER_TOOLS,
    COMPLIANCE_TOOLS,
    FINANCE_TOOLS,
    GMAIL_TOOLS,
    JOBSEARCH_TOOLS,
    MARKET_TOOLS,
    OPTION_TOOLS,
    RAG_TOOLS,
    RISK_TOOLS,
    build_tools,
)

_EXTRA_TOOLS = {
    "commander": FINANCE_TOOLS + JOBSEARCH_TOOLS + GMAIL_TOOLS + MARKET_TOOLS + OPTION_TOOLS + BROKER_TOOLS + RAG_TOOLS + COMPLIANCE_TOOLS + APPROVAL_TOOLS,
    "finance": FINANCE_TOOLS,
    "jobsearch": JOBSEARCH_TOOLS,
    "exec": GMAIL_TOOLS,
    "bd": GMAIL_TOOLS,
    "market": MARKET_TOOLS + OPTION_TOOLS + RAG_TOOLS + APPROVAL_TOOLS,
    "risk": RISK_TOOLS + BROKER_TOOLS + RAG_TOOLS + COMPLIANCE_TOOLS + APPROVAL_TOOLS,
}


def get_agent(name: str) -> Agent:
    """Return a configured Agent. `name` is the agent key ('commander', 'finance', ...)."""
    if name not in AGENTS:
        raise KeyError(f"unknown agent {name!r}. Available: {', '.join(AGENTS)}")
    label, system_prompt = AGENTS[name]
    tools = build_tools(name, _EXTRA_TOOLS.get(name))
    agent = Agent(name=name, system_prompt=system_prompt, tools=tools)
    agent.label = label  # type: ignore[attr-defined]
    return agent


def list_agents() -> str:
    return "\n".join(f"  {key:<10} {label}" for key, (label, _) in AGENTS.items())
