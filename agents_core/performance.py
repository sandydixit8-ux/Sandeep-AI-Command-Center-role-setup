"""Today performance aggregation for the monitoring dashboard.

Pulls the day's trading-loop stats, paper-portfolio P&L, daily-loss breaker,
execution audit, options paper activity and memory-engine counts into one
snapshot so the frontend "Today Performance" page renders in a single request.

Design properties:
- Read-only: never mutates state files or the guard.
- Fail-open: any missing/corrupt source degrades to empty data, never an error.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _is_today(ts: Any) -> bool:
    return isinstance(ts, str) and ts.startswith(_today())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def today_performance() -> dict[str, Any]:
    """Snapshot of how the agent performed today (read-only, fail-open)."""
    day = _today()

    from . import circuit_breaker as cb
    from . import market
    from . import trading

    # ---- trading loop: status + today's audit events ----
    try:
        tr = trading.status()
    except Exception:  # noqa: BLE001
        tr = {}
    events = _read_jsonl(DATA_DIR / "auto_trading_audit.jsonl")
    cycles_today = [e for e in events if _is_today(e.get("ts"))]
    ok_cycles = sum(1 for e in cycles_today if e.get("event") == "cycle" and e.get("ok"))
    err_cycles = sum(1 for e in cycles_today if e.get("event") == "cycle_error")
    last_err = next((e.get("error") for e in reversed(cycles_today)
                     if e.get("event") == "cycle_error"), "")
    trading_pic = {
        "status": tr.get("status", "STOPPED"),
        "running": bool(tr.get("running")),
        "cycles_total": tr.get("cycles", 0),
        "cycles_today": len(cycles_today),
        "cycles_ok_today": ok_cycles,
        "cycles_error_today": err_cycles,
        "interval_sec": tr.get("interval_sec"),
        "agent": tr.get("agent"),
        "last_cycle_at": tr.get("last_cycle_at"),
        "started_at": tr.get("started_at"),
        "stopped_at": tr.get("stopped_at"),
        "stop_reason": tr.get("stop_reason", ""),
        "mode": tr.get("mode"),
        "live_trading_enabled": bool(tr.get("live_trading_enabled")),
        "kill_switch_active": bool(tr.get("kill_switch_active")),
        "last_result": (tr.get("last_result") or "")[:2000],
        "last_error": last_err,
    }

    # ---- paper portfolio (live marks) ----
    try:
        pf = market.paper_portfolio()
    except Exception:  # noqa: BLE001
        raw = _read_json(DATA_DIR / "paper_portfolio.json")
        pf = raw if isinstance(raw, dict) else {}
    positions = pf.get("positions", [])
    trades = pf.get("trades", [])
    today_trades = [t for t in trades if _is_today(t.get("time"))]
    unrealized = round(sum(float(p.get("unrealized_pnl", 0) or 0) for p in positions), 2)
    realized_all = round(sum(float(t.get("pnl", 0) or 0) for t in trades), 2)
    realized_today = round(sum(float(t.get("pnl", 0) or 0)
                               for t in today_trades if t.get("type") == "SELL"), 2)
    buys_today = sum(1 for t in today_trades if t.get("type") == "BUY")
    sells_today = sum(1 for t in today_trades if t.get("type") == "SELL")

    # ---- daily-loss breaker ----
    try:
        dl = cb.get_guard().status()
    except Exception:  # noqa: BLE001
        dl = {}
    start_equity = float(dl.get("start_equity") or pf.get("capital") or 0)
    equity_now = float(pf.get("total_value", 0) or 0)
    day_pnl = round(equity_now - start_equity, 2)
    day_return_pct = round(day_pnl / start_equity * 100, 2) if start_equity else 0.0
    day_pic = {
        "start_equity": round(start_equity, 2),
        "equity_now": round(equity_now, 2),
        "day_pnl": day_pnl,
        "day_return_pct": day_return_pct,
        "realized_today": round(float(dl.get("realized_today", realized_today)), 2),
        "unrealized_today": unrealized,
        "buys_today": buys_today,
        "sells_today": sells_today,
        "trade_count_today": len(today_trades),
    }
    daily_loss_pic = {
        "day": dl.get("day", ""),
        "start_equity": dl.get("start_equity"),
        "equity_now": dl.get("equity_now"),
        "daily_loss": dl.get("daily_loss", 0.0),
        "limit_inr": dl.get("limit_inr", 0.0),
        "limit_pct": dl.get("limit_pct", 0.0),
        "tripped": bool(dl.get("tripped")),
        "tripped_at": dl.get("tripped_at"),
    }

    # ---- execution audit today (desc) ----
    exec_today = [e for e in _read_jsonl(DATA_DIR / "execution_audit.jsonl") if _is_today(e.get("ts"))]
    exec_today.sort(key=lambda e: str(e.get("ts", "")), reverse=True)

    # ---- options paper activity today ----
    opts = _read_json(DATA_DIR / "options_paper_positions.json")
    opts = opts if isinstance(opts, list) else []
    opt_open = [o for o in opts if o.get("status") == "OPEN"]
    opt_today = [o for o in opts if _is_today(o.get("entered_at")) or _is_today(o.get("exited_at"))]

    # ---- memory engine ----
    mem_stats = {}
    mem_today = {"decisions": 0, "outcomes": 0, "lessons": 0}
    try:
        from . import memory as memory_mod

        mem_stats = memory_mod.memory_stats()
        mem_dir = memory_mod.MEMORY_DIR
        mem_today = {
            "decisions": sum(1 for r in _read_jsonl(mem_dir / "decisions.jsonl") if _is_today(r.get("ts"))),
            "outcomes": sum(1 for r in _read_jsonl(mem_dir / "outcomes.jsonl") if _is_today(r.get("ts"))),
            "lessons": sum(1 for r in _read_jsonl(mem_dir / "lessons.jsonl") if _is_today(r.get("ts"))),
        }
    except Exception:  # noqa: BLE001
        pass

    return {
        "day": day,
        "now": datetime.now().isoformat(timespec="seconds"),
        "trading": trading_pic,
        "portfolio": {
            "cash": round(float(pf.get("cash", 0) or 0), 2),
            "total_value": equity_now,
            "pnl_all": round(float(pf.get("pnl", 0) or 0), 2),
            "return_pct_all": float(pf.get("return_pct", 0) or 0),
            "exposure_pct": float(pf.get("exposure_pct", 0) or 0),
            "realized_all": realized_all,
            "win_rate_pct_all": float(pf.get("win_rate_pct", 0) or 0),
            "open_count": len(positions),
            "positions": positions,
        },
        "day": day_pic,
        "daily_loss": daily_loss_pic,
        "activity": exec_today,
        "cycles": list(reversed(cycles_today)),
        "options": {"open": opt_open, "today": opt_today},
        "memory": {
            "stats": mem_stats,
            "today": mem_today,
        },
    }


def executions_by_date(date: str | None = None) -> dict[str, Any]:
    """Execution, cycle and option-paper history filtered by date (YYYY-MM-DD).

    Read-only and fail-open: missing/corrupt sources degrade to empty lists.
    Returns the list of dates that have any activity so the UI can build a picker.
    """
    def _day(ts: Any) -> str:
        if not isinstance(ts, str):
            return ""
        return ts.split("T")[0][:10]

    execs = _read_jsonl(DATA_DIR / "execution_audit.jsonl")
    cycles = _read_jsonl(DATA_DIR / "auto_trading_audit.jsonl")
    opts = _read_json(DATA_DIR / "options_paper_positions.json")
    opts = opts if isinstance(opts, list) else []

    available = sorted({
        _day(e.get("ts")) for e in execs
    } | {
        _day(e.get("ts")) for e in cycles
    } | {
        _day(o.get("entered_at")) for o in opts
    } | {
        _day(o.get("exited_at")) for o in opts
    } - {""}, reverse=True)

    if date:
        execs = [e for e in execs if _day(e.get("ts")) == date]
        cycles = [e for e in cycles if _day(e.get("ts")) == date]
        opts = [o for o in opts if _day(o.get("entered_at")) == date or _day(o.get("exited_at")) == date]

    execs.sort(key=lambda e: str(e.get("ts", "")), reverse=True)
    cycles.sort(key=lambda e: str(e.get("ts", "")), reverse=True)

    def _is_buy(e: dict[str, Any]) -> bool:
        return e.get("side") == "BUY" or str(e.get("action", "")).lower().startswith("buy")

    def _is_sell(e: dict[str, Any]) -> bool:
        return e.get("side") == "SELL" or str(e.get("action", "")).lower().startswith("sell")

    return {
        "date": date or "all",
        "available_dates": available,
        "executions": execs,
        "cycles": cycles,
        "options": opts,
        "summary": {
            "total": len(execs),
            "buys": sum(1 for e in execs if _is_buy(e)),
            "sells": sum(1 for e in execs if _is_sell(e)),
            "blocked": sum(1 for e in execs if e.get("allowed") is False),
        },
    }


__all__ = ["today_performance", "executions_by_date"]
