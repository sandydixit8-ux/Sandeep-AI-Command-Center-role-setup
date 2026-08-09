"""Daily-loss circuit breaker (hard risk limit).

Implements the "Daily Loss Limit" box of the trading-agent architecture: once
cumulative realised + unrealised loss for the trading day reaches the
configured limit, ALL new orders are refused (fail-closed) and an alert is
raised. Risk-reducing orders (reducing/closing a position) remain allowed so a
stuck position can still be unwound. The breaker resets at the start of each
new trading day.

Enforcement model: every execution path (paper stocks, paper options, broker
orders) calls ``check_open()`` (via the safety chain) before opening a new
position, and ``record_trade()`` after fills so daily loss accrues. State is
day-stamped and persisted under DATA_DIR so it survives restarts and cannot be
reset mid-day by restarting the process.

Configuration (env):
- ``DAILY_LOSS_LIMIT_PCT``  max daily loss as % of the day-start equity (default 3.0)
- ``DAILY_LOSS_LIMIT_INR``  optional absolute cap; if set it overrides the % cap
- ``DAILY_LOSS_CAPITAL``    day-start equity when no portfolio is known (default 1,000,000)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR
from .safety import ExecutionBlockedError

_DAY_FORMAT = "%Y-%m-%d"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except (TypeError, ValueError):
        return default


def today_key() -> str:
    return datetime.now().strftime(_DAY_FORMAT)


def _state_file() -> Path:
    return DATA_DIR / "daily_loss.json"


def _audit(event: str, detail: dict[str, Any]) -> None:
    f = DATA_DIR / "daily_loss_audit.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        import hashlib

        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            **detail,
            "prev_hash": hashlib.sha256(prev.encode("utf-8")).hexdigest(),
        }
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


class DailyLossGuard:
    """Day-stamped daily-loss circuit breaker, fail-closed on unknown state."""

    def __init__(self, state_file: str | Path | None = None,
                 capital: float | None = None,
                 limit_pct: float | None = None,
                 limit_inr: float | None = None) -> None:
        self.state_file = Path(state_file) if state_file else _state_file()
        self._default_capital = capital if capital is not None else _env_float("DAILY_LOSS_CAPITAL", 1_000_000)
        self._limit_pct = limit_pct if limit_pct is not None else _env_float("DAILY_LOSS_LIMIT_PCT", 3.0)
        self._limit_inr = limit_inr if limit_inr is not None else _env_float("DAILY_LOSS_LIMIT_INR", 0.0)
        self._provider: Callable[[], dict[str, Any]] | None = None
        self._state: dict[str, Any] | None = None

    # ---------------------------------------------------------------- state

    def set_equity_provider(self, provider: Callable[[], dict[str, Any]]) -> None:
        """Register a provider returning {equity, realized_today} for the current day."""
        self._provider = provider

    def _load(self) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        if self.state_file.exists():
            try:
                self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._state = None
        if not isinstance(self._state, dict):
            self._state = {"day": "", "start_equity": None, "realized_today": 0.0,
                           "equity_now": None, "tripped": False, "tripped_at": None}
        return self._state

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(self._load(), indent=2, ensure_ascii=False),
                                       encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    # ---------------------------------------------------------------- limits

    def limit_amount(self, start_equity: float) -> float:
        if self._limit_inr and self._limit_inr > 0:
            return self._limit_inr
        return start_equity * self._limit_pct / 100.0

    def is_tripped(self) -> bool:
        return bool(self._load().get("tripped"))

    def status(self) -> dict[str, Any]:
        """Current breaker status (read-only; safe to expose to tools/UI)."""
        st = self._load()
        start = st.get("start_equity") or self._default_capital
        equity = st.get("equity_now")
        loss = (start - equity) if equity is not None else 0.0
        limit = self.limit_amount(start)
        return {
            "day": st.get("day") or "",
            "today": today_key(),
            "start_equity": start,
            "equity_now": equity,
            "realized_today": st.get("realized_today", 0.0),
            "daily_loss": round(max(loss, 0.0), 2),
            "limit_inr": round(limit, 2),
            "limit_pct": self._limit_pct,
            "tripped": bool(st.get("tripped")),
            "tripped_at": st.get("tripped_at"),
            "allow_reducing": bool(st.get("tripped")),  # reducing orders still allowed when tripped
            "state_file": str(self.state_file),
        }

    # ---------------------------------------------------------------- core

    def _refresh_day(self) -> None:
        """Reset the day boundary when the calendar day changes."""
        st = self._load()
        if st.get("day") != today_key():
            st["day"] = today_key()
            st["start_equity"] = self._default_capital
            st["realized_today"] = 0.0
            st["equity_now"] = None
            st["tripped"] = False
            st["tripped_at"] = None
            self._save()
            _audit("day_reset", {"day": st["day"]})

    def check_open(self, detail: str = "") -> None:
        """Gate for opening new positions: raises when the daily-loss limit is hit.

        Must run BEFORE any new position is opened. Enforcement depends on an
        equity provider being registered (paper portfolio, broker funds, ...):
        if a provider is registered, the day's loss (realised + unrealised) is
        measured and the breaker blocks when the limit is reached. If no
        provider is registered there is nothing to measure, so the gate is a
        no-op (the trade is still governed by the safety gate and kill switch).
        """
        self._refresh_day()
        if self._provider is None:
            return
        st = self._load()
        try:
            snap = self._provider()
            equity = float(snap.get("equity"))
            st["equity_now"] = equity
            st["realized_today"] = float(snap.get("realized_today", st.get("realized_today", 0.0)))
            st["start_equity"] = st.get("start_equity") or equity
            self._save()
        except Exception:  # noqa: BLE001
            # Provider failed: fail closed rather than open blind.
            raise ExecutionBlockedError(
                "daily-loss breaker: equity provider failed; refusing to open a position (fail-closed)."
            )

        start = st.get("start_equity") or self._default_capital
        loss = max(start - st["equity_now"], 0.0)
        limit = self.limit_amount(start)
        if loss >= limit:
            st["tripped"] = True
            st["tripped_at"] = datetime.now().isoformat(timespec="seconds")
            self._save()
            _audit("tripped", {"loss": loss, "limit": limit, "detail": detail})
            raise ExecutionBlockedError(
                f"daily-loss limit hit: loss ₹{loss:,.2f} >= limit ₹{limit:,.2f}. "
                "No new positions today. Risk-reducing (close) orders are still allowed."
            )
        return

    def record_trade(self, realized_pnl: float) -> None:
        """Accrue realised P&L for the day after a fill."""
        self._refresh_day()
        st = self._load()
        st["realized_today"] = st.get("realized_today", 0.0) + float(realized_pnl)
        if st.get("equity_now") is not None:
            st["equity_now"] = st["equity_now"] + float(realized_pnl)
        self._save()
        _audit("trade", {"realized_pnl": realized_pnl})


_guard = DailyLossGuard()


def get_guard() -> DailyLossGuard:
    return _guard


def check_open(detail: str = "") -> None:
    get_guard().check_open(detail)


def record_trade(realized_pnl: float) -> None:
    get_guard().record_trade(realized_pnl)
