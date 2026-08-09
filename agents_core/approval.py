"""Mode A — human-approval order flow.

"Agent drafts an order, a human approves it in the UI, then it executes."

Design goals:
- The agent NEVER executes directly in Mode A: it submits a *draft* (symbol,
  quantity, side, type, price, kind=paper|broker) which is parked as PENDING.
- A human approves or rejects via the web API (``/api/v1/approvals/...``).
- Approving executes through the SAME fail-closed path the agent would use
  (paper -> ``market.paper_buy/paper_sell``; broker -> ``OrderManager.place``),
  so the safety gate, compliance engine, rate limiter and daily-loss circuit
  breaker still apply at execution time — an approval can never bypass them.
- Every lifecycle event (draft / approve / reject / execute / error) is
  appended to a hash-chained JSONL trail so the approval trail is tamper-evident.
- State is day-agnostic but persisted under DATA_DIR so drafts survive restarts.

Enable by setting ``ORDER_APPROVAL_MODE=on`` (default ``off``). In "off" mode the
agent executes directly (Mode B, hands-off). The ``approve()`` path refuses to
run when Mode B is active (no draft flow), and refuses unknown/rejected ids.
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

MODE_ENV = "ORDER_APPROVAL_MODE"


def approval_enabled() -> bool:
    """Mode A is on when ORDER_APPROVAL_MODE=on (or true/1/yes)."""
    return os.environ.get(MODE_ENV, "").strip().lower() in ("1", "true", "on", "yes")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ApprovalError(Exception):
    """Raised when a draft cannot be submitted/approved/rejected."""


def approvals_state_file() -> Path:
    return DATA_DIR / "order_approvals.json"


def approvals_audit_file() -> Path:
    return DATA_DIR / "order_approvals_audit.jsonl"


def _audit(event: str, detail: dict[str, Any]) -> None:
    f = approvals_audit_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        record = {
            "ts": _now(),
            "event": event,
            **detail,
            "prev_hash": hashlib.sha256(prev.encode("utf-8")).hexdigest(),
        }
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


class ApprovalFlow:
    """Pending order drafts + approve/reject lifecycle, hash-chained audit."""

    def __init__(self, state_file: str | Path | None = None) -> None:
        self.state_file = Path(state_file) if state_file else approvals_state_file()
        self._lock = threading.Lock()
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
            self._state = {"seq": 0, "drafts": {}}
        self._state.setdefault("seq", 0)
        self._state.setdefault("drafts", {})
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

    # ------------------------------------------------------------------ submit

    def submit(self, draft: dict[str, Any]) -> dict[str, Any]:
        """Park an order draft for human approval. Returns the approval record."""
        required = ("symbol", "quantity", "transaction_type")
        for k in required:
            if k not in draft:
                raise ApprovalError(f"draft missing required field: {k}")
        if int(draft["quantity"]) <= 0:
            raise ApprovalError("quantity must be positive")
        if draft.get("transaction_type") not in ("BUY", "SELL"):
            raise ApprovalError("transaction_type must be BUY or SELL")
        kind = draft.get("kind", "paper")
        if kind not in ("paper", "broker"):
            raise ApprovalError("kind must be 'paper' or 'broker'")

        with self._lock:
            st = self._load()
            st["seq"] += 1
            aid = f"ap-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{st['seq']:04d}"
            record = {
                "id": aid,
                "status": "PENDING",
                "created_at": _now(),
                "kind": kind,
                "draft": {k: draft[k] for k in draft},
            }
            st["drafts"][aid] = record
            self._save()
        _audit("draft", {"id": aid, "kind": kind, "symbol": draft["symbol"],
                         "qty": draft["quantity"], "side": draft["transaction_type"]})
        return self.get(aid)

    # ------------------------------------------------------------------ read

    def get(self, approval_id: str) -> dict[str, Any] | None:
        return (self._load().get("drafts") or {}).get(approval_id)

    def list_pending(self) -> list[dict[str, Any]]:
        return [d for d in (self._load().get("drafts") or {}).values()
                if d.get("status") == "PENDING"]

    def list_all(self, limit: int = 50) -> list[dict[str, Any]]:
        drafts = list((self._load().get("drafts") or {}).values())
        return drafts[-int(limit):]

    # ------------------------------------------------------------------ approve / reject

    def approve(self, approval_id: str) -> dict[str, Any]:
        """Execute an approved draft through the fail-closed execution path."""
        if not approval_enabled():
            raise ApprovalError(
                "ORDER_APPROVAL_MODE is not 'on'; the approval flow is disabled."
            )
        with self._lock:
            st = self._load()
            rec = st["drafts"].get(approval_id)
            if rec is None:
                raise ApprovalError(f"unknown approval id: {approval_id}")
            if rec.get("status") != "PENDING":
                raise ApprovalError(
                    f"approval {approval_id} is {rec.get('status')}, not PENDING"
                )
            rec["status"] = "APPROVING"
            self._save()
        _audit("approve", {"id": approval_id})

        try:
            result = self._execute(rec["draft"], rec["kind"])
            with self._lock:
                rec = st["drafts"].get(approval_id)
                rec["status"] = "EXECUTED"
                rec["executed_at"] = _now()
                rec["result"] = result
                self._save()
            _audit("execute", {"id": approval_id, "result": result})
            return self.get(approval_id)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                rec = st["drafts"].get(approval_id)
                rec["status"] = "FAILED"
                rec["error"] = str(exc)
                rec["executed_at"] = _now()
                self._save()
            _audit("error", {"id": approval_id, "error": str(exc)})
            raise ApprovalError(f"approval {approval_id} execution failed: {exc}") from exc

    def reject(self, approval_id: str, reason: str = "") -> dict[str, Any]:
        with self._lock:
            st = self._load()
            rec = st["drafts"].get(approval_id)
            if rec is None:
                raise ApprovalError(f"unknown approval id: {approval_id}")
            if rec.get("status") != "PENDING":
                raise ApprovalError(
                    f"approval {approval_id} is {rec.get('status')}, not PENDING"
                )
            rec["status"] = "REJECTED"
            rec["rejected_at"] = _now()
            rec["reject_reason"] = reason
            self._save()
        _audit("reject", {"id": approval_id, "reason": reason})
        return self.get(approval_id)

    # ------------------------------------------------------------------ execute

    @staticmethod
    def _execute(draft: dict[str, Any], kind: str) -> dict[str, Any]:
        """Execute through the same fail-closed path a direct order would use."""
        symbol = draft["symbol"]
        quantity = int(draft["quantity"])
        transaction_type = draft["transaction_type"]
        if kind == "paper":
            from . import market

            if transaction_type == "BUY":
                return market.paper_buy(symbol, quantity)
            return market.paper_sell(symbol, quantity)
        from . import upstox as u

        order_type = draft.get("order_type", "MARKET")
        product = draft.get("product", "I")
        price = float(draft.get("price") or 0)
        trigger_price = float(draft.get("trigger_price") or 0)
        return u.OrderManager().place(
            symbol=symbol,
            quantity=quantity,
            transaction_type=transaction_type,
            order_type=order_type,
            product=product,
            price=price,
            trigger_price=trigger_price,
        )

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, Any]:
        st = self._load()
        drafts = st["drafts"]
        counts: dict[str, int] = {}
        for d in drafts.values():
            counts[d.get("status", "?")] = counts.get(d.get("status", "?"), 0) + 1
        return {
            "enabled": approval_enabled(),
            "mode": "human-approval (Mode A)" if approval_enabled() else "direct (Mode B)",
            "pending": len(self.list_pending()),
            "statuses": counts,
            "state_file": str(self.state_file),
            "audit_file": str(approvals_audit_file()),
        }


_flow = ApprovalFlow()


def get_flow() -> ApprovalFlow:
    return _flow


def submit_draft(draft: dict[str, Any]) -> dict[str, Any]:
    return get_flow().submit(draft)


def approve(approval_id: str) -> dict[str, Any]:
    return get_flow().approve(approval_id)


def reject(approval_id: str, reason: str = "") -> dict[str, Any]:
    return get_flow().reject(approval_id, reason)


def approvals_status() -> dict[str, Any]:
    return get_flow().status()
