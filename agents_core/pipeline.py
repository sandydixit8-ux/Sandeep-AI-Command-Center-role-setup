"""Deterministic fast-path execution pipeline (no LLM).

Implements the architecture's CONTEXT -> SIGNAL -> RISK -> COMPLIANCE ->
EXECUTE sequence for the auto-trading loop on clear cases, so the loop does not
need to spend tokens or wait on a model when the setup is obvious. It is
opt-in via ``AUTO_TRADE_DETERMINISTIC=true``; otherwise the loop keeps running
the LLM agent cycle.

Safety properties (the pipeline never bypasses a guardrail):
- Every action still passes ``safety_gate`` (kill switch), the daily-loss
  circuit breaker, the compliance pre-trade engine and the execution audit —
  exactly the same gates the chat/agent path uses.
- Cheap NO TRADE: symbols with no clear BUY candidate short-circuit before any
  risk sizing or compliance work.
- Fail-open: any unexpected per-symbol error is captured and reported; it can
  never take the loop down.
- Deterministic: same inputs produce the same decisions, each recorded in the
  hash-chained ``pipeline_audit.jsonl``.

Configuration (env):
- ``AUTO_TRADE_DETERMINISTIC``  'true'/'1'/'yes' enables the fast-path
- ``PIPELINE_SYMBOLS``          comma-separated symbols (default RELIANCE,TCS,INFY,SBIN)
- ``PIPELINE_RISK_PCT``         default risk per trade (default 2.0, regime-scaled)
- ``PIPELINE_CAPITAL``          optional capital override (default paper capital)
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .safety import ExecutionBlockedError

DEFAULT_SYMBOLS = ["RELIANCE", "TCS", "INFY", "SBIN"]
BUY_SIGNAL = "BUY CANDIDATE"
SELL_SIGNAL = "SELL / REDUCE RISK"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def enabled() -> bool:
    return _env("AUTO_TRADE_DETERMINISTIC", "").lower() in ("1", "true", "yes", "on")


def default_symbols() -> list[str]:
    raw = _env("PIPELINE_SYMBOLS")
    syms = [s.strip().upper() for s in raw.split(",") if s.strip()] if raw else DEFAULT_SYMBOLS
    return syms or DEFAULT_SYMBOLS


def _audit(record: dict[str, Any]) -> None:
    f = DATA_DIR / "pipeline_audit.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        row = {"ts": datetime.now().isoformat(timespec="seconds"), **record,
               "prev_hash": hashlib.sha256(prev.encode("utf-8")).hexdigest()}
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 — audit must never break the caller
        pass


def _mem() -> Any:
    from . import memory as memory_mod

    return memory_mod


def _compliance_engine() -> Any:
    """SEBI pre-trade engine built from the same broker config as the adapter."""
    from . import upstox as u
    from .compliance import ComplianceEngine

    cfg = u.get_broker_settings()
    limiter = u.RateLimiter(cfg.max_orders_per_sec)
    return ComplianceEngine(
        mode=cfg.mode,
        api_key=cfg.api_key,
        algo_id=cfg.algo_id,
        is_live=cfg.is_live,
        limiter=limiter.acquire,
    )


def _regime_risk_scale() -> float:
    """Scale risk per trade down in Bear / high-volatility regimes (risk policy)."""
    from . import market

    try:
        regime = market.regime_engine().get("regime", "")
    except Exception:  # noqa: BLE001
        return 1.0
    if "Bear" in regime:
        return 0.5
    if "High Volatility" in regime:
        return 0.75
    return 1.0


def _held_quantity(symbol: str) -> int:
    from . import market

    try:
        return sum(p.get("quantity", 0) for p in market.paper_portfolio()["positions"]
                   if p.get("symbol") == symbol)
    except Exception:  # noqa: BLE001
        return 0


def _risk_allows(symbol: str, notional: float, capital: float) -> tuple[bool, str]:
    """Portfolio risk gate: block hard-limit breaches, record sector warnings."""
    from . import market

    try:
        positions = market.paper_portfolio()["positions"]
        pos = [dict(p, value=p.get("value", p.get("price", 0) * p.get("quantity", 0)))
               for p in positions]
        pos.append({"symbol": symbol, "quantity": 0, "value": notional})
        risk = market.portfolio_risk(pos, capital)
    except Exception as exc:  # noqa: BLE001
        return False, f"risk check failed: {exc}"
    if risk["exposure_pct"] > 90:
        return False, f"exposure {risk['exposure_pct']}% would exceed 90% limit"
    if risk["max_position_pct"] > 50:
        return False, f"single-position concentration {risk['max_position_pct']}% exceeds 50% limit"
    return True, f"exposure {risk['exposure_pct']}%, top sector {risk['top_sector']} {risk['top_sector_pct']}%"


def run_symbol(symbol: str, capital: float, risk_pct: float) -> dict[str, Any]:
    """Run the full deterministic pipeline for one symbol.

    Returns a dict with ``action`` in {None, 'BUY', 'SELL'}, ``reason`` and the
    executed trade (if any). Never raises.
    """
    from . import market

    result: dict[str, Any] = {"symbol": symbol, "signal": "", "confidence": 0,
                              "action": None, "reason": "", "steps": [], "executed": None}
    try:
        sig = market.signal_engine(symbol)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"signal engine error: {exc}"
        return result
    result["signal"] = sig.get("signal", "")
    result["confidence"] = sig.get("confidence", 0)

    # ---- SELL / REDUCE RISK: close the held position (risk-reducing) ----
    if result["signal"] == SELL_SIGNAL:
        held = _held_quantity(symbol)
        if not held:
            result["reason"] = "no position to reduce"
            return result
        result["steps"].append("signal: SELL / REDUCE RISK — reduce risk by closing held position")
        try:
            _compliance_engine().pre_trade(f"PIPELINE paper sell {symbol} x{held}",
                                           gate_daily_loss=False)
        except ExecutionBlockedError as exc:
            result["reason"] = f"compliance blocked: {exc}"
            return result
        try:
            r = market.paper_sell(symbol, held)
            result["action"] = "SELL"
            result["executed"] = {"side": "SELL", "quantity": held,
                                  "price": r.get("price"), "cash": r.get("cash")}
            result["reason"] = f"sold {held} to reduce risk ({r.get('price')})"
            _mem().record_decision(symbol, "PIPELINE SELL", signal=result["signal"],
                                   confidence=float(result["confidence"]),
                                   reason=f"deterministic reduce x{held}", source="pipeline")
        except Exception as exc:  # noqa: BLE001
            result["reason"] = f"execute blocked: {exc}"
        return result

    # ---- BUY CANDIDATE: size, risk, compliance, execute ----
    if result["signal"] != BUY_SIGNAL:
        result["reason"] = f"no clear setup ({result['signal']}) — cheap NO TRADE"
        return result
    result["steps"].append(f"signal: {BUY_SIGNAL} confidence {result['confidence']}%")

    if _held_quantity(symbol):
        result["reason"] = "already holding — no stacking"
        return result

    try:
        size = market.position_size(symbol, capital, risk_pct)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"position sizing error: {exc}"
        return result
    qty = int(size.get("max_quantity", 0))
    if qty < 1:
        result["reason"] = f"position sizing produced {qty} units — no trade"
        return result
    notional = float(size.get("notional", 0) or 0)
    result["steps"].append(f"risk: size {qty} units (notional ₹{notional:,.0f}, "
                           f"stop ₹{size.get('stop_loss_price')})")

    allows, why = _risk_allows(symbol, notional, capital)
    if not allows:
        result["reason"] = f"risk gate refused: {why}"
        return result
    result["steps"].append(f"risk: portfolio gate ok ({why})")

    try:
        _compliance_engine().pre_trade(f"PIPELINE paper buy {symbol} x{qty}")
        result["steps"].append("compliance: SEBI pre-trade checks passed")
    except ExecutionBlockedError as exc:
        result["reason"] = f"compliance blocked: {exc}"
        return result

    try:
        r = market.paper_buy(symbol, qty, stop_loss=float(size.get("stop_loss_price")) or None)
        result["action"] = "BUY"
        result["executed"] = {"side": "BUY", "quantity": qty,
                              "price": r.get("price"), "cash": r.get("cash"),
                              "stop_loss": size.get("stop_loss_price")}
        result["reason"] = f"bought {qty} @ {r.get('price')} (stop {size.get('stop_loss_price')})"
        result["steps"].append("execute: PAPER buy (safety gate + daily-loss gate inside)")
        _mem().record_decision(symbol, "PIPELINE BUY", signal=result["signal"],
                               confidence=float(result["confidence"]),
                               reason=f"deterministic buy x{qty} @ {r.get('price')}",
                               source="pipeline")
    except (ValueError, ExecutionBlockedError) as exc:
        result["reason"] = f"execute blocked: {exc}"
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"execute error: {exc}"
    return result


def run_pipeline(symbols: list[str] | None = None, capital: float | None = None,
                 risk_pct: float | None = None) -> dict[str, Any]:
    """Run the deterministic pipeline across the symbol universe (fail-open)."""
    from . import market

    syms = [s.upper() for s in (symbols or default_symbols())]
    try:
        cap = capital if capital else float(market.paper_portfolio().get("capital", 1_000_000))
    except Exception:  # noqa: BLE001
        cap = 1_000_000.0
    rp = risk_pct if risk_pct else (float(_env("PIPELINE_RISK_PCT", "2") or 2) * _regime_risk_scale())
    rp = max(0.1, rp)

    results = [run_symbol(s, cap, rp) for s in syms]
    buys = [r for r in results if r["action"] == "BUY"]
    sells = [r for r in results if r["action"] == "SELL"]
    no_trades = [r for r in results if r["action"] is None]

    from . import circuit_breaker as cb

    dl = "HEALTHY"
    try:
        dl = "TRIPPED" if cb.get_guard().is_tripped() else "HEALTHY"
    except Exception:  # noqa: BLE001
        pass

    summary = {
        "mode": "deterministic-pipeline",
        "enabled": enabled(),
        "symbols": syms,
        "capital": round(cap, 2),
        "risk_per_trade_pct": round(rp, 2),
        "buys": len(buys), "sells": len(sells), "no_trade": len(no_trades),
        "daily_loss": dl,
        "results": results,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    _audit({"event": "run", "symbols": syms, "buys": len(buys), "sells": len(sells),
            "no_trade": len(no_trades), "summary": summary})
    return summary


def run_cycle_report(_agent_name: str = "pipeline") -> str:
    """Human-readable one-cycle report for the auto-trading loop (LLM-free)."""
    summary = run_pipeline()
    lines = [
        f"PIPELINE run at {summary['timestamp']} (deterministic, no LLM) — "
        f"{len(summary['symbols'])} symbols, risk {summary['risk_per_trade_pct']}%/trade.",
    ]
    for r in summary["results"]:
        if r["action"]:
            lines.append(f"- {r['symbol']}: {r['action']} — {r['reason']}")
        else:
            lines.append(f"- {r['symbol']}: NO TRADE — {r['reason']}")
    lines.append(
        f"Summary: {summary['buys']} buy, {summary['sells']} sell, "
        f"{summary['no_trade']} no-trade. Daily-loss breaker: {summary['daily_loss']}. "
        "Paper/safe mode — no real money moved."
    )
    return "\n".join(lines)


__all__ = ["enabled", "default_symbols", "run_symbol", "run_pipeline",
           "run_cycle_report", "BUY_SIGNAL", "SELL_SIGNAL"]
