"""Agent factory + registry."""
from __future__ import annotations

from .agent import Agent
from .prompts import AGENTS
from .tools import FINANCE_TOOLS


def get_agent(name: str) -> Agent:
    """Return a configured Agent. `name` is the agent key ('exec', 'finance', ...)."""
    if name not in AGENTS:
        raise KeyError(f"unknown agent {name!r}. Available: {', '.join(AGENTS)}")
    label, system_prompt = AGENTS[name]
    tools = FINANCE_TOOLS if name == "finance" else None
    agent = Agent(name=name, system_prompt=system_prompt, tools=tools)
    agent.label = label  # type: ignore[attr-defined]
    return agent


def list_agents() -> str:
    return "\n".join(f"  {key:<10} {label}" for key, (label, _) in AGENTS.items())
