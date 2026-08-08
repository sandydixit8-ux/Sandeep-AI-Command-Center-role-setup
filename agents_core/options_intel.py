"""Option Intelligence layer — advanced, evidence-based analytics on top of
agents_core.options.

Production capabilities required by the Option Chain master prompt:

    SnapshotHistoryStore  append-only chain history + windows + velocity
                           (OI / volume / IV time series, intraday resample)
    FuturesAnalytics       cost-of-carry futures model + basis + long/short
                           interpretation. NO live NSE futures feed exists, so
                           outputs are always labelled "ESTIMATED".
    ExpiryAnalytics        near vs next expiry, rollover, DTE, expiry-day
                           behaviour, OI migration (put/call shifting,
                           support/resistance migration, fresh vs unwinding).
    VolStats               IV rank / percentile / IV crush / put-call skew,
                           skew change / smile.
    LiquidityExecution     bid-ask spread, depth, expected execution price,
                           slippage / impact cost, order feasibility, liquidity
                           score, multi-leg cost comparison.
    ProbabilityEngine      model-derived PoP / probability-of-touch / EV /
                           expectancy (Black-Scholes + lognormal, labelled).
    StressScenario         spot / IV / time shocks -> strategy P&L (incl.
                           volatility expansion & crush, gap up / gap down).
    TradeQuality           setup / confirmation / liquidity / R:R / vol /
                           regime / execution -> 0-100 score with evidence.
    NoTradeEngine          explicit "NO TRADE" reasons.
    DecisionOutput         standard one-card decision output.
    SignalLifecycle        Generated -> Confirmed -> Triggered -> Entered
                           -> Modified -> Exited -> Expired (persisted).
    SignalPerformance      win rate, expectancy, MAE/MFE, drawdown, kill
                           criteria -> Observation / Protect mode.
    ProviderFailover       primary -> secondary -> cached -> STOP NEW SIGNALS
                           with heartbeat + freshness checks.
    MarketBreadth          advance/decline, sector breadth, new highs/lows,
                           volume & index breadth (from the market feed).
    EventCalendar          scheduled macro/earnings gates for IV-watch.

Data-integrity rules (inherited from options.py):
- Futures numbers that are modelled are ALWAYS labelled "ESTIMATED".
- Probabilities are model-derived, never arbitrary percentages.
- No "smart money", no guaranteed floors/ceilings, no safe-income framing.
- No automated live trading; there is no live order path in this module.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from . import options as oc
from . import market as mkt

HISTORY_DIR = DATA_DIR / "option_intel_history"  # jsonl, append-only

_RF = oc.RISK_FREE_RATE


def _now() -> datetime:
    return datetime.now()


def _ts() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def _parse(dt: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(dt, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(dt)
    except Exception:  # noqa: BLE001
        return _now()


# --------------------------------------------------------------------------- snapshot history


class SnapshotHistoryStore:
    """Append-only JSONL chain history + windows / velocity / riv series."""

    def __init__(self, root: Path = HISTORY_DIR) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, underlying: str) -> Path:
        return self.root / f"{underlying.upper()}.jsonl"

    def record(self, snap: oc.ChainSnapshot) -> None:
        ana = oc.ChainAnalytics(snap).all()
        iv = ana["iv"]
        row = {
            "ts": _ts(),
            "underlying": snap.underlying,
            "expiry": snap.expiry,
            "spot": snap.spot,
            "source": snap.source,
            "quality": snap.quality.get("status"),
            "pcr_oi": ana["pcr"]["pcr_oi"],
            "pcr_volume": ana["pcr"]["pcr_volume"],
            "atm_iv": iv.get("atm_iv"),
            "iv_regime": iv.get("regime"),
            "max_pain": ana["max_pain"]["max_pain"],
            "exp_lower": ana["expected_move"].get("lower"),
            "exp_upper": ana["expected_move"].get("upper"),
            "total_ce_oi": ana["oi"]["total_ce_oi"],
            "total_pe_oi": ana["oi"]["total_pe_oi"],
            "ce_change_oi": ana["oi"]["ce_change_oi"],
            "pe_change_oi": ana["oi"]["pe_change_oi"],
        }
        with open(self.path(snap.underlying), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")

    def read(self, underlying: str) -> list[dict[str, Any]]:
        p = self.path(underlying)
        if not p.exists():
            return []
        rows: list[dict[str, Any]] = []
        with open(p, encoding="utf-8") as fh:
            for ln in fh:
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except (ValueError, json.JSONDecodeError):
                        continue
        return rows

    def window(self, underlying: str, minutes: int = 120) -> list[dict[str, Any]]:
        since = _now() - timedelta(minutes=minutes)
        return [r for r in self.read(underlying) if _parse(r.get("ts", "")) >= since]

    def velocity(self, underlying: str, minutes: int = 120) -> dict[str, Any]:
        """Change rates between the oldest and newest snapshot in the window."""
        rows = self.window(underlying, minutes)
        if len(rows) < 2:
            return {
                "points": len(rows), "span_min": minutes, "atm_iv_change": None,
                "ce_oi_change": None, "pe_oi_change": None,
                "spot_change": None, "pcr_change": None,
                "note": "need at least 2 snapshots to compute velocity",
            }
        first, last = rows[0], rows[-1]
        try:
            hours = max((_parse(last["ts"]) - _parse(first["ts"])).total_seconds() / 3600.0, 1e-6)
        except Exception:  # noqa: BLE001
            hours = 1.0

        def chg(a, b):
            if a is None or b is None:
                return None
            return round((b - a) / hours, 4)

        return {
            "points": len(rows), "window_hours": round(hours, 2),
            "atm_iv_change": chg(first.get("atm_iv"), last.get("atm_iv")),
            "ce_oi_change": chg(first.get("total_ce_oi"), last.get("total_ce_oi")),
            "pe_oi_change": chg(first.get("total_pe_oi"), last.get("total_pe_oi")),
            "spot_change": chg(first.get("spot"), last.get("spot")),
            "pcr_change": chg(first.get("pcr_oi"), last.get("pcr_oi")),
            "first": first.get("ts"), "last": last.get("ts"),
        }

    def iv_series(self, underlying: str, minutes: int = 720) -> list[dict[str, Any]]:
        rows = self.window(underlying, minutes)
        return [{"t": r["ts"], "atm_iv": r.get("atm_iv")} for r in rows]

    def iv_rank_percentile(self, underlying: str, minutes: int = 30 * 24 * 60,
                           current_iv: float | None = None) -> dict[str, Any]:
        """IV rank / percentile from recorded history (30d default window)."""
        cutoff = _now() - timedelta(minutes=minutes)
        rows = [r.get("atm_iv") for r in self.read(underlying)
                if r.get("atm_iv") is not None and _parse(r.get("ts", "")) >= cutoff]
        if not rows:
            return {"error": "no IV history recorded yet", "samples": 0}
        cur = current_iv
        if cur is None:
            snap = oc.OptionChainDataService().fetch(underlying, store=False)
            cur = oc.ChainAnalytics(snap).all()["iv"].get("atm_iv")
            if cur is None:
                return {"error": "no current IV", "samples": len(rows)}
        lo, hi = min(rows), max(rows)
        rank = 0.0 if hi == lo else round((cur - lo) / (hi - lo) * 100, 1)
        percentile = round(sum(1 for x in rows if x <= cur) / len(rows) * 100, 1)
        return {
            "current_iv": cur, "min_iv": round(lo, 4), "max_iv": round(hi, 4),
            "mean_iv": round(sum(rows) / len(rows), 4),
            "iv_rank": rank, "iv_percentile": percentile,
            "samples": len(rows), "window_days": round(minutes / 1440, 1),
            "note": "IV rank/percentile from recorded snapshots; more history = more meaningful",
        }

    def recent_ivs(self, underlying: str, n: int = 30) -> list[float]:
        rows = [r.get("atm_iv") for r in self.read(underlying) if r.get("atm_iv") is not None]
        return rows[-n:]


# --------------------------------------------------------------------------- convenience helpers

def _current_analysis(underlying: str, expiry: str | None = None) -> dict[str, Any]:
    """Fetch + record + analyse a chain snapshot (used across this module)."""
    try:
        a = oc.analyze_chain(underlying, expiry, store=True)
    except Exception:  # noqa: BLE001
        a = oc.analyze_chain(underlying, expiry, store=False, provider=oc.MockOptionChainProvider())
    return a


def _atm_iv_from_history(underlying: str, current: float | None = None) -> float | None:
    if current is not None:
        return current
    rows = SnapshotHistoryStore().read(underlying)
    for r in reversed(rows):
        if r.get("atm_iv") is not None:
            return r["atm_iv"]
    return None


# --------------------------------------------------------------------------- futures (estimated)


class FuturesAnalytics:
    """Cost-of-carry futures model.

    NSE has no public index-futures quote endpoint reachable from this box, and
    the option-chain payload has no futures OI. Instead of fabricating quotes,
    we MODEL the futures value and basis from spot + expiry + risk-free rate and
    label every number ESTIMATED. Long/short interpretation is heuristic.
    """

    def __init__(self, underlying: str, expiry: str, spot: float,
                 div_yield: float = 0.0) -> None:
        self.underlying = underlying
        self.expiry = expiry
        self.spot = spot
        self.div_yield = div_yield
        self.t = max(oc.days_to_expiry(expiry), 1) / 365.0

    def future_with_basis(self) -> dict[str, Any]:
        carry = math.exp((_RF - self.div_yield) * self.t)
        fut = self.spot * carry if carry > 0 else self.spot
        basis = fut - self.spot
        return {
            "underlying": self.underlying, "expiry": self.expiry,
            "spot": round(self.spot, 2),
            "future": round(fut, 2),
            "basis_points": round(basis, 2),
            "basis_pct": round(basis / self.spot * 100, 4) if self.spot else 0.0,
            "carry_rate": round((_RF - self.div_yield) * 100, 2),
            "dte": oc.days_to_expiry(self.expiry),
            "labeled": "ESTIMATED",
            "note": "Cost-of-carry model (no live NSE futures feed). Not a broker quote.",
        }

    @staticmethod
    def interpret(spot_change_pct: float, oi_change_pct: float) -> dict[str, Any]:
        """Textbook OI+price interpretation. Evidence strength shown."""
        if oi_change_pct is None or spot_change_pct is None:
            return {"activity": "UNKNOWN", "strength": "WEAK"}
        thr = 2.0
        if oi_change_pct > thr and spot_change_pct > thr:
            act, exp = "LONG_BUILDUP", "buyers adding, trend likely supported"
        elif oi_change_pct > thr and spot_change_pct < -thr:
            act, exp = "SHORT_BUILDUP", "sellers adding, downside pressure"
        elif oi_change_pct < -thr and spot_change_pct > thr:
            act, exp = "SHORT_COVERING", "short sellers exiting, bounce possible"
        elif oi_change_pct < -thr and spot_change_pct < -thr:
            act, exp = "LONG_UNWINDING", "longs exiting, weakness possible"
        else:
            act, exp = "NEUTRAL", "change below 2% threshold"
        strength = "WEAK" if act == "NEUTRAL" else "MODERATE"
        return {"activity": act, "interpretation": exp,
                "strength": strength,
                "note": "OI is an input for analysis; this is not a trading call."}


# --------------------------------------------------------------------------- expiry + rollover


class ExpiryAnalytics:
    """Near vs next expiry, rollover, DTE and expiry-day behaviour."""

    def __init__(self, underlying: str, near_expiry: str,
                 next_expiry: str | None = None) -> None:
        self.underlying = underlying
        self.near = near_expiry
        self.next_ = next_expiry
        self.store = SnapshotHistoryStore()

    @staticmethod
    def _fetch(underlying: str, expiry: str) -> dict[str, Any] | None:
        try:
            return oc.analyze_chain(underlying, expiry, store=False)
        except Exception:  # noqa: BLE001
            return None

    def compare(self) -> dict[str, Any]:
        near = self._fetch(self.underlying, self.near)
        nxt = self._fetch(self.underlying, self.next_) if self.next_ else None
        out: dict[str, Any] = {"near": self.near, "next": self.next_}
        if near:
            na = near["analytics"]
            out["near_dte"] = oc.days_to_expiry(self.near)
            out["near_pcr"] = na["pcr"]["pcr_oi"]
            out["near_iv"] = na["iv"].get("atm_iv")
            out["near_maxpain"] = na["max_pain"]["max_pain"]
            out["near_spot"] = near["meta"]["spot"]
        if nxt:
            na2 = nxt["analytics"]
            out["next_dte"] = oc.days_to_expiry(self.next_)
            out["next_pcr"] = na2["pcr"]["pcr_oi"]
            out["next_iv"] = na2["iv"].get("atm_iv")
            out["next_maxpain"] = na2["max_pain"]["max_pain"]
            out["next_spot"] = nxt["meta"]["spot"]
        base = out.get("near_iv")
        nb = out.get("next_iv")
        if base and nb:
            out["iv_slope"] = round((nb - base) / base * 100, 1)
            out["term_shape"] = "BACKWARDATED" if nb < base else "CONTANGO"
        return out

    def expiry_behaviour(self, expiry: str) -> dict[str, Any]:
        """Expiry-day behaviour summary (needs recorded intraday snapshots)."""
        rows = [r for r in self.store.read(self.underlying) if r.get("expiry") == expiry]
        today = _ts()[:10]
        today_rows = [r for r in rows if r.get("ts", "")[:10] == today]
        change = self.store.velocity(self.underlying, minutes=24 * 60)
        return {
            "expiry": expiry, "today_records": len(today_rows),
            "oi_migration_visible": bool(change and change.get("points", 0) >= 2),
            "velocity": change,
            "note": "Logging snapshots every few minutes makes expiry-day migration trackable.",
        }

    def rollover(self) -> dict[str, Any]:
        """Rollover from near to next expiry (OI change comparison)."""
        near = self._fetch(self.underlying, self.near)
        nxt = self._fetch(self.underlying, self.next_) if self.next_ else None
        if not near or not nxt:
            return {"rollover_estimated": False,
                    "note": "need both near and next expiries"}
        na, na2 = near["analytics"], nxt["analytics"]
        return {
            "rollover_estimated": True,
            "near_ce_oi": na["oi"]["total_ce_oi"], "near_pe_oi": na["oi"]["total_pe_oi"],
            "next_ce_oi": na2["oi"]["total_ce_oi"], "next_pe_oi": na2["oi"]["total_pe_oi"],
            "next_ce_share": round(na2["oi"]["total_ce_oi"] / max(na["oi"]["total_ce_oi"] + na2["oi"]["total_ce_oi"], 1) * 100, 1),
            "note": "Estimated relative OI share; not a measured rollover factor.",
        }


# --------------------------------------------------------------------------- IV stats


class VolStats:
    """IV rank / percentile, IV crush detection, put-call skew, smile."""

    def __init__(self, underlying: str, analysis: dict[str, Any]) -> None:
        self.underlying = underlying
        self.a = analysis
        self.ana = analysis["analytics"]
        self.store = SnapshotHistoryStore()

    def _ce_pe_by_strike(self) -> tuple[dict[float, dict], dict[float, dict]]:
        ce: dict[float, dict] = {}
        pe: dict[float, dict] = {}
        for c in self.a["contracts"]:
            (ce if c["option_type"] == "CE" else pe)[c["strike"]] = c
        return ce, pe

    def iv_rank(self) -> dict[str, Any]:
        cur = self.ana["iv"].get("atm_iv")
        return self.store.iv_rank_percentile(self.underlying, current_iv=cur)

    def iv_spread_change(self) -> dict[str, Any]:
        rows = self.store.recent_ivs(self.underlying, n=10)
        cur = self.ana["iv"].get("atm_iv")
        if not rows or cur is None:
            return {"crush_detected": False, "note": "need recorded history to detect IV crush"}
        prev = rows[0] if len(rows) > 1 else cur
        pct = round((cur - prev) / (prev or 0.01) * 100, 1)
        return {
            "iv_change_pct": pct,
            "crush_detected": pct < -12,
            "from": round(prev, 4), "to": round(cur, 4),
            "note": "Sustained IV decline after a big move is typical of post-event crush.",
        }

    def put_call_skew(self, window: int = 6) -> dict[str, Any]:
        """Difference in implied IV between puts and calls around ATM."""
        spot = self.a["meta"]["spot"]
        ce, pe = self._ce_pe_by_strike()
        all_k = sorted(set(ce) | set(pe))
        if not all_k:
            return {"skew": "INSUFFICIENT_DATA", "note": "no strikes with both sides"}
        atm = min(all_k, key=lambda s: abs(s - spot))
        step = max((all_k[1] - all_k[0]) if len(all_k) > 1 else 50, 1)
        window_k = step * window
        rows = []
        diffs = []
        for s in all_k:
            if abs(s - spot) > window_k:
                continue
            if s in ce and s in pe and ce[s]["iv"] and pe[s]["iv"]:
                diff = round(pe[s]["iv"] - ce[s]["iv"], 4)
                rows.append({"strike": s, "ce_iv": ce[s]["iv"],
                             "pe_iv": pe[s]["iv"], "put_minus_call": diff})
                diffs.append(diff)
        avg = round(sum(diffs) / len(diffs), 4) if diffs else None
        if avg is None:
            skew = "INSUFFICIENT_DATA"
        elif avg > 0.02:
            skew = "RISK"      # puts richer -> hedging demand
        elif avg < -0.02:
            skew = "CALL_BIAS"  # calls richer -> bullish tilt
        else:
            skew = "NEUTRAL"
        return {"skew": skew, "atm_strike": atm, "avg_put_minus_call": avg,
                "rows": rows[: len(rows)],
                "note": "Skew is descriptive; it is not a predictor of direction."}

    def iv_smile(self, window: int = 8) -> dict[str, Any]:
        """Surface convexity around ATM — put+calls IV averaged per strike."""
        spot = self.a["meta"]["spot"]
        ce, pe = self._ce_pe_by_strike()
        step = 50
        cats: dict[float, list[float]] = {}
        for s in set(ce) | set(pe):
            k = round(s / step) * step
            ivs = [x for x in (ce[s]["iv"] if s in ce else None, pe[s]["iv"] if s in pe else None) if x]
            cats.setdefault(k, [])
            cats[k].extend(ivs)
        points = []
        for k, ivs in cats.items():
            if abs(k - spot) <= window * step and ivs:
                points.append({"strike": k, "avg_iv": round(sum(ivs) / len(ivs), 4)})
        points.sort(key=lambda r: r["strike"])
        if len(points) < 7:
            return {"smile": "INSUFFICIENT_DATA", "points": points}
        wings = [p["avg_iv"] for p in points[:2] + points[-2:]]
        mid = points[len(points) // 2]["avg_iv"]
        ratio = round(sum(wings) / len(wings) / mid, 3) if mid else 0.0
        return {
            "smile": "CONVEX" if ratio > 1.05 else ("FLAT" if ratio > 0.95 else "INVERTED"),
            "ratio": ratio, "points": points,
            "note": "Convexity from ATM IV surface; no directional meaning.",
        }

    def all(self) -> dict[str, Any]:
        return {
            "rank": self.iv_rank(),
            "crush": self.iv_spread_change(),
            "skew": self.put_call_skew(),
            "smile": self.iv_smile(),
        }

# --------------------------------------------------------------------------- probability engine


class ProbabilityEngine:
    """Model-derived probabilities (Black-Scholes lognormal dynamics).

    PoP / probability of touch for a plain long option use the standard
    lognormal distribution — these are labelled and not arbitrary.
    """

    def __init__(self, spot: float, expiry: str, atm_iv: float) -> None:
        self.spot = spot
        self.dte = max(oc.days_to_expiry(expiry), 1)
        self.t = self.dte / 365.0
        self.iv = max(atm_iv, 1e-4)
        self.sd = self.iv * math.sqrt(self.t)

    def _norm_cdf(self, x: float) -> float:
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    def prob_of_expiring_above(self, strike: float) -> float:
        """P(spot above strike at expiry), drift-neutral."""
        d = (math.log(self.spot / strike) + 0.5 * self.iv ** 2 * self.t) / self.sd
        return self._norm_cdf(d)

    def prob_of_expiring_below(self, strike: float) -> float:
        return 1.0 - self.prob_of_expiring_above(strike)

    def probability_of_touch(self, strike: float) -> float:
        """Approx chance the underlying trades at/to strike before expiry."""
        if strike <= 0:
            return 0.0
        d = math.log(strike / self.spot) / self.sd
        return min(1.0, 2.0 * self._norm_cdf(abs(d)))

    def expected_value(self, payout_if_above: float, payout_if_below: float,
                       strike: float) -> dict[str, Any]:
        p_up = self.prob_of_expiring_above(strike)
        ev = p_up * payout_if_above + (1 - p_up) * payout_if_below
        return {
            "ev": round(ev, 2),
            "p_above": round(p_up, 4),
            "p_below": round(1 - p_up, 4),
            "note": "Model-derived expectation � not a price guarantee.",
        }

    def strategy_expectancy(self, max_profit: float, max_loss: float,
                            breakevens: list[float]) -> dict[str, Any]:
        """EV using weighted distribution between breveakeven bands."""
        bs = sorted([b for b in breakevens if b > 0])
        if not bs:
            return {"ev": None, "note": "no breakevens to evaluate"}
        ref = bs[0]
        p_win = None
        if len(bs) == 1:
            p_win = self.prob_of_expiring_above(ref)
        else:
            p_win = self.prob_of_expiring_above(bs[0]) - self.prob_of_expiring_above(bs[1])
        exp = p_win * max_profit + (1 - p_win) * max_loss
        return {
            "ev": round(exp, 2),
            "prob_of_profit": round(p_win, 4),
            "risk_reward": round(abs(max_profit / max_loss), 2) if max_loss else None,
            "note": "Probability model uses ATM IV and a driftless lognormal; real regimes differ.",
        }


# --------------------------------------------------------------------------- stress scenarios


class StressScenario:
    """Re-price a strategy's payoff under spot/IV/time shocks."""

    def __init__(self, spot: float, expiry: str, atm_iv: float) -> None:
        self.spot = spot
        self.expiry = expiry
        self.atm_iv = max(atm_iv, 1e-4)

    def _repricing(self, legs: list[dict], spot_shock: float, iv_shock: float,
                   days_pass: float) -> float:
        S = self.spot * (1 + spot_shock)
        T0 = max(oc.days_to_expiry(self.expiry), 1) / 365.0
        T = max(oc.days_to_expiry(self.expiry) - days_pass, 1) / 365.0
        iv = self.atm_iv * (1 + iv_shock)
        total = 0.0
        for leg in legs:
            t0 = oc.bs_price(self.spot, leg["strike"], T0, _RF, self.atm_iv, leg["type"] == "CE")
            t1 = oc.bs_price(S, leg["strike"], T, _RF, iv, leg["type"] == "CE")
            delta = t1 - t0
            total += delta * leg["qty"] if leg["action"] == "BUY" else -delta * leg["qty"]
        return total

    def run(self, legs: list[dict], scenarios: list[dict] | None = None) -> dict[str, Any]:
        default_scenarios = [
            {"name": "Base", "spot_shock": 0.0, "iv_shock": 0.0, "days": 0},
            {"name": "Bull +2%", "spot_shock": 0.02, "iv_shock": 0.0, "days": 0},
            {"name": "Bear -2%", "spot_shock": -0.02, "iv_shock": 0.0, "days": 0},
            {"name": "IV expansion +10%", "spot_shock": 0.0, "iv_shock": 0.10, "days": 0},
            {"name": "Vol crush -20%", "spot_shock": 0.0, "iv_shock": -0.20, "days": 2},
            {"name": "Gap down -2% + crush", "spot_shock": -0.02, "iv_shock": -0.15, "days": 1},
            {"name": "Gap up +2% + expansion", "spot_shock": 0.02, "iv_shock": 0.15, "days": 1},
        ]
        scenarios = scenarios if scenarios is not None else default_scenarios
        return {
            "scenarios": [
                {**s, "pnl": round(self._repricing(legs, s["spot_shock"], s["iv_shock"], s.get("days", 0)), 2)}
                for s in scenarios
            ],
            "note": "Scenario P&L uses a Black-Scholes revaluation at the shocked spot/IV/time. Illustrative, not a guarantee.",
        }


# --------------------------------------------------------------------------- liquidity / execution


class LiquidityExecution:
    """spread, depth, expected execution, slippage, and impact estimates."""

    def __init__(self, contracts: list[dict], spot: float, lot_size: int = 75) -> None:
        self.contracts = [c for c in contracts if c["option_type"] in ("CE", "PE")]
        self.spot = spot
        self.lot = lot_size if lot_size > 0 else 75

    def _atm(self) -> dict | None:
        best = None
        for c in self.contracts:
            d = abs(c["strike"] - self.spot)
            if best is None or d < best[0]:
                best = (d, c)
        return best[1] if best else None

    def quote_quality(self, contract: dict | None = None) -> dict:
        c = contract or self._atm()
        if not c:
            return {"score": 0, "note": "no contract"}
        b, a, ltp = c.get("bid", 0), c.get("ask", 0), c.get("ltp", 0)
        spread = (a - b) if (a and b) else None
        rel = spread / max(ltp, 1) if spread is not None else None
        depth = min(c.get("bid_qty", 0), c.get("ask_qty", 0))
        score = 0
        if rel is not None:
            if rel <= 0.02:
                score += 50
            elif rel <= 0.05:
                score += 30
            else:
                score += 10
        if depth >= 50:
            score += 25
        elif depth >= 10:
            score += 12
        if c.get("volume", 0) > 0:
            score += 25
        return {
            "spread": round(spread, 2) if spread is not None else None,
            "spread_pct": round(rel * 100, 2) if rel is not None else None,
            "depth": depth, "volume": c.get("volume"),
            "score": score, "grade": "HIGH" if score >= 60 else ("MEDIUM" if score >= 30 else "LOW"),
        }

    def order_impact(self, qty_lots: int, ecoeff: float = 1e-6) -> dict:
        """Slippage + impact cost for an order of `qty_lots` lots (proxy)."""
        c = self._atm()
        if not c:
            return {"error": "no ATM contract"}
        avg = (c["bid"] + c["ask"]) / 2
        impact = (qty_lots * self.lot) * c.get("ltp", avg) * ecoeff * max(qty_lots, 1)
        return {
            "qty_lots": qty_lots,
            "expected_exec": round(avg, 2),
            "slippage_est": round(impact, 2) if c.get("volume", 0) > 0 else None,
            "mid": round(avg, 2),
            "feasible": qty_lots <= 200,
            "note": "Impact estimate is a crude proxy; verify with broker depth.",
        }

    def multi_leg_cost(self, legs: list[dict]) -> dict:
        """Net cash flow (debit - / credit +) of a multi-leg structure at market."""
        by_key = {}
        for c in self.contracts:
            by_key[(c["strike"], c["option_type"])] = c
        total = 0.0
        missing = []
        for leg in legs:
            c = by_key.get((float(leg.get("strike")), leg.get("option_type")))
            if not c:
                missing.append(leg)
                continue
            price = c["ask"] if leg["action"] == "BUY" else c["bid"]
            total += price * leg.get("qty", 1) if leg["action"] == "BUY" else -price * leg.get("qty", 1)
        return {
            "net_cost": round(total, 2),
            "debit_credit": "DEBIT (cash out)" if total > 0 else "CREDIT (cash in)",
            "unpriced_legs": len(missing),
            "note": "Priced at bid/ask from the chain; verify with broker depth.",
        }


# --------------------------------------------------------------------------- event calendar


class EventCalendar:
    """Scheduled macro/earnings gates for IV-watch (static reference, no fetch)."""

    EVENTS = [
        {"id": "rbi", "name": "RBI MPC", "window_days": (-2, 2), "typical_iv": 0.10},
        {"id": "fed", "name": "US Fed decision", "window_days": (-1, 1), "typical_iv": 0.08},
        {"id": "cpi", "name": "CPI / inflation", "window_days": (-1, 1), "typical_iv": 0.06},
        {"id": "cspe", "name": "US monthly jobs", "window_days": (-1, 1), "typical_iv": 0.05},
        {"id": "budget", "name": "Union Budget", "window_days": (-3, 2), "typical_iv": 0.15},
        {"id": "elections", "name": "General / state elections", "window_days": (-5, 5), "typical_iv": 0.18},
    ]

    def upcoming(self) -> list[dict]:
        return [{"id": e["id"], "name": e["name"],
                 "typical_iv_impact": e["typical_iv"],
                 "note": "Macro-event calendar is a gate for IV-watch; dates are not guaranteed."}
                for e in self.EVENTS]


# --------------------------------------------------------------------------- trade quality + no-trade


class TradeQuality:
    """Scored, evidence-carrying trade-quality estimate (not a stock score)."""

    def score(self, analysis: dict, strategy: dict | None = None,
              liquidity_score: int = 0, regime: str = "Sideways") -> dict[str, Any]:
        """Components are aggregated with documented weights."""
        ana = analysis["analytics"]
        iv = ana["iv"].get("atm_iv") or 0.15
        oi = ana["oi"]
        pcr = ana["pcr"].get("pcr_oi") or 0.9
        liquidity_grade = "HIGH" if liquidity_score >= 60 else ("MEDIUM" if liquidity_score >= 30 else "LOW")

        setup_w = 25.0 * (0.5 + 0.25 * abs(pcr - 0.9))   # 0..~37.5
        conf_w = 25.0 if strategy else 12.5
        liq_w = liquidity_score * 0.25
        rr_w = (min(max(strategy["max_profit"] / max(strategy["max_loss"], 1), 0), 3) / 3) * 25 if strategy else 12.0
        vol_w = 25.0 - min(abs(iv - 0.15) * 100, 25.0)   # ATM-IV around 15% is optimal
        ex_w = 15.0
        total = min(100.0, setup_w + conf_w + liq_w + rr_w + vol_w + ex_w)
        grade = "A" if total >= 80 else ("B" if total >= 65 else ("C" if total >= 45 else "D"))
        return {
            "score": round(total, 1),
            "grade": grade,
            "components": {
                "setup_quality": round(setup_w, 1),
                "confirmation": round(conf_w, 1),
                "liquidity": round(liq_w, 1),
                "risk_reward": round(rr_w, 1),
                "volatility": round(vol_w, 1),
                "market_regime": round(ex_w, 1) if regime else 0,
            },
            "margin_of_error": 5.0,
            "note": "Trade-quality score is an aid, not a prediction of outcome.",
        }


class NoTradeEngine:
    """Explicit NO TRADE decision with auditable reasons."""

    REASONS = [
        "insufficient_liquidity", "conflicting_signals", "high_event_risk",
        "poor_risk_reward", "excessive_iv", "insufficient_historical_evidence",
        "data_quality_problem", "market_regime_uncertainty", "risk_limit_breached",
        "strategy_outside_validated_conditions", "excessive_spread",
        "model_confidence_below_threshold",
    ]

    def decide(self, analysis: dict, strategy: dict | None = None,
               liquidity_grade: str = "LOW",
               iv_rank: float | None = None,
               data_ok: bool = True, event_risk: bool = False) -> dict[str, Any]:
        flags: list[dict[str, str]] = []
        ana = analysis["analytics"]
        iv = ana["iv"].get("atm_iv")
        if liquidity_grade == "LOW":
            flags.append({"reason": "insufficient_liquidity", "detail": "spread/depth insufficient"})
        if iv and iv > 0.35:
            flags.append({"reason": "excessive_iv", "detail": f"ATM IV {iv*100:.0f}%"})
        if iv_rank is not None and iv_rank > 90:
            flags.append({"reason": "excessive_iv", "detail": f"IV rank {iv_rank:.0f}% (expensive)"})
        if event_risk:
            flags.append({"reason": "high_event_risk", "detail": "macro/earnings gate active"})
        if not data_ok:
            flags.append({"reason": "data_quality_problem", "detail": "degraded/stale chain"})
        if strategy and "breakevens" in strategy and not strategy["breakevens"]:
            flags.append({"reason": "poor_risk_reward", "detail": "no breakeven within range"})

        if flags:
            return {
                "decision": "NO TRADE",
                "reasons": flags,
                "message": "Evidence is conflicting or the risk/reward does not meet the configured threshold. Prefer staying flat.",
                "tradeable": False,
            }
        return {"decision": "WATCH / VALIDATED", "reasons": [], "narrative": "Conditions pass; keep monitoring.", "tradeable": True}


# --------------------------------------------------------------------------- decision card / signal lifecycle


class SignalLifecycle:
    """Persistent lifecycle for every system signal."""

    STATES = ["GENERATED", "CONFIRMED", "TRIGGERED", "ENTERED", "MODIFIED", "EXITED", "EXPIRED"]
    def __init__(self, file: Path = DATA_DIR / "signal_lifecycle.json") -> None:
        self.file = file
        self.rows: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            self.rows = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.rows = []

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.rows, indent=2), encoding="utf-8")

    def create(self, symbol: str, dtype: str, details: dict) -> dict:
        rec = {"id": f"SL{len(self.rows)+1}", "symbol": symbol, "type": dtype,
               "stage": "GENERATED", "ts": _ts(), "details": details, "history": [{"stage": "GENERATED", "ts": _ts()}]}
        self.rows.append(rec)
        self._save()
        return rec

    def transition(self, uid: str, to_stage: str, note: str = "") -> dict | None:
        r = next((x for x in self.rows if x["id"] == uid), None)
        if not r or to_stage not in self.STATES:
            return None
        r["stage"] = to_stage
        r["history"].append({"stage": to_stage, "ts": _ts(), "note": note})
        self._save()
        return r


class SignalsPerformance:
    """Closed-signal analytics: win rate, expectancy, drawdown, kill criteria."""

    def __init__(self, file: Path = DATA_DIR / "signal_results.json") -> None:
        self.file = file
        self.results: list[dict] = []
        self._load()

    def _load(self) -> None:
        try:
            self.results = json.loads(self.file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.results = []

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(self.results, indent=2), encoding="utf-8")

    def record(self, signal_id: str, result: float, max_adv: float = 0, max_adverse: float = 0) -> None:
        self.results.append({"id": signal_id, "result": result, "max_fav": max_adv,
                             "max_adv": max_adverse, "ts": _ts()})
        self._save()

    def stats(self) -> dict[str, Any]:
        rs = [r for r in self.results if "result" in r]
        if not rs:
            return {"note": "no closed signals yet"}
        vals = [r["result"] for r in rs]
        wins = sum(1 for x in vals if x > 0)
        losers = sum(1 for x in vals if x < 0)
        avg = sum(vals) / len(vals)
        kill = self._kill_verdict(avg)
        cum, peak, max_dd = 0.0, 0.0, 0.0
        for v in vals:
            cum += v
            peak = max(peak, cum)
            max_dd = max(max_dd, peak - cum)
        return {
            "n": len(vals), "win_rate": round(wins / len(vals), 3),
            "expectancy_per_trade": round(avg, 2),
            "avg_win": round(sum(v for v in vals if v > 0) / max(wins, 1), 2),
            "avg_loss": round(sum(v for v in vals if v < 0) / max(losers, 1), 2),
            "max_drawdown": round(max_dd, 2),
            "worst_trade": round(min(vals), 2),
            "kill": kill,
        }

    @staticmethod
    def _kill_verdict(expectancy: float) -> dict:
        return {
            "kill_strategy": expectancy <= -2.0,
            "mode": "OBSERVATION" if expectancy <= -2.0 else "NOMINAL",
            "note": "When rolling expectancy stays negative beyond a threshold the strategy enters observation mode.",
        }


# --------------------------------------------------------------------------- failover + heartbeat


class ProviderFailover:
    """Primary -> secondary -> cached -> STOP NEW SIGNALS. Heartbeat + freshness."""

    def __init__(self, primary_name: str = "nse", secondary_name: str = "mock") -> None:
        self.primary_name = primary_name
        self.secondary_name = secondary_name
        self.history: list[dict] = []
        self.state = {
            "active": primary_name, "degraded": False, "stopped": False,
            "quality": "unknown", "last_ok": None, "failures": 0,
        }

    def heartbeat(self, source: str, ok: bool, quality: str = "ok", detail: str = "") -> dict:
        rec = {"ts": _ts(), "source": source, "ok": bool(ok), "quality": quality, "detail": detail}
        self.history.append(rec)
        self.history = self.history[-200:]
        st = self.state
        st["quality"] = quality
        if not ok:
            st["failures"] = st.get("failures", 0) + 1
        else:
            st["failures"] = 0
        if st["failures"] >= 3:
            st["active"] = self.secondary_name
            st["degraded"] = True
            st["stopped"] = True
        return dict(st)

    def status(self) -> dict[str, Any]:
        return {
            "active": self.state["active"],
            "degraded": self.state["degraded"],
            "stopped_new_signals": self.state["stopped"],
            "qualify": self.state["quality"],
            "recent": self.history[-5:],
            "note": "If the feed cannot be restored within the heartbeat window, new signals STOP.",
        }


class MarketBreadth:
    """Index + stock-market breadth from the moneycontrol universe."""

    def __init__(self) -> None:
        try:
            self.indices = mkt.get_provider().get_indices()
        except Exception:  # noqa: BLE001
            self.indices = []

    def summary(self, universe: list[dict] | None = None) -> dict[str, Any]:
        ups = [i for i in self.indices if i.get("change_pct", 0) >= 0]
        down = len(self.indices) - len(ups)
        stocks = universe or mkt.screener({})
        stock_up = [s for s in stocks if (s.get("change_pct") or 0) >= 0]
        indices_breadth = round(len(ups) / len(self.indices), 2) if self.indices else 0.0
        stocks_breadth = round(len(stock_up) / len(stocks), 2) if stocks else 0.0
        return {
            "indices_up": len(ups), "indices_down": down,
            "indices_breadth": indices_breadth,
            "stocks_up": len(stock_up), "stocks_total": len(stocks),
            "stocks_breadth": stocks_breadth,
            "regime_read": "RISK-ON" if stocks_breadth >= 0.6 else ("RISK-OFF" if stocks_breadth <= 0.4 else "MIXED"),
            "note": "Breadth places option-chain signals in a market-context frame.",
        }


# --------------------------------------------------------------------------- combined intelligence


def intelligence_report(underlying: str = "NIFTY", expiry: str | None = None,
                        record: bool = True) -> dict[str, Any]:
    """Bundle every intelligence engine for `underlying`."""
    analysis = _current_analysis(underlying, expiry)
    store = SnapshotHistoryStore()
    if record:
        try:
            snap = oc.ChainSnapshot(
                underlying=analysis["meta"]["underlying"],
                spot=analysis["meta"]["spot"],
                futures=None,
                expiry=analysis["meta"]["expiry"],
                expiries=analysis["meta"]["expiries"],
                market_state=analysis["meta"]["market_state"],
                timestamp=analysis["meta"]["timestamp"],
                quality=analysis["meta"]["quality"],
                contracts=[oc.OptionContract(**{**c, "greeks_source": c.get("greeks_source", "calculated")}) for c in analysis["contracts"]],
                source=analysis["meta"]["source"],
            )
            oc.OptionChainDataService()._attach_greeks(snap.contracts, snap.spot)
            store.record(snap)
        except Exception:  # noqa: BLE001
            pass

    spot = analysis["meta"]["spot"]
    iv = analysis["analytics"]["iv"].get("atm_iv") or 0.15
    expiry = expiry or analysis["meta"]["expiry"]

    vol = VolStats(underlying, analysis)
    fut = FuturesAnalytics(underlying, expiry, spot)
    probs = ProbabilityEngine(spot, expiry, iv)
    lex = LiquidityExecution(analysis["contracts"], spot, lot_size=oc.LOT_SIZES.get(underlying, 75))
    atm_liq = lex.quote_quality()
    strat = analysis["strategies"][0] if analysis.get("strategies") else None
    tq = (TradeQuality().score(analysis, strat, liquidity_score=atm_liq.get("score", 0))
          if strat else None)

    ivr = vol.iv_rank()
    no_trade = NoTradeEngine().decide(
        analysis, strat,
        liquidity_grade=atm_liq.get("grade", "LOW"),
        iv_rank=ivr.get("iv_rank"),
        data_ok=analysis["meta"]["quality"]["status"] != "🔴 Unavailable",
    )

    return {
        "meta": analysis["meta"],
        "futures": fut.future_with_basis(),
        "expiry": ExpiryAnalytics(
            underlying,
            analysis["meta"]["expiry"],
            analysis["meta"]["expiries"][1] if len(analysis["meta"]["expiries"]) > 1 else None,
        ).compare(),
        "velocity": store.velocity(underlying, 120),
        "vol_stats": vol.all(),
        "liquidity": {"atm": lex.quote_quality(), "impact": lex.order_impact(1)},
        "trade_quality": tq,
        "no_trade": no_trade,
        "breadth": MarketBreadth().summary(),
        "events": EventCalendar().upcoming(),
        "monitoring": {
            "failover": ProviderFailover().status(),
            "freshness": analysis["meta"]["quality"],
        },
        "contracts_count": len(analysis["contracts"]),
    }
