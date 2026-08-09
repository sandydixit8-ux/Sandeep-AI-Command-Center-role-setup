"""Upstox broker execution adapter — live trading layer.

Fail-closed by default: the broker is in mode ``off`` unless ``UPSTOX_MODE`` is
configured (``mock`` | ``sandbox`` | ``live``), and real-money (``live``) orders
additionally require ``LIVE_TRADING_ENABLED=true``. Every order path passes
through ``safety_gate()`` before anything reaches the broker.

Compliance with the SEBI algo-trading framework (circular SEBI/HO/MIRSD/
MIRSD-PoD/P/CIR/2025/0000013 dated Feb-04-2025, applicable to all brokers since
Apr-01-2026) is built into this module:

- Algo-ID tagging: every order payload carries ``algo_id`` (the exchange-
  registered strategy identifier) when ``UPSTOX_ALGO_ID`` is set; live orders
  REFUSE to send without it.
- Order rate limiting: a token-bucket limiter keeps automated order flow under
  the 10-orders-per-second registration threshold (``UPSTOX_MAX_ORDERS_PER_SEC``).
- Session lifecycle: access tokens are day-stamped; a new trading day triggers
  an automatic logout + re-authorization cycle (SEBI daily auto-logout).
- Audit retention: every broker action is appended to ``data/broker_orders.jsonl``
  (retention-ready, exportable via ``export_broker_audit()`` for the 5-year rule).

The regulatory checks (algo-ID, rate limit, fail-closed readiness) live in the
dedicated compliance engine (``agents_core.compliance.ComplianceEngine``); this
adapter delegates every order/modify/cancel through it before touching the broker.

Modes
-----
off      (default)  No broker connectivity; order calls raise ExecutionBlockedError.
mock     Deterministic simulated broker (no network) for tests / the QA harness.
sandbox  Upstox sandbox endpoints using configured credentials.
live     Real-money broker. Requires ``UPSTOX_MODE=live`` AND
         ``LIVE_TRADING_ENABLED=true`` (enforced by the runtime safety gate).
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from .config import DATA_DIR
from .safety import ExecutionBlockedError, safety_gate

MODE_OFF = "off"
MODE_MOCK = "mock"
MODE_SANDBOX = "sandbox"
MODE_LIVE = "live"
SUPPORTED_MODES = (MODE_OFF, MODE_MOCK, MODE_SANDBOX, MODE_LIVE)

ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")
TRANSACTION_TYPES = ("BUY", "SELL")
PRODUCT_TYPES = ("I", "D")  # Intraday / Delivery


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class BrokerSettings:
    mode: str = MODE_OFF
    api_key: str = ""
    api_secret: str = ""
    redirect_uri: str = ""
    auth_code: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_file: Path = field(default_factory=lambda: DATA_DIR / "upstox_token.json")
    base_url: str = "https://api-hft.upstox.com"
    algo_id: str = ""
    max_orders_per_sec: float = 5.0
    instruments_file: str = ""

    def is_ready(self) -> bool:
        return self.mode in (MODE_MOCK, MODE_SANDBOX, MODE_LIVE) and bool(self.api_key)

    @property
    def is_live(self) -> bool:
        return self.mode == MODE_LIVE


def get_broker_settings() -> BrokerSettings:
    mode = _env("UPSTOX_MODE", MODE_OFF).lower()
    if mode not in SUPPORTED_MODES:
        raise ValueError(
            f"UPSTOX_MODE must be one of {SUPPORTED_MODES}, got {mode!r}"
        )
    token_file = _env("UPSTOX_TOKEN_FILE")
    instruments = _env("UPSTOX_INSTRUMENTS_FILE")
    try:
        max_ops = float(_env("UPSTOX_MAX_ORDERS_PER_SEC", "5") or 5)
    except ValueError:
        max_ops = 5.0
    return BrokerSettings(
        mode=mode,
        api_key=_env("UPSTOX_API_KEY"),
        api_secret=_env("UPSTOX_API_SECRET"),
        redirect_uri=_env("UPSTOX_REDIRECT_URI", "https://api-v2.upstox.com/redirect"),
        auth_code=_env("UPSTOX_AUTH_CODE"),
        access_token=_env("UPSTOX_ACCESS_TOKEN"),
        refresh_token=_env("UPSTOX_REFRESH_TOKEN"),
        token_file=Path(token_file) if token_file else DATA_DIR / "upstox_token.json",
        base_url=_env("UPSTOX_BASE_URL", "https://api-hft.upstox.com"),
        algo_id=_env("UPSTOX_ALGO_ID"),
        max_orders_per_sec=max_ops,
        instruments_file=instruments,
    )


class RateLimiter:
    """Token bucket that caps automated order flow under the SEBI 10-OPS threshold."""

    def __init__(self, max_per_second: float) -> None:
        self.rate = max(0.1, float(max_per_second))
        self.capacity = max(1.0, self.rate)
        self.tokens = self.capacity
        self.last = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            self.tokens = min(self.capacity, self.tokens + (now - self.last) * self.rate)
            self.last = now
            if self.tokens < 1.0:
                raise ExecutionBlockedError(
                    f"broker order rate limit exceeded ({self.rate:g} orders/sec cap; "
                    "SEBI <10 OPS registration threshold)"
                )
            self.tokens -= 1.0


class SessionManager:
    """Day-stamped token store enforcing the SEBI daily auto-logout lifecycle."""

    def __init__(self, cfg: BrokerSettings) -> None:
        self.cfg = cfg
        self._token: dict[str, Any] | None = None

    @staticmethod
    def _today() -> str:
        return datetime.now().date().isoformat()

    def _load(self) -> dict[str, Any] | None:
        if self._token is not None:
            return self._token
        f = self.cfg.token_file
        if not f.exists():
            return None
        try:
            self._token = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            self._token = None
        return self._token

    def _save(self, access: str, refresh: str) -> None:
        self._token = {"access_token": access, "refresh_token": refresh, "day": self._today()}
        try:
            self.cfg.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.cfg.token_file.write_text(
                json.dumps(self._token, indent=2), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 — persistence must never break the caller
            pass

    def store_token(self, access: str, refresh: str) -> None:
        self._save(access, refresh)

    def ensure_access_token(self) -> str:
        """Return a usable token for today, or fail closed on a stale/new-day session."""
        if self.cfg.mode == MODE_MOCK:
            return "mock-access-token"
        env_token = self.cfg.access_token
        if env_token:
            return env_token
        tok = self._load()
        if not tok or not tok.get("access_token"):
            raise ExecutionBlockedError(
                "no Upstox access token available; run the OAuth flow "
                "(set UPSTOX_ACCESS_TOKEN or complete login)."
            )
        if tok.get("day") != self._today():
            self.logout(tok["access_token"])
            raise ExecutionBlockedError(
                "new trading day detected: session auto-logged out per SEBI daily "
                "logout rule; re-authorize (refresh token) before placing orders."
            )
        return str(tok["access_token"])

    def logout(self, access_token: str) -> None:
        """Best-effort broker logout (daily auto-logout). Failures are non-fatal."""
        if not access_token or self.cfg.mode == MODE_MOCK:
            return
        try:
            with httpx.Client(timeout=15.0) as client:
                client.delete(
                    f"{self.cfg.base_url}/v3/logout",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Accept": "application/json",
                    },
                )
        except Exception:  # noqa: BLE001
            pass


class UpstoxClient:
    """Thin HTTP wrapper over Upstox v3 REST endpoints."""

    def __init__(self, cfg: BrokerSettings, token: str) -> None:
        self.cfg = cfg
        self.base = cfg.base_url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
            "Api-Version": "3.0",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=20.0) as client:
            resp = client.request(
                method,
                f"{self.base}{path}",
                headers=self.headers,
                json=json_body,
                params=params,
            )
        try:
            data = resp.json()
        except Exception:  # noqa: BLE001
            data = {"status": resp.status_code, "text": resp.text[:500]}
        if resp.status_code >= 400:
            raise ExecutionBlockedError(
                f"Upstox API {method} {path} -> HTTP {resp.status_code}: {json.dumps(data)}"
            )
        return data

    def place_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/v3/order/place", json_body=payload)

    def modify_order(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("PUT", "/v3/order/modify", json_body=payload)

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return self._request("DELETE", "/v3/order/cancel", params={"order_id": order_id})

    def order_details(self, order_id: str) -> dict[str, Any]:
        return self._request("GET", "/v3/order/details", params={"order_id": order_id})

    def order_history(self, tag: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if tag:
            params["tag"] = tag
        return self._request("GET", "/v3/order/history", params=params)

    def positions(self) -> dict[str, Any]:
        return self._request("GET", "/v3/portfolio/short-term-positions")

    def holdings(self) -> dict[str, Any]:
        return self._request("GET", "/v3/portfolio/long-term-holdings")

    def funds(self) -> dict[str, Any]:
        return self._request("GET", "/v3/user/get-funds-and-margin")

    def quotes(self, instrument_key: str) -> dict[str, Any]:
        """Live market quote (LTP, OHLC, volume, depth) for one instrument."""
        return self._request(
            "GET",
            "/v3/market-quote/quotes",
            params={"instrument_key": instrument_key},
        )

    def ohlc(self, instrument_key: str) -> dict[str, Any]:
        """OHLC quote endpoint for one instrument."""
        return self._request(
            "GET",
            "/v3/market-quote/ohlc",
            params={"instrument_key": instrument_key},
        )


# --------------------------------------------------------------------------- audit


def broker_audit_file() -> Path:
    return DATA_DIR / "broker_orders.jsonl"


def _append_broker_audit(record: dict[str, Any]) -> None:
    f = broker_audit_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        record["prev_hash"] = hashlib.sha256(prev.encode("utf-8")).hexdigest()
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass


def export_broker_audit(dest: str | Path) -> Path:
    """Export the broker audit trail to a retention-ready file (SEBI 5-year rule)."""
    src = broker_audit_file()
    out = Path(dest)
    out.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        out.write_bytes(src.read_bytes())
    else:
        out.write_text("", encoding="utf-8")
    return out


# --------------------------------------------------------------------------- instruments


def load_instruments(path: str | Path | None = None) -> list[dict[str, str]]:
    """Parse the broker-provided BOD instruments file (CSV). Empty if not configured."""
    p = Path(path) if path else None
    if p is None:
        cfg = get_broker_settings()
        p = Path(cfg.instruments_file) if cfg.instruments_file else None
    if not p or not p.exists():
        return []
    rows: list[dict[str, str]] = []
    try:
        with open(p, encoding="utf-8-sig", newline="") as fh:
            reader_lines = fh.read().splitlines()
        if not reader_lines:
            return []
        header = [h.strip().lstrip("\ufeff").lower() for h in reader_lines[0].split(",")]
        for line in reader_lines[1:]:
            cells = [c.strip() for c in line.split(",")]
            if not any(cells):
                continue
            if len(cells) < len(header):
                cells = cells + [""] * (len(header) - len(cells))
            rows.append(dict(zip(header, cells)))
    except Exception:  # noqa: BLE001
        return []
    return rows


def resolve_instrument(
    symbol: str,
    rows: list[dict[str, str]] | None = None,
) -> str | None:
    """Return the v3 instrument key (e.g. 'NSE_EQ|RELIANCE', 'NSE_FO|NIFTY ...')."""
    rows = rows if rows is not None else load_instruments()
    sym = symbol.strip().upper().replace(" ", "")
    for r in rows:
        for key in ("instrument_key", "trading_symbol", "symbol", "name"):
            val = str(r.get(key, "")).strip().upper().replace(" ", "")
            if val == sym or val.endswith(f"|{sym}") or val == f"{sym}|{sym}":
                return str(r.get("instrument_key") or r.get("trading_symbol") or r.get("symbol"))
    return None


# --------------------------------------------------------------------------- order lifecycle


class OrderManager:
    """High-level order lifecycle: gate -> rate-limit -> instrument -> send -> audit."""

    def __init__(self, cfg: BrokerSettings | None = None) -> None:
        self.cfg = cfg or get_broker_settings()
        self.limiter = RateLimiter(self.cfg.max_orders_per_sec)
        self.session = SessionManager(self.cfg)
        from .compliance import ComplianceEngine

        self.compliance = ComplianceEngine(
            mode=self.cfg.mode,
            api_key=self.cfg.api_key,
            algo_id=self.cfg.algo_id,
            is_live=self.cfg.is_live,
            limiter=self.limiter.acquire,
        )
        self._seq = 0
        self._seq_lock = threading.Lock()

    def _next_tag(self) -> str:
        with self._seq_lock:
            self._seq += 1
            ts = datetime.now().strftime("%Y%m%d%H%M%S")
            return f"{self.cfg.algo_id or 'sai'}-{ts}-{self._seq:05d}"

    def _gate(self, detail: str) -> None:
        action = "live_order" if self.cfg.is_live else "broker_sim"
        safety_gate(action, detail)

    def _require_ready(self) -> None:
        if self.cfg.mode == MODE_OFF:
            raise ExecutionBlockedError(
                "broker is OFF (UPSTOX_MODE unset). No order can reach a broker. "
                "Set UPSTOX_MODE=mock/sandbox/live to enable an execution path."
            )
        if not self.cfg.api_key:
            raise ExecutionBlockedError(
                "UPSTOX_API_KEY is not set; broker adapter is not configured."
            )

    def _build_payload(
        self,
        *,
        instrument_key: str,
        quantity: int,
        transaction_type: str,
        order_type: str = "MARKET",
        product: str = "I",
        price: float = 0.0,
        trigger_price: float = 0.0,
        validity: str = "DAY",
        disclosed_quantity: int = 0,
        tag: str,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "quantity": int(quantity),
            "product": product,
            "validity": validity,
            "price": round(float(price), 2),
            "tag": tag,
            "instrument_token": instrument_key,
            "order_type": order_type,
            "transaction_type": transaction_type,
            "trigger_price": round(float(trigger_price), 2),
        }
        if disclosed_quantity > 0:
            payload["disclosed_quantity"] = int(disclosed_quantity)
        if self.cfg.algo_id:
            payload["algo_id"] = self.cfg.algo_id
        return payload

    def _is_reducing(self, transaction_type: str, instrument_key: str, quantity: int) -> bool:
        """True when the order only reduces/closes an existing net position.

        Risk-reducing orders (SELL to close a long, BUY to cover a short) remain
        allowed while the daily-loss breaker is tripped; opening orders are blocked."""
        if self.cfg.mode == MODE_MOCK:
            net = expected_positions().get(instrument_key, 0)
        else:
            try:
                positions = self.portfolio().get("positions", {}).get("data") or []
                net = next((int(p.get("net_qty", 0)) for p in positions
                            if p.get("instrument_token") == instrument_key), 0)
            except Exception:  # noqa: BLE001
                net = 0
        if transaction_type == "SELL":
            return net > 0 and quantity <= net
        if transaction_type == "BUY":
            return net < 0 and quantity <= abs(net)
        return False

    def _send(
        self,
        payload: dict[str, Any],
        *,
        detail: str,
        transaction_type: str,
        instrument_key: str,
        quantity: int,
    ) -> dict[str, Any]:
        self._gate(detail)
        reducing = self._is_reducing(transaction_type, instrument_key, quantity)
        # Compliance engine: readiness + algo-ID + rate limit (+ daily loss unless reducing).
        self.compliance.pre_trade(detail, gate_daily_loss=not reducing)

        tag = payload["tag"]
        # Idempotency: if this tag already exists at the broker, return it (no re-send).
        if self.cfg.mode in (MODE_SANDBOX, MODE_LIVE):
            try:
                token = self.session.ensure_access_token()
                client = UpstoxClient(self.cfg, token)
                history = client.order_history(tag=tag)
                existing = history.get("data") or history.get("orders") or []
                if existing:
                    rec = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "mode": self.cfg.mode,
                        "action": "PLACE",
                        "tag": tag,
                        "order_id": existing[0].get("order_id"),
                        "status": "idempotent-hit",
                        "symbol": instrument_key,
                        "qty": quantity,
                        "transaction_type": transaction_type,
                    }
                    _append_broker_audit(rec)
                    return {"status": "idempotent-hit (no re-send)", "order_id": existing[0].get("order_id")}
            except ExecutionBlockedError as exc:
                raise exc

        if self.cfg.mode == MODE_MOCK:
            order_id = "MOCK-" + hashlib.sha256(tag.encode("utf-8")).hexdigest()[:12]
            result = {
                "status": "accepted",
                "order_id": order_id,
                "instrument_token": instrument_key,
                "quantity": quantity,
                "transaction_type": transaction_type,
                "order_type": payload["order_type"],
                "tag": tag,
                "mode": "mock (simulated broker, no real money)",
            }
            _append_broker_audit(
                {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "mode": self.cfg.mode,
                    "action": "PLACE",
                    "tag": tag,
                    "order_id": order_id,
                    "status": "accepted",
                    "symbol": instrument_key,
                    "qty": quantity,
                    "transaction_type": transaction_type,
                }
            )
            return result

        token = self.session.ensure_access_token()
        client = UpstoxClient(self.cfg, token)
        resp = client.place_order(payload)
        order_id = (resp.get("data") or {}).get("order_id") or resp.get("order_id")
        _append_broker_audit(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.cfg.mode,
                "action": "PLACE",
                "tag": tag,
                "order_id": order_id,
                "status": "sent",
                "symbol": instrument_key,
                "qty": quantity,
                "transaction_type": transaction_type,
                "raw": resp,
            }
        )
        return {"status": "sent", "order_id": order_id, "tag": tag, "raw": resp}

    def place(
        self,
        *,
        symbol: str,
        quantity: int,
        transaction_type: str,
        order_type: str = "MARKET",
        product: str = "I",
        price: float = 0.0,
        trigger_price: float = 0.0,
        instrument_rows: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if transaction_type not in TRANSACTION_TYPES:
            raise ValueError(f"transaction_type must be one of {TRANSACTION_TYPES}")
        if order_type not in ORDER_TYPES:
            raise ValueError(f"order_type must be one of {ORDER_TYPES}")
        if order_type in ("SL", "SL-M") and trigger_price <= 0:
            raise ValueError("trigger_price required for SL/SL-M orders")

        self._require_ready()

        rows = instrument_rows if instrument_rows is not None else load_instruments()
        key = resolve_instrument(symbol, rows)
        if not key:
            raise ExecutionBlockedError(
                f"cannot resolve instrument for {symbol!r} (broker instruments file "
                "missing or symbol not found). Refusing to send without an instrument key."
            )
        tag = self._next_tag()
        payload = self._build_payload(
            instrument_key=key,
            quantity=quantity,
            transaction_type=transaction_type,
            order_type=order_type,
            product=product,
            price=price,
            trigger_price=trigger_price,
            tag=tag,
        )
        return self._send(
            payload,
            detail=f"PLACE {transaction_type} {symbol} x {quantity} ({order_type}) via {self.cfg.mode}",
            transaction_type=transaction_type,
            instrument_key=key,
            quantity=quantity,
        )

    def modify(
        self,
        order_id: str,
        *,
        quantity: int | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
        order_type: str | None = None,
    ) -> dict[str, Any]:
        self._gate(f"MODIFY order {order_id} via {self.cfg.mode}")
        self.compliance.pre_trade(f"MODIFY order {order_id} via {self.cfg.mode}", gate_daily_loss=False)
        if not order_id:
            raise ValueError("order_id required")
        payload: dict[str, Any] = {"order_id": order_id}
        if quantity is not None:
            payload["quantity"] = int(quantity)
        if price is not None:
            payload["price"] = round(float(price), 2)
        if trigger_price is not None:
            payload["trigger_price"] = round(float(trigger_price), 2)
        if order_type is not None:
            payload["order_type"] = order_type
        if self.cfg.algo_id:
            payload["algo_id"] = self.cfg.algo_id

        if self.cfg.mode == MODE_MOCK:
            result = {"status": "modified", "order_id": order_id, "mode": "mock (simulated broker)"}
        else:
            token = self.session.ensure_access_token()
            result = UpstoxClient(self.cfg, token).modify_order(payload)
        _append_broker_audit(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.cfg.mode,
                "action": "MODIFY",
                "order_id": order_id,
                "status": "sent",
            }
        )
        return result

    def cancel(self, order_id: str) -> dict[str, Any]:
        self._gate(f"CANCEL order {order_id} via {self.cfg.mode}")
        self.compliance.pre_trade(f"CANCEL order {order_id} via {self.cfg.mode}", gate_daily_loss=False)
        if not order_id:
            raise ValueError("order_id required")
        if self.cfg.mode == MODE_MOCK:
            result = {"status": "cancelled", "order_id": order_id, "mode": "mock (simulated broker)"}
        else:
            token = self.session.ensure_access_token()
            result = UpstoxClient(self.cfg, token).cancel_order(order_id)
        _append_broker_audit(
            {
                "ts": datetime.now().isoformat(timespec="seconds"),
                "mode": self.cfg.mode,
                "action": "CANCEL",
                "order_id": order_id,
                "status": "sent",
            }
        )
        return result

    def order_status(self, order_id: str) -> dict[str, Any]:
        if self.cfg.mode == MODE_OFF:
            raise ExecutionBlockedError("broker is OFF (UPSTOX_MODE unset)")
        if not order_id:
            raise ValueError("order_id required")
        if self.cfg.mode == MODE_MOCK:
            return {"status": "mock-order", "order_id": order_id, "mode": "mock (simulated broker)"}
        token = self.session.ensure_access_token()
        return UpstoxClient(self.cfg, token).order_details(order_id)

    def portfolio(self) -> dict[str, Any]:
        if self.cfg.mode == MODE_OFF:
            raise ExecutionBlockedError("broker is OFF (UPSTOX_MODE unset)")
        if self.cfg.mode == MODE_MOCK:
            return {
                "positions": {
                    "data": [
                        {"instrument_token": sym, "net_qty": qty}
                        for sym, qty in expected_positions().items()
                    ]
                },
                "holdings": [],
                "mode": "mock (simulated broker)",
            }
        token = self.session.ensure_access_token()
        client = UpstoxClient(self.cfg, token)
        return {
            "positions": client.positions(),
            "holdings": client.holdings(),
            "funds": client.funds(),
        }

    def status(self) -> dict[str, Any]:
        """Read-only broker status; safe to expose to tools/UI without a gate."""
        return {
            "mode": self.cfg.mode,
            "ready": self.cfg.is_ready(),
            "live_trading": self.cfg.is_live,
            "algo_id": self.cfg.algo_id or None,
            "max_orders_per_sec": self.cfg.max_orders_per_sec,
            "instruments_loaded": len(load_instruments(self.cfg.instruments_file)) > 0,
        }


# --------------------------------------------------------------------------- reconciliation


def read_broker_audit() -> list[dict[str, Any]]:
    """Read all broker-order audit rows (append-only JSONL)."""
    f = broker_audit_file()
    if not f.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in f.read_text(encoding="utf-8").strip().splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception:  # noqa: BLE001
            continue
    return rows


def expected_positions(audit: list[dict[str, Any]] | None = None) -> dict[str, int]:
    """Replay the broker audit trail into the positions we EXPECT to hold.

    A PLACE with transaction_type BUY adds qty, SELL subtracts. A later CANCEL
    of the same order_id nets the original PLACE out. Orders whose status
    indicates rejection are ignored.
    """
    rows = audit if audit is not None else read_broker_audit()
    cancelled: set[str] = set()
    rejected: set[str] = set()
    for r in rows:
        if r.get("action") == "CANCEL":
            cancelled.add(str(r.get("order_id") or ""))
        if r.get("action") == "PLACE" and str(r.get("status", "")).lower() in (
            "rejected", "cancelled", "failed",
        ):
            rejected.add(str(r.get("order_id") or ""))

    net: dict[str, int] = {}
    for r in rows:
        if r.get("action") != "PLACE":
            continue
        oid = str(r.get("order_id") or "")
        if oid in cancelled or oid in rejected:
            continue
        sym = str(r.get("symbol") or "").strip()
        tx = str(r.get("transaction_type") or "").upper()
        if not sym or tx not in ("BUY", "SELL"):
            continue
        try:
            qty = int(r.get("qty") or 0)
        except (TypeError, ValueError):
            continue
        net[sym] = net.get(sym, 0) + (qty if tx == "BUY" else -qty)
    return {k: v for k, v in net.items() if v != 0}


def _normalise_broker_positions(pf: dict[str, Any]) -> dict[str, int]:
    """Flatten a broker portfolio response into {instrument: net_qty}."""
    out: dict[str, int] = {}
    raw = pf.get("positions") if isinstance(pf, dict) else {}
    items: list[Any] = []
    if isinstance(raw, dict):
        items = raw.get("data") or raw.get("positions") or []
    elif isinstance(raw, list):
        items = raw
    for it in items:
        if not isinstance(it, dict):
            continue
        tok = (it.get("instrument_token") or it.get("instrument_key")
               or it.get("trading_symbol") or "")
        if not tok:
            continue
        try:
            qty = int(it.get("net_qty") or it.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0
        out[str(tok)] = qty
    return {k: v for k, v in out.items() if v != 0}


def reconcile(cfg: BrokerSettings | None = None) -> dict[str, Any]:
    """Compare internal expected positions vs broker-reported positions.

    Fail-closed: if the broker is OFF this raises ExecutionBlockedError.
    Returns a verdict report and appends a hash-chained record to
    data/reconciliation.jsonl (retention-ready).
    """
    cfg = cfg or get_broker_settings()
    mgr = OrderManager(cfg)
    pf = mgr.portfolio()
    broker = _normalise_broker_positions(pf)
    expected = expected_positions()

    matched: list[dict[str, Any]] = []
    drift: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for sym in sorted(set(expected) | set(broker)):
        e, b = expected.get(sym, 0), broker.get(sym, 0)
        if sym in expected and sym in broker and e == b:
            matched.append({"symbol": sym, "expected": e, "broker": b})
        elif sym in expected and sym in broker:
            drift.append({"symbol": sym, "expected": e, "broker": b, "delta": b - e})
        elif sym in expected:
            missing.append({"symbol": sym, "expected": e, "broker": 0})
        else:
            unknown.append({"symbol": sym, "expected": 0, "broker": b})

    if drift or missing or unknown:
        verdict = "DRIFT"
    elif not (expected or broker):
        verdict = "FLAT"
    else:
        verdict = "MATCHED"

    report = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "mode": cfg.mode,
        "verdict": verdict,
        "matched": matched,
        "drift": drift,
        "missing": missing,
        "unknown": unknown,
    }
    _append_reconciliation(report)
    return report


def _append_reconciliation(record: dict[str, Any]) -> None:
    f = DATA_DIR / "reconciliation.jsonl"
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        prev = ""
        if f.exists():
            prev = f.read_text(encoding="utf-8").strip().splitlines()[-1]
        record["prev_hash"] = hashlib.sha256(prev.encode("utf-8")).hexdigest()
        with open(f, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001
        pass
