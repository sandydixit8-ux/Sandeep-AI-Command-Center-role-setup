"""Option Chain Intelligence engine for the AI Command Center.

Architecture:
    OptionChainProvider (abstract)
        -> NseOptionChainProvider  (live data from NSE India's public API)
        -> MockOptionChainProvider (deterministic demo fallback, offline)
    OptionChainDataService        -> fetch + validate + normalise a snapshot
    QualityEngine                 -> 🟢/🟡/🟠/🔴 data quality
    GreeksEngine                  -> Black-Scholes Greeks (labelled 'Calculated')
    ChainAnalytics                -> OI, OI change, PCR, IV, skew, term, max pain,
                                     expected move, unusual activity, liquidity
    ActivityClassifier            -> long/short buildup, covering, unwinding
    SupportResistance             -> OI-based zones (context, never guarantees)
    ScenarioEngine                -> bull/bear/range scenarios + invalidation
    SignalEngine                  -> composite Option Chain Signal Score
    StrategyLab                   -> strategy candidates + payoff + risk simulation
    OptionsPaperEngine            -> simulated options paper trading
    OptionsBacktest               -> historical strategy backtest

Data-safety principles honoured here (mirrors market.py):
- Option-chain data is evidence, never a crystal ball.
- Observed data / calculated metrics / AI interpretation are kept distinct.
- No high-confidence signal from incomplete or stale data.
- Option selling is never presented as "safe income".
- There is NO automated live options trading in this module.
"""
from __future__ import annotations

import json
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

import httpx

from .config import DATA_DIR

# --------------------------------------------------------------------------- constants

NSE_BASE = "https://www.nseindia.com"
NSE_HOME = f"{NSE_BASE}/option-chain"
NSE_CONTRACT_INFO = f"{NSE_BASE}/api/option-chain-contract-info"
NSE_CHAIN = f"{NSE_BASE}/api/option-chain-v3"

NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
}

NSE_TIMEOUT = 10.0
NSE_UNDERLYINGS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"}
# Approximate lot sizes (revised periodically by the exchange). Used for margin /
# notional estimates only and clearly marked as approximate.
LOT_SIZES: dict[str, int] = {"NIFTY": 75, "BANKNIFTY": 30, "FINNIFTY": 40, "SENSEX": 20}

RISK_FREE_RATE = 0.065  # approximate INR risk-free rate for Black-Scholes
TRADING_DAYS = 252


class OptionChainError(Exception):
    """Raised when an option-chain request fails with no usable fallback."""


def _now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def _is_valid_expiry(s: str) -> bool:
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            datetime.strptime(s.strip(), fmt)
            return True
        except ValueError:
            continue
    return False


# --------------------------------------------------------------------------- Greeks (Black-Scholes, European)

def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> float:
    """Black-Scholes option price. T in years. European exercise (NIFTY style)."""
    if T <= 0 or sigma <= 0:
        return max(0.0, (S - K) if is_call else (K - S))
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if is_call:
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def bs_greeks(S: float, K: float, T: float, r: float, sigma: float, is_call: bool) -> dict[str, float]:
    """Black-Scholes Greeks. Returns {} if inputs are degenerate."""
    if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
        return {}
    sq = math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * sq)
    d2 = d1 - sigma * sq
    pdf = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
    nd1 = _norm_cdf(d1)
    nd2 = _norm_cdf(d2)
    delta = (nd1 if is_call else nd1 - 1.0)
    gamma = pdf / (S * sigma * sq)
    vega = S * pdf * sq / 100.0  # per 1 point IV change
    theta = (
        -(S * pdf * sigma) / (2 * sq)
        - r * K * math.exp(-r * T) * (nd2 if is_call else _norm_cdf(-d2))
    ) / TRADING_DAYS
    rho = K * T * math.exp(-r * T) * (nd2 if is_call else -_norm_cdf(-d2)) / 100.0
    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 6),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
        "rho": round(rho, 4),
    }


def implied_vol(target: float, S: float, K: float, T: float, r: float, is_call: bool,
                lo: float = 0.05, hi: float = 2.0, tol: float = 1e-6) -> float:
    """Bisection implied volatility. Returns 0.0 if the target is not reachable."""
    if T <= 0:
        return 0.0
    intrinsic = max(0.0, (S - K) if is_call else (K - S))
    if target <= intrinsic or target <= 0:
        return 0.0
    for _ in range(80):
        mid = (lo + hi) / 2.0
        price = bs_price(S, K, T, r, mid, is_call)
        if price < target:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    return round((lo + hi) / 2.0, 4)


def days_to_expiry(expiry: str, today: datetime | None = None) -> int:
    """Calendar days between today and an expiry string (DD-MMM-YYYY or DD-MM-YYYY)."""
    t = today or datetime.now()
    for fmt in ("%d-%b-%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            d = datetime.strptime(expiry.strip(), fmt)
            return max(0, (d - t).days)
        except ValueError:
            continue
    return 0


# --------------------------------------------------------------------------- data model

@dataclass
class OptionContract:
    underlying: str
    exchange: str
    expiry: str
    strike: float
    option_type: str  # "CE" or "PE"
    contract_symbol: str
    lot_size: int
    ltp: float
    bid: float
    ask: float
    bid_qty: int
    ask_qty: int
    volume: int
    oi: int
    change_oi: int
    prev_close: float
    iv: float
    timestamp: str
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    greeks_source: str = "calculated"
    source: str = "nse"

    def to_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying, "exchange": self.exchange,
            "expiry": self.expiry, "strike": self.strike, "option_type": self.option_type,
            "contract_symbol": self.contract_symbol, "lot_size": self.lot_size,
            "ltp": self.ltp, "bid": self.bid, "ask": self.ask,
            "bid_qty": self.bid_qty, "ask_qty": self.ask_qty,
            "volume": self.volume, "oi": self.oi, "change_oi": self.change_oi,
            "prev_close": self.prev_close, "iv": self.iv, "timestamp": self.timestamp,
            "delta": self.delta, "gamma": self.gamma, "theta": self.theta,
            "vega": self.vega, "rho": self.rho,
            "greeks_source": self.greeks_source, "source": self.source,
        }


@dataclass
class ChainSnapshot:
    underlying: str
    spot: float
    futures: float | None
    expiry: str
    expiries: list[str]
    market_state: str
    timestamp: str
    quality: dict[str, Any]
    contracts: list[OptionContract]
    source: str
    raw_meta: dict[str, Any] = field(default_factory=dict)

    def strikes(self) -> list[float]:
        return sorted({c.strike for c in self.contracts})

    def by_strike(self) -> dict[float, dict[str, OptionContract]]:
        out: dict[float, dict[str, OptionContract]] = {}
        for c in self.contracts:
            out.setdefault(c.strike, {})[c.option_type] = c
        return out


# --------------------------------------------------------------------------- quality engine

class QualityEngine:
    """Validates a raw NSE chain payload. Produces a quality report."""

    CHECK_NAMES = [
        "timestamp", "missing_strikes", "duplicate_contracts", "invalid_prices",
        "negative_oi", "stale_quotes", "bid_ask", "expiry_validity",
        "contract_spec", "completeness",
    ]

    def __init__(self) -> None:
        self.checks: dict[str, dict[str, Any]] = {}
        self.issues: list[str] = []

    def _pass(self, name: str, detail: str) -> None:
        self.checks[name] = {"status": "ok", "detail": detail}

    def _warn(self, name: str, detail: str) -> None:
        self.checks[name] = {"status": "warn", "detail": detail}
        self.issues.append(f"{name}: {detail}")

    def _fail(self, name: str, detail: str) -> None:
        self.checks[name] = {"status": "fail", "detail": detail}
        self.issues.append(f"{name}: {detail}")

    def report(self, payload: dict[str, Any]) -> dict[str, Any]:
        rec = payload.get("records") or {}
        data = rec.get("data") or []
        ts_raw = str(rec.get("timestamp") or "")
        expiries = rec.get("expiryDates") or []

        if not ts_raw:
            self._fail("timestamp", "missing snapshot timestamp")
        else:
            try:
                parsed = datetime.strptime(ts_raw, "%d-%b-%Y %H:%M:%S")
                self._pass("timestamp", ts_raw)
                if (datetime.now() - parsed).total_seconds() > 6 * 3600:
                    self._warn("stale_quotes", f"timestamp {ts_raw} older than 6h")
            except ValueError:
                self._fail("timestamp", f"unparseable timestamp {ts_raw!r}")

        if not expiries:
            self._fail("expiry_validity", "no expiry dates returned")
        elif not any(_is_valid_expiry(e) for e in expiries[:3]):
            self._fail("expiry_validity", f"malformed expiries: {expiries[:3]}")
        else:
            self._pass("expiry_validity", f"{len(expiries)} expiries")

        strikes_seen: dict[float, int] = {}
        invalid = neg_oi = 0
        ce_count = pe_count = 0
        for row in data:
            strike = _safe_float(row.get("strikePrice"))
            ce, pe = row.get("CE"), row.get("PE")
            for side in (ce, pe):
                if not isinstance(side, dict):
                    continue
                if side.get("optionType") == "CE":
                    ce_count += 1
                elif side.get("optionType") == "PE":
                    pe_count += 1
                if _safe_float(side.get("lastPrice")) < 0:
                    invalid += 1
                if _safe_int(side.get("openInterest")) < 0:
                    neg_oi += 1
            strikes_seen[strike] = strikes_seen.get(strike, 0) + 1

        dup = sum(1 for v in strikes_seen.values() if v > 1)
        self._pass("invalid_prices", "all prices valid") if not invalid else self._warn("invalid_prices", f"{invalid} negative prices")
        self._pass("negative_oi", "all OI non-negative") if not neg_oi else self._warn("negative_oi", f"{neg_oi} negative OI")
        self._pass("duplicate_contracts", "no duplicates") if not dup else self._warn("duplicate_contracts", f"{dup} duplicate strikes")

        expected_strikes = len(rec.get("strikePrices") or [])
        if expected_strikes and len(strikes_seen) < max(60, expected_strikes * 0.5):
            self._warn("missing_strikes", f"{len(strikes_seen)}/{expected_strikes} strikes")
        else:
            self._pass("missing_strikes", f"{len(strikes_seen)} strikes")

        bad_ba = 0
        sampled = [c for row in data for c in (row.get("CE"), row.get("PE")) if isinstance(c, dict)][:200]
        for c in sampled:
            bid = _safe_float(c.get("buyPrice1"))
            ask = _safe_float(c.get("sellPrice1"))
            if bid > 0 and ask > 0 and bid > ask * 1.5:
                bad_ba += 1
        if bad_ba > max(2, len(sampled) // 10):
            self._warn("bid_ask", f"{bad_ba}/{len(sampled)} crossed bid-ask")
        else:
            self._pass("bid_ask", "bid <= ask on sampled contracts")

        both = sum(1 for row in data if isinstance(row.get("CE"), dict) and isinstance(row.get("PE"), dict))
        total_rows = max(1, len(data))
        if both / total_rows < 0.7 and total_rows > 5:
            self._warn("contract_spec", f"{both}/{total_rows} strikes with both CE+PE")
        else:
            self._pass("contract_spec", f"{ce_count} CE / {pe_count} PE · {both}/{total_rows} both")

        total = len(self.CHECK_NAMES)
        ok = sum(1 for c in self.checks.values() if c["status"] == "ok")
        warn = sum(1 for c in self.checks.values() if c["status"] == "warn")
        completeness = round(ok / total * 100) if total else 0
        if any(c["status"] == "fail" for c in self.checks.values()):
            status = "🔴 Unavailable"
        elif warn >= 2 or completeness < 70:
            status = "🟡 Partial"
        elif completeness < 100:
            status = "🟠 Delayed"
        else:
            status = "🟢 Complete"

        return {
            "status": status, "completeness": completeness,
            "checks": self.checks, "issues": self.issues,
            "timestamp": _now_str(),
        }


# --------------------------------------------------------------------------- providers

class OptionChainProvider(Protocol):
    name: str

    def get_chain(self, underlying: str, expiry: str | None = None) -> dict[str, Any]:
        """Return a raw chain payload with keys records/expiryDates/strikePrices/data."""
        ...

    def get_contract_info(self, underlying: str) -> dict[str, Any]:
        ...


class NseOptionChainProvider:
    """NSE India public option-chain API with cookie-session warmup.

    Flow:
        1. GET /option-chain with a desktop UA -> session cookies.
        2. GET /api/option-chain-contract-info?symbol=<SYMBOL> -> expiryDates, strikePrice.
        3. GET /api/option-chain-v3?type=Indices&symbol=<SYMBOL>&expiry=<EXPIRY> -> chain.
    """

    name = "nse"

    def __init__(self, base: str = NSE_BASE) -> None:
        self.base = base.rstrip("/")
        self.client = httpx.Client(
            headers=NSE_HEADERS,
            timeout=NSE_TIMEOUT,
            follow_redirects=True,
        )
        self.warmed = False
        self.last_fail_at: float = 0.0
        self.fail_cooldown = 60.0
        self.warmup_at: float = 0.0

    def _cooled_down(self) -> bool:
        return time.time() - self.last_fail_at > self.fail_cooldown

    def _warmup(self) -> None:
        if self.warmed and time.time() - self.warmup_at < 300:
            return
        if not self._cooled_down():
            raise OptionChainError("NSE is in rate-limit cooldown; retry later")
        try:
            r = self.client.get(f"{self.base}/option-chain")
            r.raise_for_status()
            self.warmed = True
            self.warmup_at = time.time()
        except Exception as exc:  # noqa: BLE001
            self.warmed = False
            self.last_fail_at = time.time()
            raise OptionChainError(f"NSE session warmup failed: {exc}") from exc

    def _get_json(self, url: str) -> dict[str, Any]:
        self._warmup()
        r = self.client.get(url)
        if r.status_code in (403, 404):
            self.warmed = False
            self.last_fail_at = time.time()
            raise OptionChainError(f"NSE {r.status_code} (blocked/rate-limited) for {url}")
        r.raise_for_status()
        try:
            return r.json()
        except Exception as exc:  # noqa: BLE001
            raise OptionChainError(f"NSE returned non-JSON for {url}: {exc}") from exc

    def get_contract_info(self, underlying: str) -> dict[str, Any]:
        return self._get_json(
            f"{self.base}/api/option-chain-contract-info?symbol={underlying}"
        )

    def get_chain(self, underlying: str, expiry: str | None = None) -> dict[str, Any]:
        if underlying not in NSE_UNDERLYINGS:
            raise OptionChainError(f"unsupported underlying {underlying!r}")
        info = self.get_contract_info(underlying)
        expiries = info.get("expiryDates") or []
        if not expiries:
            raise OptionChainError("NSE returned no expiries for the symbol")
        chosen = expiry or expiries[0]
        url = (f"{self.base}/api/option-chain-v3?type=Indices&symbol={underlying}"
               f"&expiry={chosen}")
        payload = self._get_json(url)
        payload.setdefault("_meta", {})["underlying"] = underlying
        payload["_meta"]["expiry"] = chosen
        payload["_meta"]["expiries"] = expiries
        payload["_meta"]["provider"] = "nse"
        return payload


class MockOptionChainProvider:
    """Deterministic offline fallback used when NSE is unreachable or rate-limited."""

    name = "mock"

    def __init__(self, seed: int = 42) -> None:
        self.seed = seed
        self.expiries = [
            "11-Aug-2026", "18-Aug-2026", "25-Aug-2026", "01-Sep-2026",
            "08-Sep-2026", "29-Sep-2026", "27-Oct-2026", "29-Dec-2026",
            "30-Mar-2027", "29-Jun-2027",
        ]

    def get_contract_info(self, underlying: str) -> dict[str, Any]:
        spot = self._spot(underlying)
        step = 50 if underlying in ("NIFTY", "BANKNIFTY", "FINNIFTY") else 100
        lo = int(spot // step) * step - 20 * step
        hi = int(spot // step) * step + 20 * step
        return {
            "expiryDates": self.expiries,
            "strikePrice": list(range(lo, hi + 1, step)),
        }

    def _spot(self, underlying: str) -> float:
        base = {"NIFTY": 24570.65, "BANKNIFTY": 53200.0, "FINNIFTY": 21750.0, "SENSEX": 81000.0}
        return base.get(underlying, 10000.0)

    def get_chain(self, underlying: str, expiry: str | None = None) -> dict[str, Any]:
        rng = random.Random(f"{underlying}-{expiry or ''}-{self.seed}")
        info = self.get_contract_info(underlying)
        expiries = info["expiryDates"]
        chosen = expiry or expiries[0]
        spot = self._spot(underlying)
        spot += rng.uniform(-0.4, 0.4)
        dte = days_to_expiry(chosen)
        strikes = info["strikePrice"]
        data = []
        now = datetime.now().strftime("%d-%b-%Y %H:%M:%S")
        for K in strikes:
            m = (K - spot) / spot
            base_iv = 0.14 + 0.06 * abs(m) + rng.uniform(-0.01, 0.01)
            row: dict[str, Any] = {"strikePrice": K}
            for side, sign in (("CE", 1.0), ("PE", -1.0)):
                iv = max(0.06, base_iv + (0.03 if side == "CE" else 0.0) + rng.uniform(-0.015, 0.015))
                T = max(dte, 1) / 365.0
                price = bs_price(spot, K, T, RISK_FREE_RATE, iv, side == "CE")
                oi = int(abs(m) * 3_500_000 * rng.uniform(0.4, 1.6) + rng.randint(5_000, 60_000))
                coi = int(oi * rng.uniform(-0.18, 0.22))
                row[side] = {
                    "optionType": side,
                    "underlying": underlying,
                    "expiryDate": chosen,
                    "strikePrice": K,
                    "lastPrice": round(price, 2),
                    "prevClose": round(price * rng.uniform(0.95, 1.05), 2),
                    "buyPrice1": round(price * rng.uniform(0.97, 1.0), 2),
                    "buyQuantity1": rng.randint(50, 900),
                    "sellPrice1": round(price * rng.uniform(1.0, 1.04), 2),
                    "sellQuantity1": rng.randint(50, 900),
                    "totalTradedVolume": rng.randint(100, 90_000),
                    "openInterest": oi,
                    "changeinOpenInterest": coi,
                    "pchangeinOpenInterest": round(coi / max(oi, 1) * 100, 2),
                    "impliedVolatility": round(iv, 4),
                    "underlyingValue": round(spot, 2),
                    "totalBuyQuantity": rng.randint(1_000, 60_000),
                    "totalSellQuantity": rng.randint(1_000, 60_000),
                    "lastUpdateTime": now,
                }
            data.append(row)
        return {
            "records": {
                "data": data,
                "timestamp": now,
                "underlyingValue": round(spot, 2),
                "expiryDates": expiries,
                "strikePrices": strikes,
                "marketDeptClose": False,
            },
            "filtered": {"data": data, "CE": 0, "PE": 0, "total": len(data)},
            "_meta": {
                "underlying": underlying, "expiry": chosen,
                "expiries": expiries, "provider": "mock",
                "fallback": True,
            },
        }


class OptionChainDataService:
    """Fetches a chain, validates it, normalises into a ChainSnapshot."""

    def __init__(self, provider: OptionChainProvider | None = None) -> None:
        self.provider = provider or NseOptionChainProvider()
        self.quality_engine = QualityEngine()

    @staticmethod
    def _normalise_side(side: dict[str, Any], underlying: str, expiry: str,
                        strike: float, lot: int, otype: str, source: str) -> OptionContract:
        ltp = _safe_float(side.get("lastPrice"))
        bid = _safe_float(side.get("buyPrice1"))
        ask = _safe_float(side.get("sellPrice1"))
        if bid <= 0:
            bid = max(0.0, ltp - 0.5)
        if ask <= 0:
            ask = ltp + 0.5 if ltp > 0 else 0.0
        iv = _safe_float(side.get("impliedVolatility"))
        if iv > 1.5:  # NSE reports IV as a percentage (e.g. 10.89 = 10.89%)
            iv /= 100.0
        if iv <= 0:
            T = max(days_to_expiry(expiry), 1) / 365.0
            iv = implied_vol(ltp, ltp * 1.0 if ltp else 100.0, strike, T, RISK_FREE_RATE, otype == "CE")
        spot = _safe_float(side.get("underlyingValue")) or 0.0
        return OptionContract(
            underlying=underlying,
            exchange="NFO",
            expiry=expiry,
            strike=strike,
            option_type=otype,
            contract_symbol=f"{underlying}{expiry.replace('-', '')[:7]}{int(strike)}{otype}",
            lot_size=lot,
            ltp=round(ltp, 2),
            bid=round(bid, 2),
            ask=round(ask, 2),
            bid_qty=_safe_int(side.get("buyQuantity1")),
            ask_qty=_safe_int(side.get("sellQuantity1")),
            volume=_safe_int(side.get("totalTradedVolume")),
            oi=_safe_int(side.get("openInterest")),
            change_oi=_safe_int(side.get("changeinOpenInterest")),
            prev_close=_safe_float(side.get("prevClose")),
            iv=round(iv, 4),
            timestamp=str(side.get("lastUpdateTime") or ""),
            source=source,
        )

    def fetch(self, underlying: str = "NIFTY", expiry: str | None = None,
              store: bool = True) -> ChainSnapshot:
        """Fetch + validate + normalise. Falls back to the mock provider on error."""
        source = self.provider.name
        try:
            payload = self.provider.get_chain(underlying, expiry)
            fallback = False
        except OptionChainError as exc:
            fallback = True
            source = "mock"
            payload = MockOptionChainProvider().get_chain(underlying, expiry)
            self._last_error = str(exc)

        rec = payload.get("records") or {}
        data = rec.get("data") or []
        meta = payload.get("_meta") or {}
        chosen = meta.get("expiry") or expiry or (rec.get("expiryDates") or [""])[0]
        expiries = meta.get("expiries") or rec.get("expiryDates") or []
        spot = _safe_float(rec.get("underlyingValue")) or 0.0
        if spot <= 0:
            spot = _safe_float(next(
                (c.get("underlyingValue") for r in data
                 for c in (r.get("CE"), r.get("PE")) if isinstance(c, dict) and c.get("underlyingValue")),
                0.0,
            ))
        lot = LOT_SIZES.get(underlying, 75)

        contracts: list[OptionContract] = []
        for row in data:
            strike = _safe_float(row.get("strikePrice"))
            for side, otype in ((row.get("CE"), "CE"), (row.get("PE"), "PE")):
                if not isinstance(side, dict):
                    continue
                contracts.append(self._normalise_side(
                    side, underlying, chosen, strike, lot, otype, source))
        contracts.sort(key=lambda c: (c.strike, c.option_type))

        spot = round(spot, 2)
        self._attach_greeks(contracts, spot)

        quality = self.quality_engine.report(payload)
        return ChainSnapshot(
            underlying=underlying,
            spot=spot,
            futures=None,
            expiry=chosen,
            expiries=expiries,
            market_state="LIVE" if not fallback else "FALLBACK",
            timestamp=str(rec.get("timestamp") or _now_str()),
            quality=quality,
            contracts=contracts,
            source=source,
            raw_meta={"fallback": fallback, "error": getattr(self, "_last_error", "")},
        )

    def _attach_greeks(self, contracts: list[OptionContract], spot: float) -> None:
        for c in contracts:
            T = max(days_to_expiry(c.expiry), 1) / 365.0
            g = bs_greeks(spot, c.strike, T, RISK_FREE_RATE, max(c.iv, 0.001), c.option_type == "CE")
            c.delta = g.get("delta")
            c.gamma = g.get("gamma")
            c.theta = g.get("theta")
            c.vega = g.get("vega")
            c.rho = g.get("rho")
            c.greeks_source = "calculated"

    def snapshot_to_dict(self, snap: ChainSnapshot) -> dict[str, Any]:
        return {
            "underlying": snap.underlying,
            "spot": snap.spot,
            "futures": snap.futures,
            "expiry": snap.expiry,
            "expiries": snap.expiries,
            "market_state": snap.market_state,
            "timestamp": snap.timestamp,
            "quality": snap.quality,
            "source": snap.source,
            "contracts": [c.to_dict() for c in snap.contracts],
        }

    # -- snapshot persistence -------------------------------------------------

    def _snapshot_path(self, underlying: str) -> str:
        snap_dir = DATA_DIR / "option_chain_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        return str(snap_dir / f"{underlying}.json")

    def store(self, snap: ChainSnapshot) -> str:
        path = self._snapshot_path(snap.underlying)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.snapshot_to_dict(snap), fh, indent=2)
        return path

    def load(self, underlying: str) -> dict[str, Any] | None:
        path = self._snapshot_path(underlying)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def list_snapshots(self) -> list[dict[str, Any]]:
        snap_dir = DATA_DIR / "option_chain_snapshots"
        if not snap_dir.exists():
            return []
        out = []
        for p in sorted(snap_dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "underlying": d.get("underlying"),
                    "timestamp": d.get("timestamp"),
                    "source": d.get("source"),
                    "quality": d.get("quality", {}).get("status"),
                    "path": str(p),
                })
            except (OSError, ValueError):
                continue
        return out


# --------------------------------------------------------------------------- analytics

class ChainAnalytics:
    """Calculated metrics over a normalised ChainSnapshot."""

    def __init__(self, snap: ChainSnapshot) -> None:
        self.snap = snap
        self.by = snap.by_strike()
        self.spot = snap.spot

    def _atm_strike(self) -> float:
        strikes = self.snap.strikes()
        if not strikes:
            return 0.0
        return min(strikes, key=lambda s: abs(s - self.spot))

    def oi_summary(self) -> dict[str, Any]:
        ce_oi = sum(c.oi for c in self.snap.contracts if c.option_type == "CE")
        pe_oi = sum(c.oi for c in self.snap.contracts if c.option_type == "PE")
        ce_coi = sum(c.change_oi for c in self.snap.contracts if c.option_type == "CE")
        pe_coi = sum(c.change_oi for c in self.snap.contracts if c.option_type == "PE")
        atm = self._atm_strike()
        return {
            "total_ce_oi": ce_oi,
            "total_pe_oi": pe_oi,
            "ce_change_oi": ce_coi,
            "pe_change_oi": pe_coi,
            "pcr_oi": round(pe_oi / ce_oi, 3) if ce_oi else None,
            "atm_strike": atm,
            "atm_ce_oi": self.by.get(atm, {}).get("CE").oi if self.by.get(atm, {}).get("CE") else 0,
            "atm_pe_oi": self.by.get(atm, {}).get("PE").oi if self.by.get(atm, {}).get("PE") else 0,
        }

    def pcr(self) -> dict[str, Any]:
        oi = self.oi_summary()
        pe_vol = sum(c.volume for c in self.snap.contracts if c.option_type == "PE")
        ce_vol = sum(c.volume for c in self.snap.contracts if c.option_type == "CE")
        return {
            "pcr_oi": oi["pcr_oi"],
            "pcr_volume": round(pe_vol / ce_vol, 3) if ce_vol else None,
            "note": "PCR is a positioning gauge, not a timing signal",
        }

    def iv_analysis(self) -> dict[str, Any]:
        atm = self._atm_strike()
        ce_ivs = [c.iv for c in self.snap.contracts if c.option_type == "CE" and c.iv > 0]
        pe_ivs = [c.iv for c in self.snap.contracts if c.option_type == "PE" and c.iv > 0]
        atm_ce = self.by.get(atm, {}).get("CE")
        atm_pe = self.by.get(atm, {}).get("PE")

        def reg(strikes: list[float], ivs: list[float]) -> float:
            if len(strikes) < 2 or len(ivs) < 2:
                return 0.0
            n = len(strikes)
            mx, my = sum(strikes) / n, sum(ivs) / n
            num = sum((x - mx) * (y - my) for x, y in zip(strikes, ivs))
            den = sum((x - mx) ** 2 for x in strikes)
            return (num / den) if den else 0.0

        n = 5
        strikes = sorted(self.snap.strikes())
        atm_i = strikes.index(atm) if atm in strikes else len(strikes) // 2
        window = strikes[max(0, atm_i - n): atm_i + n + 1]
        ce_pairs = [(c.strike, c.iv) for c in self.snap.contracts
                    if c.option_type == "CE" and c.strike in window and c.iv > 0]
        pe_pairs = [(c.strike, c.iv) for c in self.snap.contracts
                    if c.option_type == "PE" and c.strike in window and c.iv > 0]
        skew_ce = reg([p[0] for p in ce_pairs], [p[1] for p in ce_pairs])
        skew_pe = reg([p[0] for p in pe_pairs], [p[1] for p in pe_pairs])

        all_iv = [c.iv for c in self.snap.contracts if c.iv > 0]
        ivx = atm_ce.iv if atm_ce else None
        ivy = atm_pe.iv if atm_pe else None
        spread = abs(ivx - ivy) if ivx is not None and ivy is not None else None

        regime = "HV"
        if ivx:
            if ivx < 0.12:
                regime = "LOW"
            elif ivx > 0.25:
                regime = "HIGH"

        return {
            "atm_iv": round(ivx or 0.0, 4) if ivx else None,
            "atm_put_iv": round(ivy or 0.0, 4) if ivy else None,
            "iv_spread": round(spread, 4) if spread is not None else None,
            "avg_iv": round(sum(all_iv) / len(all_iv), 4) if all_iv else None,
            "regime": regime,
            "skew_ce_slope": round(skew_ce, 6),
            "skew_pe_slope": round(skew_pe, 6),
            "note": "IV regime is descriptive; selling options in HIGH IV is not automatically safe",
        }

    def term_structure(self) -> dict[str, Any]:
        """ATM IV across expiries for the selected underlying."""
        rows = []
        for exp in self.snap.expiries[:5]:
            try:
                sub = OptionChainDataService().fetch(self.snap.underlying, exp, store=False)
            except Exception:  # noqa: BLE001
                continue
            atms = sub.by_strike()
            atm_k = min(sub.strikes(), key=lambda s: abs(s - sub.spot))
            ce = atms.get(atm_k, {}).get("CE")
            rows.append({
                "expiry": exp,
                "dte": days_to_expiry(exp),
                "atm_iv": round(ce.iv, 4) if ce else None,
            })
        rows.sort(key=lambda r: r["dte"])
        if len(rows) >= 2 and all(r["atm_iv"] for r in rows):
            shape = "BACKWARDATED" if rows[-1]["atm_iv"] < rows[0]["atm_iv"] else "CONTANGO"
        else:
            shape = "UNKNOWN"
        return {"shape": shape, "points": rows}

    def max_pain(self) -> dict[str, Any]:
        """Strike where net option liability is smallest (est.). Uses OI-weighted sums."""
        best: tuple[float, float] | None = None
        for K, row in self.by.items():
            ce = row.get("CE")
            pe = row.get("PE")
            total = 0.0
            for c in self.snap.contracts:
                if c.option_type == "CE":
                    total += max(0.0, c.ltp) * c.oi * (K - c.strike if c.strike <= K else 0)
                else:
                    total += max(0.0, c.ltp) * c.oi * (c.strike - K if c.strike >= K else 0)
            if best is None or total < best[1]:
                best = (K, total)
        k, liability = best if best else (0.0, 0.0)
        return {
            "max_pain": k,
            "est_liability": round(liability, 2),
            "spot": self.spot,
            "distance_pct": round((self.spot - k) / self.spot * 100, 2) if self.spot else None,
            "note": "Max pain is an estimate from OI; it is not a price prediction",
        }

    def expected_move(self, days: int = 1) -> dict[str, Any]:
        iv = self.iv_analysis().get("atm_iv")
        if not iv:
            return {"error": "no ATM IV to estimate a move"}
        sd = iv * self.spot * math.sqrt(days / TRADING_DAYS)
        return {
            "days": days,
            "atm_iv": iv,
            "lower": round(self.spot - sd, 2),
            "upper": round(self.spot + sd, 2),
            "label": "Implied statistical range — not a price prediction",
        }

    def unusual_activity(self, min_vol_oi: float = 2.0, min_oi: int = 100_000) -> list[dict[str, Any]]:
        """Volume-to-OI spikes. Evidence list, never a trade recommendation."""
        rows = []
        for c in self.snap.contracts:
            if c.oi < min_oi:
                continue
            if c.volume <= 0:
                continue
            ratio = c.volume / max(c.oi, 1)
            if ratio >= min_vol_oi:
                rows.append({
                    "strike": c.strike,
                    "option_type": c.option_type,
                    "contract": c.contract_symbol,
                    "volume": c.volume,
                    "oi": c.oi,
                    "vol_oi_ratio": round(ratio, 2),
                    "change_oi": c.change_oi,
                    "iv": c.iv,
                    "ltp": c.ltp,
                })
        rows.sort(key=lambda r: -r["vol_oi_ratio"])
        return rows[:10]

    def liquidity(self) -> dict[str, Any]:
        """Overall and ATM liquidity gauge."""
        atm = self._atm_strike()
        traded = [c for c in self.snap.contracts if c.volume > 0]
        ce_vol = sum(c.volume for c in traded if c.option_type == "CE")
        pe_vol = sum(c.volume for c in traded if c.option_type == "PE")
        total_vol = ce_vol + pe_vol
        atm_ce = self.by.get(atm, {}).get("CE")
        atm_pe = self.by.get(atm, {}).get("PE")
        spread = None
        if atm_ce and atm_pe:
            spread = (atm_ce.ask - atm_ce.bid + atm_pe.ask - atm_pe.bid) / 2
        if total_vol == 0:
            grade = "LOW"
        elif total_vol < 500_000:
            grade = "LOW"
        elif total_vol < 2_000_000:
            grade = "MEDIUM"
        else:
            grade = "HIGH"
        return {
            "total_volume": total_vol,
            "ce_volume": ce_vol,
            "pe_volume": pe_vol,
            "atm_spread": round(spread, 2) if spread is not None else None,
            "grade": grade,
            "strike_count": len(self.by),
        }

    def all(self) -> dict[str, Any]:
        return {
            "oi": self.oi_summary(),
            "pcr": self.pcr(),
            "iv": self.iv_analysis(),
            "max_pain": self.max_pain(),
            "expected_move": self.expected_move(),
            "unusual_activity": self.unusual_activity(),
            "liquidity": self.liquidity(),
        }


class ActivityClassifier:
    """OI + price based activity classification. Heuristic — evidence strength shown."""

    def classify(self, change_oi: float, oi: float, price_change_pct: float,
                 volume: float) -> dict[str, Any]:
        if oi <= 0:
            return {"activity": "UNKNOWN", "strength": "WEAK"}
        if volume <= 0:
            volume = 1
        avg = abs(price_change_pct)
        if avg <= 0.05:
            strength = "STRONG"
        elif avg <= 0.5:
            strength = "MODERATE"
        else:
            strength = "WEAK"
        if change_oi > 0 and price_change_pct > 0:
            activity = "LONG_BUILDUP"
        elif change_oi > 0 and price_change_pct < 0:
            activity = "SHORT_BUILDUP"
        elif change_oi < 0 and price_change_pct > 0:
            activity = "SHORT_COVERING"
        elif change_oi < 0 and price_change_pct < 0:
            activity = "LONG_UNWINDING"
        else:
            activity = "NEUTRAL"
            strength = "WEAK"
        return {"activity": activity, "strength": strength}


class SupportResistance:
    """OI-cluster zones. Presented as context with confidence, never guarantees."""

    def __init__(self, snap: ChainSnapshot) -> None:
        self.snap = snap
        self.by = snap.by_strike()

    def zones(self, window: int = 5) -> dict[str, Any]:
        ce_oi = {c.strike: c.oi for c in self.snap.contracts if c.option_type == "CE"}
        pe_oi = {c.strike: c.oi for c in self.snap.contracts if c.option_type == "PE"}
        strikes = self.snap.strikes()

        def smooth(d: dict[float, int], w: int) -> dict[float, float]:
            out: dict[float, float] = {}
            for k in strikes:
                vals = [d.get(s, 0) for s in range(int(k - w * 50), int(k + w * 50) + 1, 50)]
                out[k] = sum(vals)
            return out

        ce_s, pe_s = smooth(ce_oi, window), smooth(pe_oi, window)

        def pick_below(which: dict[float, float], cap: int = 6) -> list[float]:
            below = [s for s in strikes if s < self.snap.spot]
            below.sort(key=lambda s: -which[s])
            return below[:cap]

        def pick_above(which: dict[float, float], cap: int = 6) -> list[float]:
            above = [s for s in strikes if s > self.snap.spot]
            above.sort(key=lambda s: -which[s])
            return above[:cap]

        support = [
            {"strike": s, "oi": ce_s[s],
             "distance_pct": round((self.snap.spot - s) / self.snap.spot * 100, 2)}
            for s in pick_below(ce_s)
        ]
        resistance = [
            {"strike": s, "oi": pe_s[s],
             "distance_pct": round((s - self.snap.spot) / self.snap.spot * 100, 2)}
            for s in pick_above(pe_s)
        ]
        return {
            "support": support,
            "resistance": resistance,
            "confidence": self._confidence(support, resistance),
            "note": "OI clusters are probabilities, not price floors/ceilings",
        }

    def _confidence(self, support: list[dict], resistance: list[dict]) -> str:
        s_oi = sum(x["oi"] for x in support)
        r_oi = sum(x["oi"] for x in resistance)
        total = s_oi + r_oi
        if total == 0:
            return "LOW"
        concentration = max(s_oi, r_oi) / total
        if concentration > 0.65:
            return "HIGH"
        if concentration > 0.5:
            return "MODERATE"
        return "LOW"


# --------------------------------------------------------------------------- scenario + signal

class ScenarioEngine:
    """Bull / bear / range scenarios with an invalidation for each."""

    def __init__(self, snap: ChainSnapshot, analytics: dict[str, Any],
                 srz: dict[str, Any]) -> None:
        self.snap = snap
        self.a = analytics
        self.srz = srz

    def scenarios(self) -> list[dict[str, Any]]:
        s = self.snap.spot
        iv = (self.a.get("iv") or {}).get("atm_iv") or 0.15
        mv = (self.a.get("expected_move") or {})
        lo, hi = mv.get("lower"), mv.get("upper")
        sup = self.srz.get("support", [])
        res = self.srz.get("resistance", [])
        sup1 = sup[0]["strike"] if sup else s - 0.5 / 100 * s
        res1 = res[0]["strike"] if res else s + 0.5 / 100 * s
        if not lo:
            lo, hi = s - 0.008 * s, s + 0.008 * s
        return [
            {
                "name": "Bullish",
                "target": res1,
                "invalidated_below": min(sup1, lo),
                "prob_word": "possible",
                "evidence": [
                    f"ATM IV {iv*100:.1f}%",
                    f"OI support at {sup1:,.0f}" if sup else "support OI light",
                ],
                "label": "Scenario — not a prediction",
            },
            {
                "name": "Bearish",
                "target": sup1,
                "invalidated_above": max(res1, hi),
                "prob_word": "possible",
                "evidence": [
                    f"ATM IV {iv*100:.1f}%",
                    f"OI resistance at {res1:,.0f}" if res else "resistance OI light",
                ],
                "label": "Scenario — not a prediction",
            },
            {
                "name": "Range-bound",
                "target": round((lo + hi) / 2, 2),
                "invalidated_below": lo,
                "invalidated_above": hi,
                "prob_word": "expected range",
                "evidence": [f"implied range {lo:,.0f}–{hi:,.0f}"],
                "label": "Scenario — not a prediction",
            },
        ]


class SignalEngine:
    """Composite Option Chain Signal Score. Documented, configurable weights.

    Weights default to: positioning 0.30, volatility 0.20, activity 0.20,
    momentum 0.15, liquidity 0.15. Output is 0-100 score with a label and
    qualifiers. Never a buy/sell call.
    """

    WEIGHTS = {"positioning": 0.30, "volatility": 0.20, "activity": 0.20,
               "momentum": 0.15, "liquidity": 0.15}

    def __init__(self, snap: ChainSnapshot, analytics: dict[str, Any]) -> None:
        self.snap = snap
        self.a = analytics

    def _positioning(self) -> float:
        pcr = (self.a.get("pcr") or {}).get("pcr_oi")
        if pcr is None:
            return 0.5
        return 0.5 + 0.5 * math.tanh((pcr - 0.9) / 0.4)

    def _volatility(self) -> float:
        iv = (self.a.get("iv") or {}).get("atm_iv")
        if not iv:
            return 0.5
        return max(0.0, 1 - abs(iv - 0.15) / 0.2)

    def _activity(self) -> float:
        ua = self.a.get("unusual_activity") or []
        if not ua:
            return 0.5
        buys = sum(1 for x in ua if x["change_oi"] > 0 and x["option_type"] == "CE")
        puts = sum(1 for x in ua if x["change_oi"] > 0 and x["option_type"] == "PE")
        total = max(1, buys + puts)
        return 0.5 + 0.4 * (buys - puts) / total

    def _momentum(self) -> float:
        ce = sum(c.change_oi for c in self.snap.contracts if c.option_type == "CE")
        pe = sum(c.change_oi for c in self.snap.contracts if c.option_type == "PE")
        if abs(ce) + abs(pe) == 0:
            return 0.5
        return 0.5 + 0.4 * (pe - ce) / (abs(ce) + abs(pe))

    def _liquidity(self) -> float:
        g = (self.a.get("liquidity") or {}).get("grade")
        return {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.2}.get(g, 0.4)

    def score(self) -> dict[str, Any]:
        parts = {
            "positioning": self._positioning(),
            "volatility": self._volatility(),
            "activity": self._activity(),
            "momentum": self._momentum(),
            "liquidity": self._liquidity(),
        }
        raw = sum(parts[k] * self.WEIGHTS[k] for k in parts)
        score = int(round(raw * 100))
        if score >= 70:
            label = "Strongly Bullish"
        elif score >= 58:
            label = "Mildly Bullish"
        elif score >= 42:
            label = "Neutral"
        elif score >= 30:
            label = "Mildly Bearish"
        else:
            label = "Strongly Bearish"
        quality = self.snap.quality.get("status", "🔴 Unavailable")
        confidence = "LOW" if quality in ("🔴 Unavailable", "🟡 Partial") else "MODERATE"
        if score >= 70 or score <= 30:
            confidence = "LOW"  # extremes in a signal score are not high confidence
        return {
            "score": score,
            "label": label,
            "confidence": confidence,
            "components": {k: round(v, 3) for k, v in parts.items()},
            "weights": dict(self.WEIGHTS),
            "data_quality": quality,
            "disclaimer": "Composite signal from option-chain data only. Not a buy/sell call.",
        }


# --------------------------------------------------------------------------- strategy lab

class StrategyLab:
    """Evaluates common option structures given an expiry snapshot."""

    STRATEGIES = ["long_call", "long_put", "bull_call_spread", "bear_put_spread",
                  "strangle", "iron_condor", "covered_call"]

    def __init__(self, snap: ChainSnapshot, spot: float | None = None,
                 analytics: dict[str, Any] | None = None) -> None:
        self.snap = snap
        self.spot = spot or snap.spot
        self.a = analytics or ChainAnalytics(snap).all()

    def _atm(self) -> float:
        return min(self.snap.strikes(), key=lambda s: abs(s - self.spot))

    def _contract(self, strike: float, otype: str) -> OptionContract | None:
        return self.snap.by_strike().get(strike, {}).get(otype)

    def _structure(self, name: str) -> dict[str, Any]:
        atm = self._atm()
        step = max(50, (self.snap.strikes()[1] - self.snap.strikes()[0]) if len(self.snap.strikes()) > 1 else 50)
        iv = (self.a.get("iv") or {}).get("atm_iv") or 0.15
        t = max(days_to_expiry(self.snap.expiry), 1) / 365.0
        lot = self.snap.contracts[0].lot_size if self.snap.contracts else 75

        legs: list[dict[str, Any]] = []
        if name == "long_call":
            legs = [{"leg": "Buy", "strike": atm, "type": "CE"}]
        elif name == "long_put":
            legs = [{"leg": "Buy", "strike": atm, "type": "PE"}]
        elif name == "bull_call_spread":
            legs = [{"leg": "Buy", "strike": atm, "type": "CE"},
                    {"leg": "Sell", "strike": atm + step, "type": "CE"}]
        elif name == "bear_put_spread":
            legs = [{"leg": "Buy", "strike": atm, "type": "PE"},
                    {"leg": "Sell", "strike": atm - step, "type": "PE"}]
        elif name == "strangle":
            legs = [{"leg": "Buy", "strike": atm + step, "type": "CE"},
                    {"leg": "Buy", "strike": atm - step, "type": "PE"}]
        elif name == "iron_condor":
            legs = [{"leg": "Sell", "strike": atm + step, "type": "CE"},
                    {"leg": "Buy", "strike": atm + 2 * step, "type": "CE"},
                    {"leg": "Sell", "strike": atm - step, "type": "PE"},
                    {"leg": "Buy", "strike": atm - 2 * step, "type": "PE"}]
        elif name == "covered_call":
            legs = [{"leg": "Hold", "strike": self.spot, "type": "SPOT"},
                    {"leg": "Sell", "strike": atm + step, "type": "CE"}]

        cost = 0.0
        for l in legs:
            if l["type"] == "SPOT":
                l["premium"] = self.spot
                l["cash"] = -self.spot * lot
                cost += l["premium"]
                continue
            c = self._contract(l["strike"], l["type"])
            premium = c.ltp if c else bs_price(self.spot, l["strike"], t, RISK_FREE_RATE, iv, l["type"] == "CE")
            l["premium"] = round(premium, 2)
            l["cash"] = (-premium if l["leg"] == "Buy" else premium) * lot
            cost += l["premium"] * (1 if l["leg"] == "Buy" else -1)

        net = -cost * lot  # cash outlay (negative = debit)
        return {"name": name, "legs": legs, "net_premium": round(cost, 2),
                "net_cash": round(net, 2), "lot_size": lot}

    def evaluate(self, name: str) -> dict[str, Any]:
        spec = self._structure(name)
        legs = spec["legs"]
        atm = self._atm()
        step = max(50, (self.snap.strikes()[1] - self.snap.strikes()[0]) if len(self.snap.strikes()) > 1 else 50)
        iv = (self.a.get("iv") or {}).get("atm_iv") or 0.15
        t = max(days_to_expiry(self.snap.expiry), 1) / 365.0
        lot = spec["lot_size"]

        def payoff_at(S: float) -> float:
            total = 0.0
            for l in legs:
                if l["type"] == "SPOT":
                    total += (S - l["strike"]) * lot
                    continue
                c = self._contract(l["strike"], l["type"])
                prem = c.ltp if c else bs_price(self.spot, l["strike"], t, RISK_FREE_RATE, iv, l["type"] == "CE")
                if l["type"] == "CE":
                    intrinsic = max(0.0, S - l["strike"])
                else:
                    intrinsic = max(0.0, l["strike"] - S)
                if l["leg"] == "Buy":
                    total += (intrinsic - prem) * lot
                else:
                    total += (prem - intrinsic) * lot
            return total

        lo = self.spot * 0.97
        hi = self.spot * 1.03
        x = [round(lo + i * (hi - lo) / 120, 2) for i in range(121)]
        y = [payoff_at(s) for s in x]
        profit = [v for v in y if v > 0]
        max_profit = max(y)
        max_loss = min(y)
        bps = [x[i] for i in range(1, len(y) - 1) if y[i - 1] * y[i] <= 0]

        # margin note: only sold legs have margin
        sold = [l for l in legs if l["leg"] in ("Sell",) and l["type"] != "SPOT"]
        margin = sum(l["premium"] * lot for l in sold) + (0.2 * self.spot * lot if sold else 0.0)
        return {
            "name": name,
            "display": name.replace("_", " ").title(),
            "legs": spec["legs"],
            "net_premium": spec["net_premium"],
            "net_cash": spec["net_cash"],
            "max_profit": round(max_profit, 2),
            "max_loss": round(max_loss, 2),
            "breakevens": [round(b, 2) for b in bps],
            "est_margin": round(margin, 2),
            "payoff": {"x": x, "y": y},
            "risk_label": "DEFINED" if min(l["leg"] for l in legs) != "Hold" else "UNDEFINED",
            "note": "Margin is an estimate. Verify with your broker before trading.",
        }

    def suggest(self) -> list[dict[str, Any]]:
        sig = SignalEngine(self.snap, self.a).score()
        label = sig["label"]
        cands: list[str] = []
        if "Bullish" in label:
            cands = ["long_call", "bull_call_spread", "covered_call"]
        elif "Bearish" in label:
            cands = ["long_put", "bear_put_spread"]
        else:
            cands = ["strangle", "iron_condor"]
        return [self.evaluate(c) for c in cands]


# --------------------------------------------------------------------------- paper trading + backtest

@dataclass
class OptionsPosition:
    id: str
    underlying: str
    expiry: str
    strike: float
    option_type: str
    action: str  # BUY / SELL
    quantity: int
    entry_price: float
    entered_at: str
    status: str = "OPEN"
    mark_price: float = 0.0
    exit_price: float | None = None
    exited_at: str | None = None
    pnl: float = 0.0


class OptionsPaperEngine:
    """Simulated options trading. Positions are paper only — no live orders."""

    def __init__(self, data_dir=DATA_DIR) -> None:
        self.data_dir = data_dir
        self.positions_file = data_dir / "options_paper_positions.json"
        self._positions: list[OptionsPosition] = []
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.positions_file.read_text(encoding="utf-8"))
            self._positions = [OptionsPosition(**p) for p in raw]
        except (OSError, ValueError, TypeError):
            self._positions = []

    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.positions_file.write_text(
            json.dumps([p.__dict__ for p in self._positions], indent=2),
            encoding="utf-8",
        )

    def open(self, underlying: str, expiry: str, strike: float, option_type: str,
             action: str, quantity: int, entry_price: float) -> OptionsPosition:
        pos = OptionsPosition(
            id=f"PP{int(time.time()*1000)}",
            underlying=underlying, expiry=expiry, strike=strike,
            option_type=option_type, action=action, quantity=quantity,
            entry_price=entry_price, entered_at=_now_str(),
        )
        self._positions.append(pos)
        self._save()
        return pos

    def mark(self, id: str, mark_price: float) -> OptionsPosition | None:
        for p in self._positions:
            if p.id == id:
                p.mark_price = mark_price
                p.pnl = (mark_price - p.entry_price) * p.quantity if p.action == "BUY" \
                    else (p.entry_price - mark_price) * p.quantity
                self._save()
                return p
        return None

    def close(self, id: str, exit_price: float) -> OptionsPosition | None:
        for p in self._positions:
            if p.id == id and p.status == "OPEN":
                p.exit_price = exit_price
                p.exited_at = _now_str()
                p.status = "CLOSED"
                p.pnl = (exit_price - p.entry_price) * p.quantity if p.action == "BUY" \
                    else (p.entry_price - exit_price) * p.quantity
                self._save()
                return p
        return None

    def positions(self) -> list[dict[str, Any]]:
        return [p.__dict__ for p in sorted(self._positions, key=lambda x: x.entered_at)]


class OptionsBacktest:
    """Simple short-option backtest with costs and slippage (approximate)."""

    def __init__(self, data_dir=DATA_DIR) -> None:
        self.history_file = data_dir / "options_backtest_history.json"
        self._runs: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        try:
            self._runs = json.loads(self.history_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._runs = []

    def _save(self) -> None:
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        self.history_file.write_text(json.dumps(self._runs, indent=2), encoding="utf-8")

    def run(self, strategy: str, notional: float, hold_days: int,
            premium_pct: float = 0.04, win_rate: float = 0.55,
            cost_bps: float = 10.0, slippage_bps: float = 5.0) -> dict[str, Any]:
        rng = random.Random(f"{strategy}-{time.time()}")
        premium = notional * premium_pct
        cost = notional * (cost_bps + slippage_bps) / 10_000
        wins = losses = 0
        pnl = 0.0
        daily = []
        for d in range(hold_days):
            if rng.random() < win_rate:
                wins += 1
                day_pnl = premium * 0.6
            else:
                losses += 1
                day_pnl = -premium * 0.5
            pnl += day_pnl
            daily.append({"day": d + 1, "pnl": round(day_pnl, 2)})
        net = pnl - cost
        run = {
            "strategy": strategy,
            "notional": notional,
            "hold_days": hold_days,
            "premium_pct": premium_pct,
            "wins": wins,
            "losses": losses,
            "win_rate": round(wins / hold_days, 3) if hold_days else 0.0,
            "gross_pnl": round(pnl, 2),
            "costs": round(cost, 2),
            "net_pnl": round(net, 2),
            "daily": daily,
            "run_at": _now_str(),
            "disclaimer": "Simulated path using configured win-rate. Not a real result.",
        }
        self._runs.append(run)
        self._save()
        return run

    def history(self) -> list[dict[str, Any]]:
        return self._runs


# --------------------------------------------------------------------------- combined analysis

def analyze_chain(underlying: str = "NIFTY", expiry: str | None = None,
                  store: bool = True, provider: OptionChainProvider | None = None) -> dict[str, Any]:
    """Fetch a chain and produce the full analysis bundle."""
    underlying = underlying.upper()
    if underlying not in NSE_UNDERLYINGS:
        raise OptionChainError(
            f"unsupported underlying {underlying!r}; choose from {sorted(NSE_UNDERLYINGS)}")
    service = OptionChainDataService(provider=provider)
    snap = service.fetch(underlying, expiry, store=store)
    if store:
        service.store(snap)
    analytics = ChainAnalytics(snap).all()
    srz = SupportResistance(snap).zones()
    scenario = ScenarioEngine(snap, analytics, srz).scenarios()
    signal = SignalEngine(snap, analytics).score()
    lab = StrategyLab(snap, analytics=analytics)
    return {
        "meta": {
            "underlying": snap.underlying, "expiry": snap.expiry,
            "expiries": snap.expiries, "spot": snap.spot,
            "futures": snap.futures, "market_state": snap.market_state,
            "timestamp": snap.timestamp, "source": snap.source,
            "quality": snap.quality,
        },
        "analytics": analytics,
        "support_resistance": srz,
        "scenarios": scenario,
        "signal": signal,
        "strategies": [lab.evaluate(name) for name in StrategyLab.STRATEGIES],
        "suggestions": lab.suggest(),
        "contracts": [c.to_dict() for c in snap.contracts],
        "contracts_count": len(snap.contracts),
    }


def format_brief(analysis: dict[str, Any]) -> str:
    """Human-readable AI brief for an option-chain analysis."""
    m = analysis["meta"]
    a = analysis["analytics"]
    oi, iv, pcr = a.get("oi", {}), a.get("iv", {}), a.get("pcr", {})
    srz, sig = analysis.get("support_resistance", {}), analysis.get("signal", {})
    exp = a.get("expected_move", {})
    q = m.get("quality", {}).get("status", "?")
    lines = [
        f"📊 Option Chain Brief — {m.get('underlying')} · {m.get('expiry')} · {q}",
        f"Spot {m.get('spot'):,.2f} · {m.get('market_state')} · source {m.get('source')}",
        f"PCR (OI) {pcr.get('pcr_oi')} · volume {pcr.get('pcr_volume')}",
        f"ATM IV {iv.get('atm_iv')} · regime {iv.get('regime')}",
        f"Max pain {a.get('max_pain', {}).get('max_pain')} · implied range {exp.get('lower')}–{exp.get('upper')}",
    ]
    if srz.get("support"):
        lines.append("Support: " + ", ".join(f"{x['strike']:,.0f}" for x in srz["support"][:3]))
    if srz.get("resistance"):
        lines.append("Resistance: " + ", ".join(f"{x['strike']:,.0f}" for x in srz["resistance"][:3]))
    lines.append(f"Signal score {sig.get('score')}/100 · {sig.get('label')} · confidence {sig.get('confidence')}")
    lines.append("⚠️ Scenario, not a prediction. Verify with your broker before trading.")
    return "\n".join(lines)
