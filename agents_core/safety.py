"""Runtime execution safety gate.

Every trade-like action (paper or any future live path) MUST pass through
``safety_gate()`` before it is executed. This is the single, enforced safety
point for all execution paths (the QA framework's "one safety gate" contract).

Two independent conditions can block execution (fail-closed):

1. ``LIVE_TRADING_ENABLED`` env var — if it is not exactly ``true`` (default is
   unset/false), live-order actions are refused. This system is PAPER-ONLY, so
   live actions are never allowed by default.

2. A kill switch — an emergency stop that blocks even paper actions. It is
   active if the env var ``AGENT_KILL_SWITCH`` is truthy OR a marker file exists
   at ``data/.kill_switch`` (or ``AGENT_KILL_SWITCH_FILE`` if configured).
   A marker file can be created externally without touching config/code:
   ``echo stop > data/.kill_switch``

Every gate decision is appended to ``data/execution_audit.jsonl`` (append-only,
hash-chained) so it can never be silently disabled.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BASE_DIR, DATA_DIR


class ExecutionBlockedError(Exception):
    """Raised when safety_gate() refuses an action (fail-closed)."""


def _env_true(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def live_trading_enabled() -> bool:
    """Live orders are only permitted if LIVE_TRADING_ENABLED=true AND no kill switch."""
    return _env_true("LIVE_TRADING_ENABLED") and not kill_switch_active()[0]


def kill_switch_file() -> Path:
    override = os.environ.get("AGENT_KILL_SWITCH_FILE", "").strip()
    if override:
        return Path(override)
    return DATA_DIR / ".kill_switch"


def kill_switch_active() -> tuple[bool, str]:
    """Independent emergency stop. Env var OR marker file; neither can be overridden at runtime by tools."""
    if _env_true("AGENT_KILL_SWITCH"):
        return True, "env AGENT_KILL_SWITCH is set"
    f = kill_switch_file()
    if f.exists():
        return True, f"kill-switch file present: {f}"
    return False, "no kill switch"


def _audit_record(action: str, detail: str, allowed: bool) -> None:
    f = DATA_DIR / "execution_audit.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        prev_hash = hashlib.sha256(prev.encode("utf-8")).hexdigest()
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "action": action,
            "detail": detail,
            "allowed": allowed,
            "live_trading_enabled": live_trading_enabled(),
            "prev_hash": prev_hash,
        }
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — audit must never break the caller
        pass


def safety_gate(action: str, detail: str = "") -> None:
    """Enforce the single execution gate. Raises ExecutionBlockedError when refused.

    action: 'paper_stock', 'paper_option', 'live_order', ... Any action whose
    name starts with 'live_' is treated as a live-money order.
    """
    is_live = action.startswith("live_")
    killed, why = kill_switch_active()

    if killed:
        _audit_record(action, f"{detail} blocked: {why}".strip(), allowed=False)
        raise ExecutionBlockedError(
            f"execution blocked by kill switch ({why}). No trade executed."
        )
    if is_live and not live_trading_enabled():
        _audit_record(action, f"{detail} blocked: LIVE_TRADING_ENABLED not true".strip(), allowed=False)
        raise ExecutionBlockedError(
            "live orders are disabled (LIVE_TRADING_ENABLED is not 'true'). "
            "This system is paper-only; no real-money order was sent."
        )

    _audit_record(action, detail or "no detail", allowed=True)
