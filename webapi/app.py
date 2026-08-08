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
from agents_core import options as options_mod
from agents_core import options_intel as intel_mod

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


# --------------------------------------------------------------------------- option chain intelligence


def _valid_underlying(underlying: str) -> str:
    u = underlying.upper()
    if u not in options_mod.NSE_UNDERLYINGS:
        raise HTTPException(status_code=400,
                            detail=f"unsupported underlying {underlying!r}; choose from {sorted(options_mod.NSE_UNDERLYINGS)}")
    return u


def _analyze(underlying: str, expiry: str | None) -> dict:
    try:
        return options_mod.analyze_chain(_valid_underlying(underlying), expiry)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option-chain fetch failed: {exc}") from exc


@app.get("/api/v1/options/analysis")
def options_analysis(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    return _analyze(underlying, expiry)


@app.get("/api/v1/options/expiries")
def options_expiries(underlying: str = "NIFTY") -> list[str]:
    snap = options_mod.OptionChainDataService().fetch(_valid_underlying(underlying), store=False)
    return snap.expiries


@app.get("/api/v1/options/metrics")
def options_metrics(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    return _analyze(underlying, expiry)["analytics"]


@app.get("/api/v1/options/support-resistance")
def options_support_resistance(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    return _analyze(underlying, expiry)["support_resistance"]


@app.get("/api/v1/options/scenarios")
def options_scenarios(underlying: str = "NIFTY", expiry: str | None = None) -> list[dict]:
    return _analyze(underlying, expiry)["scenarios"]


@app.get("/api/v1/options/signal")
def options_signal(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    return _analyze(underlying, expiry)["signal"]


@app.get("/api/v1/options/strategies")
def options_strategies(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    a = _analyze(underlying, expiry)
    return {"strategies": a["strategies"], "suggestions": a["suggestions"]}


@app.get("/api/v1/options/chain")
def options_chain(underlying: str = "NIFTY", expiry: str | None = None,
                  limit: int = 0) -> dict:
    a = _analyze(underlying, expiry)
    contracts = a["contracts"]
    if limit and limit > 0:
        contracts = contracts[: int(limit)]
    return {
        "meta": a["meta"], "analytics": a["analytics"],
        "support_resistance": a["support_resistance"],
        "signal": a["signal"], "contracts": contracts,
    }


@app.get("/api/v1/options/unusual-activity")
def options_unusual_activity(underlying: str = "NIFTY", expiry: str | None = None) -> list[dict]:
    return _analyze(underlying, expiry)["analytics"]["unusual_activity"]


@app.get("/api/v1/options/paper/positions")
def options_paper_positions() -> list[dict]:
    return options_mod.OptionsPaperEngine().positions()


@app.post("/api/v1/options/paper/open")
def options_paper_open(underlying: str = "NIFTY", expiry: str | None = None,
                       strike: float = 0, option_type: str = "CE",
                       action: str = "BUY", quantity: int = 1,
                       entry_price: float = 0.0) -> dict:
    if not expiry or strike <= 0 or entry_price <= 0 or quantity <= 0:
        raise HTTPException(status_code=400, detail="expiry, strike, quantity and entry_price required")
    if option_type.upper() not in ("CE", "PE") or action.upper() not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="option_type CE/PE, action BUY/SELL")
    pos = options_mod.OptionsPaperEngine().open(
        _valid_underlying(underlying), expiry, strike, option_type.upper(),
        action.upper(), quantity, entry_price)
    return {"opened": pos.__dict__, "note": "Paper trade only — no real money."}


@app.get("/api/v1/options/backtest")
def options_backtest(strategy: str = "iron_condor", notional: float = 100000.0,
                     hold_days: int = 30, premium_pct: float = 0.04) -> dict:
    return options_mod.OptionsBacktest().run(strategy, notional, hold_days, premium_pct)


@app.get("/api/v1/options/backtest/history")
def options_backtest_history() -> list[dict]:
    return options_mod.OptionsBacktest().history()


# --------------------------------------------------------------------------- option intelligence layer


@app.get("/api/v1/options/intel")
def options_intel(underlying: str = "NIFTY", expiry: str | None = None, record: bool = True) -> dict:
    try:
        return intel_mod.intelligence_report(_valid_underlying(underlying), expiry, record=record)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option-intel failed: {exc}") from exc


@app.get("/api/v1/options/futures")
def options_intel_futures(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    a = _analyze(underlying, expiry)
    m = a["meta"]
    return intel_mod.FuturesAnalytics(m["underlying"], m["expiry"], m["spot"]).future_with_basis()


@app.get("/api/v1/options/expiry")
def options_intel_expiry(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    a = _analyze(underlying, expiry)
    m = a["meta"]
    nxt = m["expiries"][1] if len(m["expiries"]) > 1 else None
    return intel_mod.ExpiryAnalytics(m["underlying"], m["expiry"], nxt).compare()


@app.get("/api/v1/options/vol")
def options_intel_vol(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    a = _analyze(underlying, expiry)
    return intel_mod.VolStats(_valid_underlying(underlying), a).all()


@app.get("/api/v1/options/velocity")
def options_intel_velocity(underlying: str = "NIFTY", minutes: int = 120) -> dict:
    return intel_mod.SnapshotHistoryStore().velocity(_valid_underlying(underlying), minutes)


@app.get("/api/v1/options/no-trade")
def options_intel_no_trade(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    a = _analyze(underlying, expiry)
    strategy = a["strategies"][0] if a.get("strategies") else None
    liq = intel_mod.LiquidityExecution(a["contracts"], a["meta"]["spot"]).quote_quality()
    return intel_mod.NoTradeEngine().decide(
        a, strategy, liquidity_grade=liq.get("grade", "LOW"), data_ok=True)


@app.get("/api/v1/options/signal-performance")
def options_intel_signal_performance() -> dict:
    return intel_mod.SignalsPerformance().stats()


@app.get("/api/v1/options/events")
def options_intel_events() -> list[dict]:
    return intel_mod.EventCalendar().upcoming()


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
