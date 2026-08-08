"""FastAPI web API over agents_core.

Endpoints:
    GET  /                           chat UI (static HTML)
    GET  /health                     liveness probe
    GET  /api/v1/agents              list of available agents
    POST /api/v1/run                 run an agent on a task -> {agent, response}
    POST /api/v1/run/stream          SSE stream of agent progress + result
    GET  /api/v1/market/*            market intelligence (indices, quotes, analysis, risk)

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
from agents_core import market

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Sandeep AI Command Center", version="0.5.0")


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


# --------------------------------------------------------------------------- market intelligence


def _stock_or_404(symbol: str) -> dict:
    s = market.get_provider().get_stock(symbol)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")
    return s


@app.get("/api/v1/market/status")
def market_status() -> dict:
    return market.get_provider().status()


@app.get("/api/v1/market/indices")
def market_indices() -> list[dict]:
    return market.get_provider().get_indices()


@app.get("/api/v1/market/stocks")
def market_stocks() -> list[dict]:
    return market.get_provider().list_stocks()


@app.get("/api/v1/market/brief")
def market_brief() -> dict:
    return market.market_brief()


@app.get("/api/v1/market/regime")
def market_regime() -> dict:
    return market.regime_engine()


@app.get("/api/v1/market/news")
def market_news(symbol: str | None = None) -> list[dict]:
    return market.news_sentiment(symbol)


@app.get("/api/v1/market/quote/{symbol}")
def market_quote(symbol: str) -> dict:
    return _stock_or_404(symbol)


@app.get("/api/v1/market/ohlc/{symbol}")
def market_ohlc(symbol: str, days: int = 200) -> list[dict]:
    _stock_or_404(symbol)
    return market.get_provider().ohlc(symbol, max(2, min(days, 750)))


@app.get("/api/v1/market/technical/{symbol}")
def market_technical(symbol: str) -> dict:
    _stock_or_404(symbol)
    return market.technical_view(symbol)


@app.get("/api/v1/market/fundamental/{symbol}")
def market_fundamental(symbol: str) -> dict:
    _stock_or_404(symbol)
    return market.fundamental_view(symbol)


@app.get("/api/v1/market/score/{symbol}")
def market_score(symbol: str) -> dict:
    _stock_or_404(symbol)
    return market.market_score(symbol)


@app.get("/api/v1/market/signal/{symbol}")
def market_signal(symbol: str) -> dict:
    _stock_or_404(symbol)
    return market.signal_engine(symbol)


@app.get("/api/v1/market/screener")
def market_screener(min_score: float | None = None, sector: str | None = None,
                    max_pe: float | None = None, min_momentum: float | None = None,
                    min_market_cap: float | None = None) -> list[dict]:
    filters = {}
    if min_score is not None:
        filters["min_score"] = min_score
    if sector:
        filters["sector"] = sector
    if max_pe is not None:
        filters["max_pe"] = max_pe
    if min_momentum is not None:
        filters["min_momentum"] = min_momentum
    if min_market_cap is not None:
        filters["min_market_cap"] = min_market_cap
    return market.screener(filters)


@app.get("/api/v1/market/position-size/{symbol}")
def market_position_size(symbol: str, capital: float, risk_per_trade_pct: float = 2.0) -> dict:
    _stock_or_404(symbol)
    return market.position_size(symbol, capital, risk_per_trade_pct)


@app.get("/api/v1/market/backtest/{symbol}")
def market_backtest(symbol: str, entry_rule: str = "EMA+RSI", exit_rule: str = "stop/target",
                    stop_loss_pct: float = 8.0, target_pct: float | None = None, days: int = 500) -> dict:
    _stock_or_404(symbol)
    return market.backtest(symbol, entry_rule, exit_rule, stop_loss_pct, target_pct, days)


@app.get("/api/v1/market/portfolio-risk")
def market_portfolio_risk() -> dict:
    paper = market.paper_portfolio()
    positions = [{"symbol": p["symbol"], "value": p.get("value", 0), "quantity": p["quantity"]} for p in paper["positions"]]
    return market.portfolio_risk(positions, paper["capital"])


# --------------------------------------------------------------------------- paper trading


@app.get("/api/v1/paper-trading/portfolio")
def paper_portfolio() -> dict:
    return market.paper_portfolio()


@app.post("/api/v1/paper-trading/buy")
def paper_buy(symbol: str, quantity: int) -> dict:
    _stock_or_404(symbol)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    try:
        return market.paper_buy(symbol, quantity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/paper-trading/sell")
def paper_sell(symbol: str, quantity: int) -> dict:
    _stock_or_404(symbol)
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="quantity must be positive")
    try:
        return market.paper_sell(symbol, quantity)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
