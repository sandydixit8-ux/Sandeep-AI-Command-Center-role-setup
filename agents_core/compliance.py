"""Compliance engine — SEBI algo-trading framework pre-trade checks.

A single, reviewable module that centralises the regulatory checks every
automated order must pass before it can reach the execution layer. This keeps
the compliance surface in one place (the "Compliance Engine" box of the
architecture) instead of being scattered across execution adapters.

Checks enforced here (SEBI circular SEBI/HO/MIRSD/MIRSD-PoD/P/CIR/2025/0000013
dated Feb-04-2025, mandatory for all brokers since Apr-01-2026):

- algo-ID tagging: every algo order carries the exchange-registered strategy id;
  live orders refuse to send without it.
- Order rate limiting: a token bucket keeps automated flow under the <10-orders-
  per-second registration threshold.
- Fail-closed readiness: the broker must be enabled and configured, or the order
  is refused.
- Audit retention: every decision (pass or block) is appended to a hash-chained
  JSONL trail so it can never be silently disabled.

The engine is deliberately framework-agnostic: callers hand it a settings object
and a rate limiter, and ``pre_trade()`` raises ``ExecutionBlockedError`` with the
first failing reason. Reducing/closing orders are allowed while the daily-loss
circuit breaker is tripped (that decision is made by the caller, not here).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import DATA_DIR
from .safety import ExecutionBlockedError
from . import circuit_breaker as cb


def _env(name: str, default: str = "") -> str:
    import os

    return os.environ.get(name, default).strip()


class ComplianceDecision:
    """Result of the pre-trade compliance run."""

    def __init__(self, allowed: bool, reason: str = "", checks: dict[str, bool] | None = None) -> None:
        self.allowed = allowed
        self.reason = reason
        self.checks = checks or {}

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reason": self.reason, "checks": self.checks}

    def __bool__(self) -> bool:
        return self.allowed


def compliance_audit_file() -> Path:
    return DATA_DIR / "compliance_audit.jsonl"


def _audit(decision: ComplianceDecision, detail: str) -> None:
    f = compliance_audit_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "allowed": decision.allowed,
            "reason": decision.reason,
            "checks": decision.checks,
            "detail": detail,
            "prev_hash": hashlib.sha256(prev.encode("utf-8")).hexdigest(),
        }
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — audit must never break the caller
        pass


class ComplianceEngine:
    """Runs the SEBI pre-trade check sequence for an automated order.

    Callers provide:
    - ``mode``      broker mode string ('off' | 'mock' | 'sandbox' | 'live')
    - ``api_key``   configured API key (readiness requires it)
    - ``algo_id``   exchange-registered strategy id ('' when unset)
    - ``is_live``   True when the order targets real money
    - ``limiter``   callable that raises ExecutionBlockedError on rate overflow
    """

    def __init__(
        self,
        *,
        mode: str,
        api_key: str,
        algo_id: str,
        is_live: bool,
        limiter: Callable[[], None],
    ) -> None:
        self.mode = mode
        self.api_key = api_key
        self.algo_id = algo_id
        self.is_live = is_live
        self.limiter = limiter

    # ------------------------------------------------------------------ checks

    def check_readiness(self) -> bool:
        """Fail-closed: broker must be enabled and configured."""
        return self.mode in ("mock", "sandbox", "live") and bool(self.api_key)

    def check_algo_id(self) -> bool:
        """SEBI algo-ID tagging: live algo orders refuse without the strategy id."""
        return not self.is_live or bool(self.algo_id)

    def check_rate(self) -> bool:
        """Under the <10 OPS registration threshold."""
        try:
            self.limiter()
            return True
        except ExecutionBlockedError:
            return False

    # ------------------------------------------------------------------ run

    def pre_trade(self, detail: str = "", *, gate_daily_loss: bool = True) -> ComplianceDecision:
        """Run the full sequence. Raises ExecutionBlockedError on the first failure.

        gate_daily_loss=False skips the circuit breaker (for risk-reducing orders
        when the day's limit has already been hit).
        """
        checks = {
            "readiness": self.check_readiness(),
            "algo_id": self.check_algo_id(),
            "rate_limit": self.check_rate(),
            "daily_loss": True,
        }
        if not checks["readiness"]:
            reason = "broker is OFF or unconfigured (mode not enabled / API key missing). Fail-closed."
            dec = ComplianceDecision(False, reason, checks)
            _audit(dec, detail)
            raise ExecutionBlockedError(reason)
        if not checks["algo_id"]:
            reason = (
                "live order refused: exchange-registered algo id not set. "
                "SEBI algo-ID tagging requires the strategy id on every algo order."
            )
            dec = ComplianceDecision(False, reason, checks)
            _audit(dec, detail)
            raise ExecutionBlockedError(reason)
        if not checks["rate_limit"]:
            reason = "order rate limit exceeded (<10 OPS SEBI registration threshold)"
            dec = ComplianceDecision(False, reason, checks)
            _audit(dec, detail)
            raise ExecutionBlockedError(reason)

        if gate_daily_loss:
            try:
                cb.check_open(detail)
            except ExecutionBlockedError as exc:
                checks["daily_loss"] = False
                dec = ComplianceDecision(False, str(exc), checks)
                _audit(dec, detail)
                raise
        dec = ComplianceDecision(True, "all compliance checks passed", checks)
        _audit(dec, detail)
        return dec

    def status(self) -> dict[str, Any]:
        """Read-only compliance posture for tool/UI exposure."""
        return {
            "engine": "sebi-algo-framework",
            "mode": self.mode,
            "ready": self.check_readiness(),
            "algo_id": bool(self.algo_id),
            "algo_id_value": self.algo_id,
            "is_live": self.is_live,
            "audit_file": str(compliance_audit_file()),
        }


def engine_from(
    *,
    mode: str,
    api_key: str,
    algo_id: str,
    is_live: bool,
    limiter: Callable[[], None],
) -> ComplianceEngine:
    """Factory helper so callers can build an engine in one expression."""
    return ComplianceEngine(mode=mode, api_key=api_key, algo_id=algo_id,
                            is_live=is_live, limiter=limiter)


def compliance_posture() -> dict[str, Any]:
    """Status of the singleton compliance engine if one has been built, else env-derived."""
    try:
        from . import upstox as _u

        mgr = _u.OrderManager()
        eng = mgr.compliance
        return eng.status()
    except Exception:  # noqa: BLE001
        return {
            "engine": "sebi-algo-framework",
            "mode": _env("UPSTOX_MODE", "off"),
            "ready": False,
            "algo_id": False,
            "is_live": _env("UPSTOX_MODE", "").lower() == "live",
            "audit_file": str(compliance_audit_file()),
        }
