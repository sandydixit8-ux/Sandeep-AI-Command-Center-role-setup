"""FastAPI web API over agents_core.

Endpoints:
    GET  /health                    liveness probe
    GET  /api/v1/agents             list of available agents
    POST /api/v1/run                run an agent on a task -> {agent, response}
    POST /api/v1/reset              reset an agent's conversation memory

Each request gets a fresh agent instance (stateless), so concurrent calls are safe.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from agents_core.registry import get_agent, list_agents
from agents_core.prompts import AGENTS

app = FastAPI(title="Sandeep AI Command Center", version="0.2.0")


class RunRequest(BaseModel):
    agent: str
    task: str = Field(min_length=1)
    reset: bool = False


class RunResponse(BaseModel):
    agent: str
    label: str
    response: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/agents")
def agents() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, (label, _) in AGENTS.items()]


@app.post("/api/v1/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    try:
        agent = get_agent(req.agent)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    label = getattr(agent, "label", req.agent)
    try:
        response = agent.run(req.task)
    finally:
        agent.close()
    return RunResponse(agent=req.agent, label=label, response=response)
