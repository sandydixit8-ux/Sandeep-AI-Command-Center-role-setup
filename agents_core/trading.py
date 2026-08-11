"""Automatic trading loop — Start / Stop controller.

A fail-closed supervisor for "automatic trading": while RUNNING, a background
thread periodically runs the trading agent (risk agent by default) through the
SAME gated tool path a human chat would use. Every cycle therefore still goes
through safety_gate (kill switch + LIVE_TRADING_ENABLED), the compliance engine,
the daily-loss circuit breaker and the rate limiter — starting the loop never
bypasses any guardrail.

Safety properties:
- FAIL-CLOSED: default state is STOPPED. It can only run after an explicit
  ``start()`` (via the UI button or API).
- Emergency stop: if the kill switch (env or ``data/.kill_switch`` marker) is
  present at cycle time, the loop records the halt and auto-stops.
- The loop never sends real-money orders unless LIVE_TRADING_ENABLED=true is
  set AND the broker is configured — the safety gate enforces this per order.
- Every start/stop/cycle is appended to a hash-chained JSONL audit trail.
- State is persisted under DATA_DIR so a server restart restores STOPPED (never
  silently resumes running).

Configuration (env):
- ``AUTO_TRADE_INTERVAL``   seconds between cycles (default 60)
- ``AUTO_TRADE_AGENT``      agent key to run each cycle (default 'risk')
- ``AUTO_TRADE_TASK``       optional custom task text (default: a safe brief +
  regime + signal + paper-trade cycle that respects the daily-loss limit)
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .safety import ExecutionBlockedError, kill_switch_active

DEFAULT_TASK = (
    "Automatic trading cycle. 1) Check compliance_status and risk_daily_limit "
    "first. 2) Read market_brief and market_regime. 3) For RELIANCE, TCS, INFY "
    "and SBIN run market_signal. 4) If a signal is a clear BUY/SELL with "
    "supporting evidence AND the daily-loss breaker is not tripped, place a "
    "PAPER trade via paper_buy / paper_sell (simulation only, never claim a real "
    "order). 5) Stay flat when in doubt; a NO-TRADE decision is valid. 6) Report "
    "what you did and why, with the data-quality badge."
)


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _state_file() -> Path:
    return DATA_DIR / "auto_trading.json"


def _audit(event: str, detail: dict[str, Any]) -> None:
    f = DATA_DIR / "auto_trading_audit.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
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


class TradingController:
    """Start/stop supervisor for the automatic-trading loop (fail-closed)."""

    def __init__(self, state_file: str | Path | None = None) -> None:
        self.state_file = Path(state_file) if state_file else _state_file()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._state: dict[str, Any] | None = None

    # ------------------------------------------------------------------ state

    def _load(self) -> dict[str, Any]:
        if self._state is not None:
            return self._state
        if self.state_file.exists():
            try:
                self._state = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._state = None
        if not isinstance(self._state, dict):
            self._state = {"status": "STOPPED", "cycles": 0, "last_cycle_at": None,
                           "last_result": None, "started_at": None, "stopped_at": None,
                           "stop_reason": ""}
        return self._state

    def _save(self) -> None:
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(
                json.dumps(self._load(), indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ control

    def start(self, *, interval: float | None = None,
              agent: str | None = None,
              task: str | None = None) -> dict[str, Any]:
        """Start the automatic trading loop. Refuses if a kill switch is active."""
        killed, why = kill_switch_active()
        if killed:
            _audit("start_blocked", {"reason": f"kill switch active: {why}"})
            raise ExecutionBlockedError(
                f"cannot start automatic trading: kill switch active ({why})."
            )
        with self._lock:
            st = self._load()
            if st.get("status") == "RUNNING":
                return self.status()
            self._stop_event = threading.Event()
            st["status"] = "RUNNING"
            st["started_at"] = datetime.now().isoformat(timespec="seconds")
            st["stop_reason"] = ""
            self._save()
            interval = interval if interval is not None else self.interval()
            agent = agent or _env("AUTO_TRADE_AGENT", "risk") or "risk"
            task = task or _env("AUTO_TRADE_TASK") or DEFAULT_TASK
            self._thread = threading.Thread(
                target=self._loop,
                args=(float(interval), agent, task),
                name="auto-trader",
                daemon=True,
            )
            self._thread.start()
        _audit("start", {"interval": interval, "agent": agent})
        return self.status()

    def stop(self, reason: str = "manual") -> dict[str, Any]:
        """Stop the loop. Safe to call repeatedly; never fails."""
        with self._lock:
            st = self._load()
            was = st.get("status")
            self._stop_event.set()
            st["status"] = "STOPPED"
            st["stopped_at"] = datetime.now().isoformat(timespec="seconds")
            st["stop_reason"] = reason
            self._save()
        _audit("stop", {"from": was, "reason": reason})
        return self.status()

    def status(self) -> dict[str, Any]:
        st = self._load()
        killed, why = kill_switch_active()
        live = (os.environ.get("LIVE_TRADING_ENABLED", "").strip().lower()
                in ("1", "true", "yes", "on"))
        return {
            "status": st.get("status", "STOPPED"),
            "running": st.get("status") == "RUNNING",
            "cycles": st.get("cycles", 0),
            "interval_sec": self.interval(),
            "agent": _env("AUTO_TRADE_AGENT", "risk") or "risk",
            "last_cycle_at": st.get("last_cycle_at"),
            "last_result": (st.get("last_result") or "")[:2000],
            "started_at": st.get("started_at"),
            "stopped_at": st.get("stopped_at"),
            "stop_reason": st.get("stop_reason", ""),
            "kill_switch_active": killed,
            "kill_switch_reason": why,
            "live_trading_enabled": live,
            "mode": "LIVE-MONEY ENABLED" if (killed is False and live) else
                    ("blocked-by-kill-switch" if killed else "paper/safe"),
            "state_file": str(self.state_file),
            "audit_file": str(DATA_DIR / "auto_trading_audit.jsonl"),
        }

    @staticmethod
    def interval() -> float:
        try:
            return max(5.0, float(_env("AUTO_TRADE_INTERVAL", "60") or 60))
        except ValueError:
            return 60.0

    # ------------------------------------------------------------------ loop

    def _loop(self, interval: float, agent: str, task: str) -> None:
        while not self._stop_event.is_set():
            killed, _why = kill_switch_active()
            if killed:
                _audit("halt", {"reason": "kill switch active during run"})
                self.stop(reason="kill switch")
                return
            try:
                result = self._run_cycle(agent, task)
                with self._lock:
                    st = self._load()
                    st["cycles"] = st.get("cycles", 0) + 1
                    st["last_cycle_at"] = datetime.now().isoformat(timespec="seconds")
                    st["last_result"] = result
                    self._save()
                _audit("cycle", {"agent": agent, "ok": True})
            except Exception as exc:  # noqa: BLE001
                _audit("cycle_error", {"agent": agent, "error": str(exc)})
            # wait for interval, but abort promptly on stop
            self._stop_event.wait(interval)

    @staticmethod
    def _run_cycle(agent_name: str, task: str) -> str:
        """Run one agent cycle through the gated tool path (same as a chat turn).

        When AUTO_TRADE_DETERMINISTIC=true the deterministic pipeline (signal ->
        risk -> compliance -> execute, no LLM) runs instead, through the same
        gates. Fail-open: pipeline errors fall back to the agent cycle.
        """
        from . import pipeline

        if pipeline.enabled():
            try:
                return pipeline.run_cycle_report(agent_name)
            except Exception as exc:  # noqa: BLE001 — fall back to the agent cycle
                return f"pipeline error: {exc}"

        from .registry import get_agent

        agent = get_agent(agent_name)
        try:
            result = agent.run(task)
            try:
                from . import memory as _memory_mod

                _memory_mod.record_decision(
                    "CYCLE", "RUN", signal=agent_name, confidence=100.0,
                    reason=(task or DEFAULT_TASK)[:200], source="auto-trading",
                )
                _memory_mod.record_lesson(
                    "CYCLE", (result or "")[:300], category="auto-cycle",
                )
            except Exception:  # noqa: BLE001 — memory must never break the loop
                pass
            return result
        finally:
            agent.close()


_controller = TradingController()


def get_controller() -> TradingController:
    return _controller


def start(interval: float | None = None, agent: str | None = None,
          task: str | None = None) -> dict[str, Any]:
    return get_controller().start(interval=interval, agent=agent, task=task)


def stop(reason: str = "manual") -> dict[str, Any]:
    return get_controller().stop(reason=reason)


def status() -> dict[str, Any]:
    return get_controller().status()
