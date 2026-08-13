"""FastAPI web API over agents_core.

Endpoints:
    GET  /                           chat UI (static HTML)
    GET  /health                     liveness probe
    GET  /api/v1/agents              list of available agents
    POST /api/v1/run                 run an agent on a task -> {agent, response}
    POST /api/v1/run/stream          SSE stream of agent progress + result
    GET  /api/v1/market/*            market intelligence (indices, quotes, analysis, risk)

Each request gets a fresh agent instance (stateless), so concurrent calls are safe.

Auth (defense in depth): if the environment variable AGENT_API_TOKEN is set,
every /api/* route requires it (via `Authorization: Bearer <token>` or the
`X-API-Key` header). If it is unset the API stays open (single-user, localhost
default). /, /health and /static are never gated.
"""
from __future__ import annotations

import hmac
import json
import os
import re as _re
import time
from contextlib import asynccontextmanager, nullcontext as _nullcontext
from functools import wraps
from pathlib import Path
from threading import Lock

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from agents_core.registry import get_agent
from agents_core.prompts import AGENTS
from agents_core import market
from agents_core import options as options_mod
from agents_core import options_intel as intel_mod

STATIC_DIR = Path(__file__).resolve().parent / "static"

@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Resume the auto-trading loop after a process restart if it was RUNNING,
    # so the state file never claims RUNNING while no loop thread is alive.
    try:
        from agents_core import trading

        if trading.status().get("running"):
            trading.start()
    except Exception:  # noqa: BLE001 — never block app startup
        pass
    yield


app = FastAPI(title="Sandeep AI Command Center", version="0.5.0",
              lifespan=_lifespan)


def _api_token() -> str:
    return (os.environ.get("AGENT_API_TOKEN") or "").strip()


def _supplied_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[len("bearer "):].strip()
    return request.headers.get("X-API-Key", "").strip()


@app.middleware("http")
async def api_auth_middleware(request: Request, call_next):
    token = _api_token()
    if token and request.url.path.startswith("/api/"):
        provided = _supplied_key(request)
        if not provided or not hmac.compare_digest(provided, token):
            return JSONResponse(status_code=401, content={"detail": "missing or invalid API key"})
    return await call_next(request)


_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=(), payment=()",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


class _RateLimiter:
    """Fixed 60s window, per-client. Settable via AGENT_RATE_LIMIT_RPM (0 disables)."""

    def __init__(self, rpm: int) -> None:
        self.rpm = max(rpm, 1)
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = Lock()

    def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start >= 60.0:
                start, count = now, 0
            count += 1
            self._windows[key] = (start, count)
            if len(self._windows) > 10_000:
                self._windows = {k: v for k, v in self._windows.items() if now - v[0] < 120.0}
            if count > self.rpm:
                return False, int(60.0 - (now - start)) + 1
            return True, 0


_RATE_LIMIT_RPM = int(os.environ.get("AGENT_RATE_LIMIT_RPM", "600") or 600)
_RATE_LIMITER = _RateLimiter(_RATE_LIMIT_RPM)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if _RATE_LIMIT_RPM > 0 and request.url.path.startswith("/api/"):
        client = request.client.host if request.client else "unknown"
        allowed, retry_after = _RATE_LIMITER.allow(client)
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "rate limit exceeded, slow down"},
                headers={"Retry-After": str(retry_after)},
            )
    return await call_next(request)


# --------------------------------------------------------------------------- conversations
# Optional multi-turn sessions: pass `session_id` in /api/v1/run (and /run/stream)
# to keep one agent instance alive across calls so history/memory persist.
# Sessions are in-memory and per-process (single uvicorn worker), matching the
# rate-limiter/TTL caches/trading loop. Without session_id, behavior is the same
# stateless fresh-agent-per-request as before.


class _Session:
    __slots__ = ("agent", "created", "last_used", "lock")

    def __init__(self, agent):
        self.agent = agent
        self.created = time.time()
        self.last_used = time.time()
        self.lock = Lock()


_SESSION_TTL = float(os.environ.get("AGENT_SESSION_TTL_MIN", "30") or 30) * 60.0
_session_lock = Lock()
_sessions: dict[str, _Session] = {}


def _evict_sessions():
    if not _sessions:
        return
    now = time.time()
    stale = [k for k, s in _sessions.items() if now - s.last_used > _SESSION_TTL]
    for k in stale:
        sess = _sessions.pop(k, None)
        if sess is not None:
            try:
                sess.agent.close()
            except Exception:
                pass


def _resolve_session(session_id: str, agent_name: str):
    """Return the (possibly new) agent for a session. Holds no lock on return."""
    with _session_lock:
        _evict_sessions()
        sess = _sessions.get(session_id)
        if sess is None or sess.agent.name != agent_name:
            if sess is not None:
                _sessions.pop(session_id, None)
                try:
                    sess.agent.close()
                except Exception:
                    pass
            sess = _Session(get_agent(agent_name))
            _sessions[session_id] = sess
        else:
            sess.last_used = time.time()
        return sess


def _history_turns(agent) -> int:
    return sum(1 for e in getattr(agent, "history", []) if e.get("role") == "user")


def _ttl_cache(ttl: float):
    """Small in-memory TTL cache for CPU-heavy read endpoints (signal/screener)."""
    store: dict[str, tuple[float, object]] = {}
    lock = Lock()

    def deco(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            key = json.dumps([args, kwargs], default=str)
            now = time.time()
            with lock:
                hit = store.get(key)
                if hit is not None and now - hit[0] < ttl:
                    return hit[1]
            result = fn(*args, **kwargs)
            with lock:
                store[key] = (now, result)
            return result

        return wrapper

    return deco


class RunRequest(BaseModel):
    agent: str
    task: str = Field(min_length=1)
    session_id: str | None = Field(default=None, max_length=128)

    @field_validator("task")
    @classmethod
    def _task_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("task must not be blank")
        return v

    @field_validator("session_id")
    @classmethod
    def _session_id_safe(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not _re.fullmatch(r"[A-Za-z0-9_.\-]+", v):
            raise ValueError("session_id may only contain letters, digits, '_', '.', '-'")
        return v


class RunResponse(BaseModel):
    agent: str
    label: str
    response: str


def _llm_http_error(text: str) -> HTTPException:
    """Map an agent error string to a meaningful HTTP status instead of 200."""
    lowered = text.lower()
    if " 429 " in f" {lowered} " or lowered.startswith("error: openai error 429"):
        return HTTPException(status_code=429, detail=text)
    if "413" in lowered:
        return HTTPException(status_code=413, detail=text)
    return HTTPException(status_code=502, detail=text)


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
    _get_agent_or_404(req.agent)
    sess = _resolve_session(req.session_id, req.agent) if req.session_id else None
    agent = sess.agent if sess else get_agent(req.agent)
    label = getattr(agent, "label", req.agent)
    events: list[dict] = []
    try:
        with sess.lock if sess else _nullcontext():
            events = list(agent.run_stream(req.task))
    finally:
        if sess is None:
            agent.close()
    for event in events:
        if event["type"] == "error":
            raise _llm_http_error(event["text"])
    response = next((e["text"] for e in reversed(events) if e["type"] == "result"), "(no response)")
    return RunResponse(agent=req.agent, label=label, response=response)


@app.post("/api/v1/run/stream")
def run_stream(req: RunRequest) -> StreamingResponse:
    label = AGENTS[req.agent][0] if req.agent in AGENTS else req.agent
    _get_agent_or_404(req.agent)  # validate before streaming starts

    def gen():
        sess = _resolve_session(req.session_id, req.agent) if req.session_id else None
        agent = sess.agent if sess else get_agent(req.agent)
        try:
            yield f"data: {json.dumps({'type': 'meta', 'agent': req.agent, 'label': label, 'session_id': req.session_id})}\n\n"
            with sess.lock if sess else _nullcontext():
                for event in agent.run_stream(req.task):
                    yield f"data: {json.dumps(event, default=str)}\n\n"
        finally:
            if sess is None:
                agent.close()

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/v1/conversations")
def conversations() -> list[dict]:
    with _session_lock:
        _evict_sessions()
        return [
            {
                "session_id": sid,
                "agent": s.agent.name,
                "label": getattr(s.agent, "label", s.agent.name),
                "turns": _history_turns(s.agent),
                "created": s.created,
                "last_used": s.last_used,
            }
            for sid, s in sorted(_sessions.items(), key=lambda kv: kv[1].last_used, reverse=True)
        ]


@app.get("/api/v1/conversations/{session_id}")
def conversation_detail(session_id: str) -> dict:
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]+", session_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    with _session_lock:
        _evict_sessions()
        sess = _sessions.get(session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        history = [
            {"role": e.get("role"), "content": e.get("content", "")}
            for e in getattr(sess.agent, "history", [])
            if e.get("role") in ("user", "assistant")
        ]
        return {
            "session_id": session_id,
            "agent": sess.agent.name,
            "label": getattr(sess.agent, "label", sess.agent.name),
            "created": sess.created,
            "last_used": sess.last_used,
            "turns": _history_turns(sess.agent),
            "history": history,
        }


@app.delete("/api/v1/conversations/{session_id}")
def conversation_delete(session_id: str) -> dict:
    if not _re.fullmatch(r"[A-Za-z0-9_.\-]+", session_id):
        raise HTTPException(status_code=404, detail="conversation not found")
    with _session_lock:
        _evict_sessions()
        sess = _sessions.pop(session_id, None)
    if sess is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    try:
        sess.agent.close()
    except Exception:
        pass
    return {"status": "closed", "session_id": session_id}


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


@app.get("/api/v1/market/orderbook/{symbol}")
def market_orderbook(symbol: str) -> dict:
    s = market.get_provider().orderbook(symbol)
    if s is None:
        raise HTTPException(status_code=404, detail=f"unknown symbol: {symbol}")
    return s


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
@_ttl_cache(ttl=10.0)
def market_signal(symbol: str) -> dict:
    _stock_or_404(symbol)
    return market.signal_engine(symbol)


@app.get("/api/v1/market/screener")
@_ttl_cache(ttl=10.0)
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
    u = _valid_underlying(underlying)
    try:
        return options_mod.analyze_chain(u, expiry)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option-chain fetch failed: {exc}") from exc


@app.get("/api/v1/options/analysis")
def options_analysis(underlying: str = "NIFTY", expiry: str | None = None) -> dict:
    return _analyze(underlying, expiry)


@app.get("/api/v1/options/expiries")
def options_expiries(underlying: str = "NIFTY") -> list[str]:
    try:
        snap = options_mod.OptionChainDataService().fetch(_valid_underlying(underlying), store=False)
        return snap.expiries
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"option-chain fetch failed: {exc}") from exc


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


# --------------------------------------------------------------------------- compliance + rag enrichment


@app.get("/api/v1/compliance/status")
def compliance_status() -> dict:
    from agents_core import compliance as comp

    return comp.compliance_posture()


@app.get("/api/v1/rag/drift")
def rag_drift() -> dict:
    from agents_core import rag as rag_mod

    return rag_mod.rag_drift()


@app.get("/api/v1/rag/graph")
def rag_graph() -> dict:
    from agents_core import rag as rag_mod

    return rag_mod.rag_graph()


# --------------------------------------------------------------------------- mode A order approvals


class DraftRequest(BaseModel):
    symbol: str
    quantity: int
    transaction_type: str
    order_type: str = "MARKET"
    product: str = "I"
    price: float = 0.0
    trigger_price: float = 0.0
    kind: str = "paper"


@app.get("/api/v1/approvals")
def approvals_list(pending_only: bool = True) -> dict:
    from agents_core import approval as appr

    return {"status": "ok", "approvals": appr.get_flow().list_pending() if pending_only else appr.get_flow().list_all()}


@app.get("/api/v1/approvals/status")
def approvals_status() -> dict:
    from agents_core import approval as appr

    return {"status": "ok", **appr.approvals_status()}


@app.post("/api/v1/approvals/submit")
def approvals_submit(req: DraftRequest) -> dict:
    from agents_core import approval as appr

    try:
        record = appr.submit_draft({
            "symbol": req.symbol.upper(),
            "quantity": req.quantity,
            "transaction_type": req.transaction_type,
            "order_type": req.order_type,
            "product": req.product,
            "price": req.price,
            "trigger_price": req.trigger_price,
            "kind": req.kind,
        })
    except appr.ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "PENDING", "approval_id": record["id"], "draft": record["draft"]}


@app.post("/api/v1/approvals/{approval_id}/approve")
def approvals_approve(approval_id: str) -> dict:
    from agents_core import approval as appr

    try:
        record = appr.approve(approval_id)
    except appr.ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": record["status"], "approval_id": approval_id, "result": record.get("result")}


@app.post("/api/v1/approvals/{approval_id}/reject")
def approvals_reject(approval_id: str, reason: str = "") -> dict:
    from agents_core import approval as appr

    try:
        record = appr.reject(approval_id, reason)
    except appr.ApprovalError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": record["status"], "approval_id": approval_id}


# --------------------------------------------------------------------------- automatic trading (start/stop)


class TradingControl(BaseModel):
    interval: float | None = None
    agent: str | None = None
    task: str | None = None


@app.get("/api/v1/trading/status")
def trading_status() -> dict:
    from agents_core import trading

    return {"status": "ok", "trading": trading.status()}


@app.post("/api/v1/trading/start")
def trading_start(req: TradingControl | None = None) -> dict:
    from agents_core import trading
    from agents_core.safety import ExecutionBlockedError

    req = req or TradingControl()
    try:
        return {"status": "ok", "trading": trading.start(
            interval=req.interval, agent=req.agent, task=req.task)}
    except ExecutionBlockedError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@app.post("/api/v1/trading/stop")
def trading_stop() -> dict:
    from agents_core import trading

    return {"status": "ok", "trading": trading.stop(reason="ui")}


@app.get("/api/v1/trading/performance/today")
def trading_performance_today() -> dict:
    from agents_core import performance

    return {"status": "ok", "performance": performance.today_performance()}


@app.get("/api/v1/trading/executions")
def trading_executions(date: str | None = None) -> dict:
    from agents_core import performance

    return {"status": "ok", **performance.executions_by_date(date)}
