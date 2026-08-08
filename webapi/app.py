"""FastAPI web API over agents_core.

Endpoints:
    GET  /                           chat UI (static HTML)
    GET  /health                     liveness probe
    GET  /api/v1/agents              list of available agents
    POST /api/v1/run                 run an agent on a task -> {agent, response}
    POST /api/v1/run/stream          SSE stream of agent progress + result

Each request gets a fresh agent instance (stateless), so concurrent calls are safe.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agents_core.registry import get_agent
from agents_core.prompts import AGENTS

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Sandeep AI Command Center", version="0.3.0")


class RunRequest(BaseModel):
    agent: str
    task: str = Field(min_length=1)


class RunResponse(BaseModel):
    agent: str
    label: str
    response: str


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/agents")
def agents() -> list[dict[str, str]]:
    return [{"key": key, "label": label} for key, (label, _) in AGENTS.items()]


def _get_agent_or_404(name: str):
    try:
        return get_agent(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/run", response_model=RunResponse)
def run(req: RunRequest) -> RunResponse:
    agent = _get_agent_or_404(req.agent)
    label = getattr(agent, "label", req.agent)
    try:
        response = agent.run(req.task)
    finally:
        agent.close()
    return RunResponse(agent=req.agent, label=label, response=response)


@app.post("/api/v1/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    label = AGENTS[req.agent][0] if req.agent in AGENTS else req.agent
    _get_agent_or_404(req.agent)  # validate before streaming starts

    def gen():
        agent = get_agent(req.agent)
        try:
            yield f"data: {json.dumps({'type': 'meta', 'agent': req.agent, 'label': label})}\n\n"
            for event in agent.run_stream(req.task):
                yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            agent.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
