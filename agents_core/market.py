"""Market Intelligence engine for the AI Command Center.

Architecture (per the product spec):
    MarketDataProvider (abstract) -> MoneycontrolMarketProvider (live quotes,
        indices and OHLC from Moneycontrol's public pricefeed, with automatic
        fallback to MockMarketProvider when the feed is offline)
    TechnicalAnalysis  -> indicators + technical view
    FundamentalView    -> fundamentals + trends
    MarketScoring      -> transparent factor score
    SignalEngine       -> composite signal (never from one indicator)
    RegimeEngine       -> market regime detection
    Screener           -> filter stocks
    RiskEngine         -> position sizing, exposure, concentration
    BacktestEngine     -> strategy backtest + metrics
    PaperTradingStore  -> simulated portfolio (file-backed)

Data-safety principles honoured here:
- Prices are never invented by the AI: they come from the provider layer.
- Live records are labelled with the source and 'Live (Moneycontrol)'.
- When the live feed is unreachable, the provider falls back to deterministic
  mock data that is clearly labelled 'Delayed (demo data)'.
- Signals must combine multiple factors; a lone indicator never produces a signal.
- Confidence is only shown when there is supporting evidence.
"""
from __future__ import annotations

import json
import math
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from .config import DATA_DIR
from .safety import safety_gate
from . import circuit_breaker as cb

# --------------------------------------------------------------------------- data

# Deterministic stock universe (Indian market). Prices are mock/demo values.
STOCKS: list[dict[str, Any]] = [
    {"symbol": "RELIANCE", "name": "Reliance Industries", "sector": "Energy", "price": 2894.5, "prev_close": 2867.2, "market_cap": 1954000, "pe": 24.1, "pb": 2.6, "roe": 9.8, "roce": 8.9, "de": 0.52, "div_yield": 0.34, "promoter": 50.5, "volatility": 0.022, "momentum": 0.012, "news_sentiment": 0.35, "volume": 4800000, "avg_volume": 4100000, "52w_high": 3090.0, "52w_low": 2290.0},
    {"symbol": "TATA MOTORS", "name": "Tata Motors", "sector": "Automobile", "price": 1006.4, "prev_close": 990.1, "market_cap": 368200, "pe": 18.9, "pb": 3.8, "roe": 21.5, "roce": 14.2, "de": 0.61, "div_yield": 0.20, "promoter": 42.7, "volatility": 0.028, "momentum": 0.018, "news_sentiment": 0.42, "volume": 9200000, "avg_volume": 7400000, "52w_high": 1085.0, "52w_low": 586.0},
    {"symbol": "INFY", "name": "Infosys", "sector": "IT Services", "price": 1728.9, "prev_close": 1741.0, "market_cap": 717400, "pe": 27.6, "pb": 6.7, "roe": 29.8, "roce": 31.0, "de": 0.08, "div_yield": 2.05, "promoter": 14.4, "volatility": 0.018, "momentum": -0.006, "news_sentiment": 0.12, "volume": 5200000, "avg_volume": 6900000, "52w_high": 1903.0, "52w_low": 1441.0},
    {"symbol": "HDFCBANK", "name": "HDFC Bank", "sector": "Banking", "price": 1664.2, "prev_close": 1651.8, "market_cap": 1275000, "pe": 20.2, "pb": 3.1, "roe": 17.6, "roce": 8.2, "de": 1.20, "div_yield": 1.12, "promoter": 26.0, "volatility": 0.015, "momentum": 0.005, "news_sentiment": 0.20, "volume": 9800000, "avg_volume": 8800000, "52w_high": 1750.0, "52w_low": 1363.0},
    {"symbol": "TCS", "name": "Tata Consultancy Services", "sector": "IT Services", "price": 3990.0, "prev_close": 4020.5, "market_cap": 1443000, "pe": 29.4, "pb": 12.8, "roe": 45.2, "roce": 48.0, "de": 0.03, "div_yield": 1.65, "promoter": 72.3, "volatility": 0.016, "momentum": -0.004, "news_sentiment": 0.15, "volume": 2100000, "avg_volume": 2400000, "52w_high": 4380.0, "52w_low": 3420.0},
    {"symbol": "SBIN", "name": "State Bank of India", "sector": "Banking", "price": 842.3, "prev_close": 831.7, "market_cap": 751800, "pe": 10.8, "pb": 1.8, "roe": 18.5, "roce": 6.4, "de": 0.42, "div_yield": 1.85, "promoter": 57.5, "volatility": 0.020, "momentum": 0.010, "news_sentiment": 0.28, "volume": 11200000, "avg_volume": 9900000, "52w_high": 912.0, "52w_low": 636.0},
    {"symbol": "ITC", "name": "ITC Limited", "sector": "FMCG", "price": 468.9, "prev_close": 465.2, "market_cap": 585400, "pe": 29.8, "pb": 8.4, "roe": 28.6, "roce": 33.1, "de": 0.00, "div_yield": 3.10, "promoter": 0.0, "volatility": 0.012, "momentum": 0.007, "news_sentiment": 0.18, "volume": 8100000, "avg_volume": 7600000, "52w_high": 519.0, "52w_low": 398.0},
    {"symbol": "BHARTIARTL", "name": "Bharti Airtel", "sector": "Telecom", "price": 1498.6, "prev_close": 1476.3, "market_cap": 895100, "pe": 61.2, "pb": 12.4, "roe": 19.4, "roce": 16.8, "de": 0.95, "div_yield": 0.30, "promoter": 56.0, "volatility": 0.021, "momentum": 0.015, "news_sentiment": 0.25, "volume": 4300000, "avg_volume": 3800000, "52w_high": 1602.0, "52w_low": 988.0},
    {"symbol": "HINDUNILVR", "name": "Hindustan Unilever", "sector": "FMCG", "price": 2450.0, "prev_close": 2470.8, "market_cap": 575800, "pe": 50.1, "pb": 10.6, "roe": 21.9, "roce": 28.4, "de": 0.05, "div_yield": 1.45, "promoter": 61.9, "volatility": 0.014, "momentum": -0.008, "news_sentiment": 0.05, "volume": 1600000, "avg_volume": 1900000, "52w_high": 2702.0, "52w_low": 2230.0},
    {"symbol": "LT", "name": "Larsen & Toubro", "sector": "Infrastructure", "price": 3642.8, "prev_close": 3598.1, "market_cap": 501600, "pe": 33.4, "pb": 5.2, "roe": 15.8, "roce": 12.5, "de": 0.49, "div_yield": 0.72, "promoter": 0.0, "volatility": 0.024, "momentum": 0.011, "news_sentiment": 0.30, "volume": 2400000, "avg_volume": 2100000, "52w_high": 3860.0, "52w_low": 2950.0},
    {"symbol": "ASIANPAINT", "name": "Asian Paints", "sector": "Consumer", "price": 3050.4, "prev_close": 3080.0, "market_cap": 292600, "pe": 62.0, "pb": 16.8, "roe": 26.4, "roce": 33.2, "de": 0.11, "div_yield": 0.55, "promoter": 52.6, "volatility": 0.020, "momentum": -0.010, "news_sentiment": -0.08, "volume": 1500000, "avg_volume": 1700000, "52w_high": 3380.0, "52w_low": 2610.0},
    {"symbol": "SUNPHARMA", "name": "Sun Pharmaceutical", "sector": "Pharma", "price": 1812.5, "prev_close": 1790.0, "market_cap": 435000, "pe": 34.8, "pb": 5.6, "roe": 16.2, "roce": 14.9, "de": 0.12, "div_yield": 0.85, "promoter": 54.4, "volatility": 0.019, "momentum": 0.013, "news_sentiment": 0.38, "volume": 2300000, "avg_volume": 2000000, "52w_high": 1905.0, "52w_low": 1325.0},
    {"symbol": "TITAN", "name": "Titan Company", "sector": "Consumer", "price": 3450.0, "prev_close": 3421.7, "market_cap": 306400, "pe": 88.2, "pb": 21.0, "roe": 25.1, "roce": 24.6, "de": 0.09, "div_yield": 0.12, "promoter": 52.9, "volatility": 0.023, "momentum": 0.009, "news_sentiment": 0.22, "volume": 900000, "avg_volume": 1100000, "52w_high": 3860.0, "52w_low": 2760.0},
    {"symbol": "BAJFINANCE", "name": "Bajaj Finance", "sector": "Finance", "price": 7200.0, "prev_close": 7150.3, "market_cap": 446500, "pe": 31.5, "pb": 5.1, "roe": 19.8, "roce": 4.2, "de": 3.40, "div_yield": 0.60, "promoter": 54.1, "volatility": 0.026, "momentum": 0.016, "news_sentiment": 0.30, "volume": 1200000, "avg_volume": 1400000, "52w_high": 7880.0, "52w_low": 5415.0},
    {"symbol": "MARUTI", "name": "Maruti Suzuki", "sector": "Automobile", "price": 11450.0, "prev_close": 11380.0, "market_cap": 360100, "pe": 24.8, "pb": 3.5, "roe": 15.4, "roce": 13.8, "de": 0.03, "div_yield": 1.10, "promoter": 58.2, "volatility": 0.017, "momentum": 0.006, "news_sentiment": 0.16, "volume": 800000, "avg_volume": 900000, "52w_high": 12300.0, "52w_low": 8950.0},
]

INDICES: list[dict[str, Any]] = [
    {"symbol": "NIFTY 50", "name": "NIFTY 50", "value": 24250.35, "change": 148.6, "pct": 0.62},
    {"symbol": "BANK NIFTY", "name": "NIFTY Bank", "value": 52780.1, "change": 402.4, "pct": 0.77},
    {"symbol": "SENSEX", "name": "BSE Sensex", "value": 79650.2, "change": 315.8, "pct": 0.40},
    {"symbol": "NIFTY IT", "name": "NIFTY IT", "value": 39520.0, "change": -210.5, "pct": -0.53},
    {"symbol": "NIFTY MIDCAP", "name": "NIFTY Midcap", "value": 56210.0, "change": 338.9, "pct": 0.61},
    {"symbol": "NIFTY AUTO", "name": "NIFTY Auto", "value": 23860.0, "change": 195.4, "pct": 0.83},
]


# --------------------------------------------------------------------------- live provider (moneycontrol)

# Moneycontrol public pricefeed (same endpoints the website/app use).
MC_PRICE_FEED = "https://priceapi.moneycontrol.com/pricefeed"
MC_TECH_CHARTS = "https://priceapi.moneycontrol.com/techCharts/indianMarket/stock"

# Moneycontrol index code -> pricefeed code (verified: NSX/SEN/nbx/cnit/ccx/cnxa).
MC_INDEX_CODES: dict[str, str] = {
    "NIFTY 50": "NSX",
    "BANK NIFTY": "nbx",
    "SENSEX": "SEN",
    "NIFTY IT": "cnit",
    "NIFTY MIDCAP": "ccx",
    "NIFTY AUTO": "cnxa",
}

# Stock symbol -> Moneycontrol sc_id (resolved via autosuggestion_solr.php).
MC_STOCK_IDS: dict[str, str] = {
    "RELIANCE": "RI",
    "TATA MOTORS": "TML02",
    "INFY": "IT",
    "HDFCBANK": "HDF01",
    "TCS": "TCS",
    "SBIN": "SBI",
    "ITC": "ITC",
    "BHARTIARTL": "BTV",
    "HINDUNILVR": "HL",
    "LT": "LT",
    "ASIANPAINT": "API",
    "SUNPHARMA": "SPI",
    "TITAN": "TI01",
    "BAJFINANCE": "BAF",
    "MARUTI": "MU01",
}

# Stock symbol -> NSE ticker used by the techCharts history feed.
MC_STOCK_TICKERS: dict[str, str] = {
    "RELIANCE": "RELIANCE",
    "TATA MOTORS": "TMCV",
    "INFY": "INFY",
    "HDFCBANK": "HDFCBANK",
    "TCS": "TCS",
    "SBIN": "SBIN",
    "ITC": "ITC",
    "BHARTIARTL": "BHARTIARTL",
    "HINDUNILVR": "HINDUNILVR",
    "LT": "LT",
    "ASIANPAINT": "ASIANPAINT",
    "SUNPHARMA": "SUNPHARMA",
    "TITAN": "TITAN",
    "BAJFINANCE": "BAJFINANCE",
    "MARUTI": "MARUTI",
}

MC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.moneycontrol.com/",
    "Origin": "https://www.moneycontrol.com",
    "X-Requested-With": "XMLHttpRequest",
}

MC_TIMEOUT = 8.0  # seconds


def _deterministic_rng(symbol: str, seed_extra: int = 0) -> random.Random:
    return random.Random(sum(ord(c) for c in symbol) + seed_extra)


# --------------------------------------------------------------------------- provider

class MarketDataProvider(Protocol):
    def name(self) -> str: ...
    def get_indices(self) -> list[dict[str, Any]]: ...
    def get_stock(self, symbol: str) -> dict[str, Any] | None: ...
    def list_stocks(self) -> list[dict[str, Any]]: ...
    def ohlc(self, symbol: str, days: int = 200) -> list[dict[str, float]]: ...
    def status(self) -> dict[str, str]: ...
    def orderbook(self, symbol: str) -> dict[str, Any] | None: ...


class MockMarketProvider:
    """Deterministic demo market data. Clearly labelled as mock/delayed."""

    def name(self) -> str:
        return "mock-nse-demo"

    def get_indices(self) -> list[dict[str, Any]]:
        now = datetime.now()
        return [
            {**i,
             "change_pct": round(i["pct"], 2),
             "status": "🟢 Open" if 9 <= now.hour < 16 else "🟡 Delayed",
             "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
             "source": self.name(),
             "quality": "🟡 Delayed (demo data)"}
            for i in INDICES
        ]

    def list_stocks(self) -> list[dict[str, Any]]:
        return [self._decorate(s) for s in STOCKS]

    def get_stock(self, symbol: str) -> dict[str, Any] | None:
        key = symbol.strip().upper()
        for s in STOCKS:
            if s["symbol"] == key or key in s["name"].upper():
                return self._decorate(s)
        return None

    def _decorate(self, s: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now()
        return {
            **s,
            "change": round(s["price"] - s["prev_close"], 2),
            "change_pct": round((s["price"] - s["prev_close"]) / s["prev_close"] * 100, 2),
            "status": "🟢 Open" if 9 <= now.hour < 16 else "🟡 Delayed",
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "source": self.name(),
            "quality": "🟡 Delayed (demo data)",
            "exchange": "NSE",
        }

    def ohlc(self, symbol: str, days: int = 200) -> list[dict[str, float]]:
        """Generate a deterministic OHLC series ending near the stock's current price."""
        s = self.get_stock(symbol)
        if not s:
            raise ValueError(f"unknown symbol: {symbol}")
        rng = _deterministic_rng(symbol)
        price = float(s["52w_low"])
        high = float(s["52w_high"])
        vol = float(s["volatility"])
        series: list[dict[str, float]] = []
        end = datetime.now()
        for i in range(days):
            drift = (high - price) / (high + 1) * 0.02 + rng.uniform(-vol, vol)
            open_ = price
            close = max(price * (1 + drift), 1)
            hi = max(open_, close) * (1 + abs(rng.gauss(0, vol * 0.5)))
            lo = min(open_, close) * (1 - abs(rng.gauss(0, vol * 0.5)))
            date = end - timedelta(days=days - 1 - i)
            volume = s["avg_volume"] * rng.uniform(0.7, 1.4)
            series.append({"date": date.strftime("%Y-%m-%d"), "open": round(open_, 2), "high": round(hi, 2), "low": round(lo, 2), "close": round(close, 2), "volume": round(volume)})
            price = close
        # snap the final close to the current quoted price so views stay consistent
        series[-1]["close"] = s["price"]
        series[-1]["open"] = s["prev_close"]
        series[-1]["high"] = max(series[-1]["high"], s["price"])
        series[-1]["low"] = min(series[-1]["low"], s["price"])
        return series

    def orderbook(self, symbol: str) -> dict[str, Any] | None:
        """Deterministic mock orderbook built around the current mock price."""
        s = self.get_stock(symbol)
        if not s:
            return None
        rng = _deterministic_rng(symbol)
        px = float(s["price"])
        levels = []
        for i in range(1, 6):
            levels.append({
                "level": i,
                "bid": round(px - i * px * 0.0004, 2),
                "bid_qty": int(s["avg_volume"] / 40 * rng.uniform(0.6, 1.4)),
                "ask": round(px + i * px * 0.0004, 2),
                "ask_qty": int(s["avg_volume"] / 40 * rng.uniform(0.6, 1.4)),
            })
        return {
            "symbol": s["symbol"],
            "source": self.name(),
            "quality": "🟡 Delayed (demo data)",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "levels": levels,
        }

    def status(self) -> dict[str, str]:
        return {"provider": self.name(), "mode": "demo", "data": "delayed-mock", "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


_provider: MarketDataProvider | None = None


class MoneycontrolMarketProvider:
    """Live Indian market data from Moneycontrol's public pricefeed.

    Fetches real quotes/indices/OHLC from the same endpoints the Moneycontrol
    website and app use. Every record is labelled as live. On any network or
    parse error it silently falls back to the deterministic mock so the app
    never breaks offline.
    """

    def __init__(self) -> None:
        self._mock = MockMarketProvider()
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_live: str | None = None
        self._last_fallback: str | None = None
        self._fallback_reason: str | None = None
        self._degraded_until: float = 0.0

    def name(self) -> str:
        return "moneycontrol-live"

    # ------------------------------------------------------------------ http

    def _get_json(self, url: str, params: dict[str, Any] | None = None, ttl: float = 20.0) -> Any:
        now = time.time()
        if now < self._degraded_until:
            raise ConnectionError("moneycontrol temporarily degraded (offline circuit breaker)")
        key = url
        cached = self._cache.get(key)
        if cached and now - cached[0] < ttl:
            return cached[1]
        resp = httpx.get(url, params=params, headers=MC_HEADERS, timeout=MC_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, dict) and "code" in payload and str(payload.get("code")) != "200":
            raise ValueError(f"moneycontrol error {payload.get('code')}: {payload.get('message')}")
        self._cache[key] = (now, payload)
        return payload

    def _mark_degraded(self) -> None:
        self._degraded_until = time.time() + 60

    # ------------------------------------------------------------------ indices

    def get_indices(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for base in INDICES:
            code = MC_INDEX_CODES.get(base["symbol"])
            try:
                payload = self._get_json(f"{MC_PRICE_FEED}/notapplicable/inidicesindia/in%3B{code}")
                d = payload["data"]
                now = datetime.now()
                value = float(d.get("pricecurrent") or base["value"])
                prev = float(d.get("priceprevclose") or (value - base["change"]))
                change = float(d.get("pricechange") or (value - prev))
                pct = float(d.get("pricepercentchange") or base["pct"])
                status = "🟢 Open" if str(d.get("market_state", "")).upper() == "OPEN" else "🟡 Market closed"
                results.append({
                    **base,
                    "value": value,
                    "change": round(change, 2),
                    "pct": round(pct, 2),
                    "change_pct": round(pct, 2),
                    "open": float(d.get("OPEN") or 0),
                    "high": float(d.get("HIGH") or 0),
                    "low": float(d.get("LOW") or 0),
                    "52w_high": float(d.get("52wkhi") or 0),
                    "52w_low": float(d.get("52wklow") or 0),
                    "status": status,
                    "timestamp": str(d.get("lastupd") or now.strftime("%Y-%m-%d %H:%M:%S")),
                    "source": self.name(),
                    "quality": "🟢 Live (Moneycontrol)",
                })
                self._last_live = str(d.get("lastupd") or now.strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as exc:  # noqa: BLE001
                self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._fallback_reason = f"{base['symbol']}: {exc}"
                self._mark_degraded()
                fallback = next((i for i in self._mock.get_indices() if i["symbol"] == base["symbol"]), None)
                results.append(fallback if fallback else self._mock.get_indices()[0])
        return results

    # ------------------------------------------------------------------ stocks

    def _live_stock(self, s: dict[str, Any]) -> dict[str, Any] | None:
        sc_id = MC_STOCK_IDS.get(s["symbol"])
        if not sc_id:
            return None
        payload = self._get_json(f"{MC_PRICE_FEED}/nse/equitycash/{sc_id}")
        d = payload["data"]
        price = float(d.get("pricecurrent") or s["price"])
        prev = float(d.get("priceprevclose") or s["prev_close"])
        change = float(d.get("pricechange") or (price - prev))
        pct = float(d.get("pricepercentchange") or (round((price - prev) / prev * 100, 2) if prev else 0))
        status = "🟢 Open" if str(d.get("market_state", "")).upper() == "OPEN" else "🟡 Market closed"
        return {
            **s,
            "price": price,
            "prev_close": prev,
            "change": round(change, 2),
            "change_pct": round(pct, 2),
            "market_cap": float(d.get("MKTCAP") or s["market_cap"]),
            "pe": float(d.get("PE") or s["pe"]),
            "pb": float(d.get("PB") or s["pb"]),
            "52w_high": float(d.get("52H") or s["52w_high"]),
            "52w_low": float(d.get("52L") or s["52w_low"]),
            "volume": int(float(d.get("VOL") or s["volume"])),
            "status": status,
            "timestamp": str(d.get("lastupd") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "source": self.name(),
            "quality": "🟢 Live (Moneycontrol)",
            "exchange": "NSE",
        }

    def get_stock(self, symbol: str) -> dict[str, Any] | None:
        key = symbol.strip().upper()
        for s in STOCKS:
            if s["symbol"] == key or key in s["name"].upper():
                try:
                    return self._live_stock(s)
                except Exception as exc:  # noqa: BLE001
                    self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self._fallback_reason = f"{s['symbol']}: {exc}"
                    self._mark_degraded()
                    return self._mock.get_stock(symbol)
        return None

    def list_stocks(self) -> list[dict[str, Any]]:
        return [self.get_stock(s["symbol"]) for s in STOCKS]

    # ------------------------------------------------------------------ ohlc

    def ohlc(self, symbol: str, days: int = 200) -> list[dict[str, float]]:
        """Real daily OHLC from Moneycontrol techCharts, falling back to mock."""
        s = self.get_stock(symbol)
        if not s:
            raise ValueError(f"unknown symbol: {symbol}")
        ticker = MC_STOCK_TICKERS.get(s["symbol"], s["symbol"])
        try:
            payload = self._get_json(
                f"{MC_TECH_CHARTS}/history",
                params={"symbol": ticker, "resolution": "D", "countback": max(1, days), "to": int(time.time())},
                ttl=300.0,
            )
            if not isinstance(payload, dict) or payload.get("s") != "ok":
                raise ValueError(f"history status: {payload.get('errmsg', 'unknown')}")
            ts, o, h, l, c = payload.get("t", []), payload.get("o", []), payload.get("h", []), payload.get("l", []), payload.get("c", [])
            v = payload.get("v", [])
            bars: list[dict[str, float]] = []
            for i, t in enumerate(ts):
                bars.append({
                    "date": datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open": round(float(o[i]), 2),
                    "high": round(float(h[i]), 2),
                    "low": round(float(l[i]), 2),
                    "close": round(float(c[i]), 2),
                    "volume": round(float(v[i])) if i < len(v) else 0,
                })
            if not bars:
                raise ValueError("no bars returned")
            # snap the final close to the current live price so views stay consistent
            bars[-1]["close"] = s["price"]
            bars[-1]["high"] = max(bars[-1]["high"], s["price"])
            bars[-1]["low"] = min(bars[-1]["low"], s["price"])
            self._last_live = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return bars
        except Exception as exc:  # noqa: BLE001
            self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fallback_reason = f"{symbol} history: {exc}"
            self._mark_degraded()
            return self._mock.ohlc(symbol, days)

    # ------------------------------------------------------------------ orderbook

    def orderbook(self, symbol: str) -> dict[str, Any] | None:
        """Live orderbook via Moneycontrol's market-depth pricefeed, mock fallback."""
        s = self.get_stock(symbol)
        if not s:
            return None
        sc_id = MC_STOCK_IDS.get(s["symbol"])
        if not sc_id:
            return self._mock.orderbook(symbol)
        try:
            payload = self._get_json(f"{MC_PRICE_FEED}/nse/equitycash/{sc_id}")
            d = payload["data"]
            asks = d.get("ask") or {}
            bids = d.get("bid") or {}
            levels = []
            for i in range(1, 6):
                ask = float(asks.get(f"price{i}") or 0)
                ask_qty = int(asks.get(f"qty{i}") or 0)
                bid = float(bids.get(f"price{i}") or 0)
                bid_qty = int(bids.get(f"qty{i}") or 0)
                if not (ask or bid):
                    continue
                levels.append({
                    "level": i,
                    "bid": round(bid, 2), "bid_qty": bid_qty,
                    "ask": round(ask, 2), "ask_qty": ask_qty,
                })
            return {
                "symbol": s["symbol"],
                "source": self.name(),
                "quality": "🟢 Live (Moneycontrol)",
                "timestamp": str(d.get("lastupd") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "levels": levels,
            }
        except Exception as exc:  # noqa: BLE001
            self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fallback_reason = f"{symbol} orderbook: {exc}"
            self._mark_degraded()
            return self._mock.orderbook(symbol)

    # ------------------------------------------------------------------ status

    def status(self) -> dict[str, str]:
        if self._last_live is None and self._last_fallback is None:
            try:
                self.get_indices()
            except Exception:  # noqa: BLE001
                pass
        mode = "live" if self._last_live else ("fallback" if self._last_fallback else "unknown")
        return {
            "provider": self.name(),
            "mode": mode,
            "data": "moneycontrol-pricefeed" if self._last_live else "mock-nse-demo",
            "updated": self._last_live or self._last_fallback or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fallback_reason": self._fallback_reason or "",
            "quality": "🟢 Live (Moneycontrol)" if self._last_live else "🟡 Delayed (demo data)",
        }


class UpstoxMarketProvider:
    """Live Indian market data from the Upstox v3 REST market-quote endpoints.

    Uses the same access token / session lifecycle as the execution layer, so
    quotes are genuinely live when the broker is configured (sandbox/live) and
    fall back to Moneycontrol -> mock deterministically when it is not. Every
    record is labelled with its true source. This is the broker-grade price
    feed behind the SIGNAL ENGINE: prices are never invented here.
    """

    def __init__(self) -> None:
        self._mc = MoneycontrolMarketProvider()
        self._mock = MockMarketProvider()
        self._last_live: str | None = None
        self._last_fallback: str | None = None
        self._fallback_reason: str | None = None

    def name(self) -> str:
        return "upstox-live"

    def _broker_ready(self) -> bool:
        try:
            from . import upstox as u

            cfg = u.get_broker_settings()
            # Market data is only served in LIVE mode. Sandbox is order-only, so
            # the quote feed falls back to Moneycontrol there (no wasted calls).
            if cfg.mode != u.MODE_LIVE or not cfg.api_key:
                return False
            tok = u.SessionManager(cfg).ensure_access_token()
            return bool(tok)
        except Exception:  # noqa: BLE001
            return False

    def _quote(self, symbol: str) -> dict[str, Any] | None:
        """Best-effort live quote from Upstox; None when the feed is unavailable."""
        try:
            from . import upstox as u

            cfg = u.get_broker_settings()
            rows = u.load_instruments()
            key = u.resolve_instrument(symbol, rows)
            if not key:
                raise ValueError(f"no instrument key for {symbol}")
            token = u.SessionManager(cfg).ensure_access_token()
            data = u.UpstoxClient(cfg, token).quotes(key)
            raw = (data.get("data") or {}).get(key) or {}
            if not raw:
                raise ValueError(f"no quote payload for {key}")
            ltp = float(raw.get("last_price") or 0)
            ohlc = raw.get("ohlc") or {}
            prev = float((ohlc or {}).get("close") or 0)
            if ltp <= 0:
                raise ValueError(f"empty LTP for {key}")
            return {
                "instrument_token": key,
                "ltp": ltp,
                "prev_close": prev,
                "open": float((ohlc or {}).get("open") or 0),
                "high": float((ohlc or {}).get("high") or 0),
                "low": float((ohlc or {}).get("low") or 0),
                "volume": int(raw.get("volume") or 0),
                "timestamp": str(raw.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "source": self.name(),
            }
        except Exception as exc:  # noqa: BLE001
            self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fallback_reason = f"{symbol}: {exc}"
            return None

    def get_stock(self, symbol: str) -> dict[str, Any] | None:
        key = symbol.strip().upper()
        base = None
        for s in STOCKS:
            if s["symbol"] == key or key in s["name"].upper():
                base = s
                break
        if not base:
            return None
        q = self._quote(base["symbol"])
        if q and q.get("ltp"):
            self._last_live = q["timestamp"]
            price = q["ltp"]
            prev = q["prev_close"] or base["prev_close"]
            return {
                **base,
                "price": price,
                "prev_close": prev,
                "change": round(price - prev, 2),
                "change_pct": round((price - prev) / prev * 100, 2) if prev else 0,
                "volume": q.get("volume") or base["volume"],
                "status": "🟢 Open",
                "timestamp": q["timestamp"],
                "source": self.name(),
                "quality": "🟢 Live (Upstox)",
                "exchange": "NSE",
                "instrument_token": q.get("instrument_token"),
            }
        return self._mc.get_stock(symbol)

    def get_indices(self) -> list[dict[str, Any]]:
        return self._mc.get_indices()

    def list_stocks(self) -> list[dict[str, Any]]:
        return [self.get_stock(s["symbol"]) for s in STOCKS]

    def ohlc(self, symbol: str, days: int = 200) -> list[dict[str, float]]:
        """Live OHLC from the Upstox OHLC endpoint when reachable, else MC/mock."""
        try:
            from . import upstox as u

            cfg = u.get_broker_settings()
            rows = u.load_instruments()
            key = u.resolve_instrument(symbol, rows)
            if not key:
                raise ValueError(f"no instrument key for {symbol}")
            token = u.SessionManager(cfg).ensure_access_token()
            data = u.UpstoxClient(cfg, token).ohlc(key)
            raw = (data.get("data") or {}).get(key) or {}
            if not raw:
                raise ValueError(f"no OHLC payload for {key}")
            ohlc = raw.get("ohlc") or {}
            candles = raw.get("candles") or []
            if not candles:
                raise ValueError("no candle series")
            bars = []
            for c in candles[:days]:
                bars.append({
                    "date": str(c.get("timestamp") or c.get("date") or ""),
                    "open": round(float(c.get("open") or 0), 2),
                    "high": round(float(c.get("high") or 0), 2),
                    "low": round(float(c.get("low") or 0), 2),
                    "close": round(float(c.get("close") or 0), 2),
                    "volume": round(float(c.get("volume") or 0)),
                })
            if bars:
                self._last_live = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                return bars
            raise ValueError("empty candle series")
        except Exception as exc:  # noqa: BLE001
            self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fallback_reason = f"{symbol} ohlc: {exc}"
            return self._mc.ohlc(symbol, days)

    def orderbook(self, symbol: str) -> dict[str, Any] | None:
        """Live 5-level orderbook from the Upstox quote depth, else MC/mock."""
        try:
            from . import upstox as u

            cfg = u.get_broker_settings()
            rows = u.load_instruments()
            key = u.resolve_instrument(symbol, rows)
            if not key:
                raise ValueError(f"no instrument key for {symbol}")
            token = u.SessionManager(cfg).ensure_access_token()
            data = u.UpstoxClient(cfg, token).quotes(key)
            raw = (data.get("data") or {}).get(key) or {}
            depth = raw.get("depth") or {}
            if not depth:
                raise ValueError(f"no depth for {key}")
            levels = []
            for i in range(1, 6):
                buy = (depth.get("buy") or [])[i - 1] if i <= len(depth.get("buy") or []) else {}
                sell = (depth.get("sell") or [])[i - 1] if i <= len(depth.get("sell") or []) else {}
                levels.append({
                    "level": i,
                    "bid": float((buy or {}).get("price") or 0),
                    "bid_qty": int((buy or {}).get("quantity") or 0),
                    "ask": float((sell or {}).get("price") or 0),
                    "ask_qty": int((sell or {}).get("quantity") or 0),
                })
            self._last_live = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {
                "symbol": symbol.strip().upper(),
                "source": self.name(),
                "quality": "🟢 Live (Upstox)",
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "instrument_token": key,
                "levels": levels,
            }
        except Exception as exc:  # noqa: BLE001
            self._last_fallback = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._fallback_reason = f"{symbol} orderbook: {exc}"
            return self._mc.orderbook(symbol)

    def status(self) -> dict[str, str]:
        if self._last_live is None and self._last_fallback is None:
            try:
                self.get_indices()
            except Exception:  # noqa: BLE001
                pass
        mode = "live" if self._last_live else ("fallback" if self._last_fallback else "unknown")
        return {
            "provider": self.name(),
            "mode": mode,
            "data": "upstox-market-quote" if self._last_live else "moneycontrol/mock",
            "updated": self._last_live or self._last_fallback or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "fallback_reason": self._fallback_reason or "",
            "quality": "🟢 Live (Upstox)" if self._last_live else "🟡 Delayed (demo data)",
        }


def get_provider() -> MarketDataProvider:
    """Return the configured provider: Upstox live when available, else live
    Moneycontrol with mock fallback.

    MARKET_DATA_SOURCE env selects the preference: 'upstox' (default) tries the
    broker feed first, 'moneycontrol' uses the public feed directly, 'mock'
    forces the deterministic demo provider.
    """
    global _provider
    if _provider is not None:
        return _provider
    source = (os.environ.get("MARKET_DATA_SOURCE") or "upstox").strip().lower()
    if source == "mock":
        _provider = MockMarketProvider()
    elif source == "moneycontrol":
        _provider = MoneycontrolMarketProvider()
    else:
        try:
            up = UpstoxMarketProvider()
            if up._broker_ready():
                _provider = up
            else:
                _provider = MoneycontrolMarketProvider()
        except Exception:  # noqa: BLE001
            _provider = MoneycontrolMarketProvider()
    return _provider


def provider_quality(provider: MarketDataProvider | None = None) -> str:
    """Quality badge from the active provider (live vs delayed fallback)."""
    try:
        st = (provider or get_provider()).status()
        return st.get("quality", "🟡 Delayed (demo data)")
    except Exception:  # noqa: BLE001
        return "🟡 Delayed (demo data)"


# --------------------------------------------------------------------------- indicators

def sma(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            out.append(round(sum(values[i + 1 - period:i + 1]) / period, 4))
    return out


def ema(values: list[float], period: int) -> list[float | None]:
    if not values:
        return []
    k = 2 / (period + 1)
    out: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    prev = seed
    out.append(round(prev, 4))
    for v in values[period:]:
        prev = v * k + prev * (1 - k)
        out.append(round(prev, 4))
    return out


def rsi(values: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period + 1:
        return out
    gains, losses = 0.0, 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 2)
    for i in range(period + 1, len(values)):
        d = values[i] - values[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[i] = 100 if avg_l == 0 else round(100 - 100 / (1 + avg_g / avg_l), 2)
    return out


def macd(values: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float | None]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)
    line: list[float | None] = []
    for f, sl in zip(ema_fast, ema_slow):
        line.append(None if f is None or sl is None else round(f - sl, 4))
    # signal = EMA of the MACD line over the valid window
    valid = [v for v in line if v is not None]
    sig = ema(valid, signal)
    hist: list[float | None] = []
    idx = 0
    for v in line:
        if v is None:
            hist.append(None)
        else:
            hist.append(round(v - (sig[idx] or 0), 4))
            idx += 1
    return {"macd": line, "signal": sig, "histogram": hist}


def bollinger(values: list[float], period: int = 20, mult: float = 2.0) -> dict[str, list[float | None]]:
    mid = sma(values, period)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(values)):
        if mid[i] is None or i + 1 < period:
            upper.append(None)
            lower.append(None)
            continue
        window = values[i + 1 - period:i + 1]
        mean = sum(window) / period
        var = sum((x - mean) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper.append(round(mean + mult * sd, 4))
        lower.append(round(mean - mult * sd, 4))
    return {"mid": mid, "upper": upper, "lower": lower}


def atr(series: list[dict[str, float]], period: int = 14) -> list[float | None]:
    trs: list[float] = []
    for i in range(len(series)):
        h, l = series[i]["high"], series[i]["low"]
        pc = series[i - 1]["close"] if i > 0 else l
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    out: list[float | None] = [None] * len(series)
    if len(series) < period:
        return out
    at = sum(trs[:period]) / period
    out[period - 1] = round(at, 2)
    for i in range(period, len(series)):
        at = (at * (period - 1) + trs[i]) / period
        out[i] = round(at, 2)
    return out


def vwap(series: list[dict[str, float]]) -> float:
    pv = sum((s["high"] + s["low"] + s["close"]) / 3 * s["volume"] for s in series)
    v = sum(s["volume"] for s in series)
    return round(pv / v, 2) if v else 0.0


def support_resistance(series: list[dict[str, float]], window: int = 60) -> dict[str, float]:
    """Simple swing-high/low detection over the recent window."""
    recent = series[-window:]
    highs = [s["high"] for s in recent]
    lows = [s["low"] for s in recent]
    resistance = round(sorted(highs, reverse=True)[:3][0], 2)
    support = round(sorted(lows)[:3][0], 2)
    return {"support": support, "resistance": resistance}


def last(values: list[float | None]) -> float | None:
    for v in reversed(values):
        if v is not None:
            return v
    return None


def prev(values: list[float | None]) -> float | None:
    found = 0
    for v in reversed(values):
        if v is not None:
            found += 1
            if found == 2:
                return v
    return None


# --------------------------------------------------------------------------- technical view

def technical_view(symbol: str, days: int = 200) -> dict[str, Any]:
    provider = get_provider()
    s = provider.get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    series = provider.ohlc(symbol, days)
    closes = [c["close"] for c in series]
    vol = [c["volume"] for c in series]

    e20, e50 = ema(closes, 20), ema(closes, 50)
    r = rsi(closes, 14)
    m = macd(closes)
    bb = bollinger(closes, 20)
    at = atr(series, 14)
    sr = support_resistance(series)
    vp = vwap(series)

    price = closes[-1]
    trend = "Positive" if (last(e20) or price) > (last(e50) or price) else "Negative"
    rsi_now = last(r)
    momentum = "Positive" if rsi_now is not None and rsi_now > 50 else "Negative"
    # volume trend: compare last-5 avg vs prior-5 avg
    vol_avg_5 = sum(vol[-5:]) / 5
    vol_avg_prev = sum(vol[-10:-5]) / 5
    vol_trend = "Increasing" if vol_avg_5 > vol_avg_prev else "Decreasing"
    atr_now = last(at) or 0.0
    volatility = "Low" if atr_now < price * 0.015 else "Elevated" if atr_now < price * 0.025 else "High"

    return {
        "symbol": s["symbol"], "name": s["name"], "price": price,
        "trend": {"value": trend, "evidence": f"price {price} vs 20-EMA {last(e20)} and 50-EMA {last(e50)}"},
        "momentum": {"value": momentum, "evidence": f"RSI(14) = {rsi_now}"},
        "volatility": {"value": volatility, "evidence": f"ATR(14) = {atr_now} ({round(atr_now / price * 100, 2)}% of price)"},
        "volume": {"value": vol_trend, "evidence": f"5-day avg {round(vol_avg_5):,} vs prior {round(vol_avg_prev):,}"},
        "support": sr["support"], "resistance": sr["resistance"],
        "rsi": rsi_now, "macd": last(m["macd"]), "macd_signal": last(m["signal"]),
        "bb_upper": last(bb["upper"]), "bb_lower": last(bb["lower"]),
        "atr": atr_now, "vwap": vp,
        "last_close": closes[-5:],
        "source": provider.name(), "quality": provider_quality(provider),
    }


# --------------------------------------------------------------------------- fundamental view

def fundamental_view(symbol: str) -> dict[str, Any]:
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    return {
        "symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
        "market_cap": s["market_cap"], "pe": s["pe"], "pb": s["pb"],
        "roe": s["roe"], "roce": s["roce"], "de": s["de"],
        "div_yield": s["div_yield"], "promoter": s["promoter"],
        "eps": round(s["price"] / s["pe"], 2),
        "revenue_cr": round(s["market_cap"] / (s["pe"] / 100) / 100, 0) * 100,
        "quality": provider_quality(),
        "note": "Price, market cap, P/E and P/B are live from Moneycontrol where available; "
                "remaining fundamentals are demo estimates for the prototype.",
    }


# --------------------------------------------------------------------------- market scoring

def market_score(symbol: str) -> dict[str, Any]:
    """Transparent factor score. Every sub-score is traceable to evidence."""
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    tv = technical_view(symbol)

    def _factor(name: str, score: float, evidence: str) -> dict[str, Any]:
        return {"name": name, "score": score, "evidence": evidence}

    factors: list[dict[str, Any]] = []

    tech = 50 + (12 if tv["trend"]["value"] == "Positive" else -10) + (8 if tv["momentum"]["value"] == "Positive" else -8)
    factors.append(_factor("Technical Trend", max(0, min(100, tech)), tv["trend"]["evidence"]))

    mom = 50 + (20 if (tv["rsi"] or 50) > 60 else 8 if (tv["rsi"] or 50) > 50 else -12)
    factors.append(_factor("Momentum", max(0, min(100, mom)), f"RSI(14) = {tv['rsi']}"))

    vol = 50 + (15 if tv["volume"]["value"] == "Increasing" else -10)
    factors.append(_factor("Volume", max(0, min(100, vol)), tv["volume"]["evidence"]))

    pe, roe = s["pe"], s["roe"]
    fund = 50 + (min(25, (30 - pe) * 2) if pe < 30 else -12) + (min(20, (roe - 12) * 2) if roe > 12 else -10)
    factors.append(_factor("Fundamentals", max(0, min(100, fund)), f"P/E {pe}, ROE {roe}%"))

    sent = 50 + s["news_sentiment"] * 50
    factors.append(_factor("Sentiment", max(0, min(100, round(sent))), f"news sentiment {s['news_sentiment']:.2f}"))

    vola = 50 + (15 if tv["volatility"]["value"] == "Low" else -10 if tv["volatility"]["value"] == "High" else 0)
    factors.append(_factor("Risk", max(0, min(100, vola)), f"volatility = {tv['volatility']['value']} ({tv['volatility']['evidence']})"))

    weights = {"Technical Trend": 0.22, "Momentum": 0.18, "Volume": 0.12, "Fundamentals": 0.18, "Sentiment": 0.15, "Risk": 0.15}
    total = round(sum(f["score"] * weights[f["name"]] for f in factors))
    return {"symbol": s["symbol"], "name": s["name"], "score": total, "factors": factors, "model": "mkt-score-v1", "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# --------------------------------------------------------------------------- signal engine

def signal_engine(symbol: str) -> dict[str, Any]:
    """Composite signal from multiple independent factors. Never a single indicator."""
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    sc = market_score(symbol)
    tv = technical_view(symbol)

    checks: list[dict[str, Any]] = []
    ok = 0
    # trend
    if tv["trend"]["value"] == "Positive":
        ok += 1; checks.append({"factor": "Technical Trend", "support": "BUY", "evidence": tv["trend"]["evidence"]})
    else:
        checks.append({"factor": "Technical Trend", "support": "SELL", "evidence": tv["trend"]["evidence"]})
    # momentum
    if (tv["rsi"] or 50) >= 50:
        ok += 1; checks.append({"factor": "Momentum (RSI)", "support": "BUY", "evidence": f"RSI={tv['rsi']}"})
    else:
        checks.append({"factor": "Momentum (RSI)", "support": "SELL", "evidence": f"RSI={tv['rsi']}"})
    # volume
    if tv["volume"]["value"] == "Increasing":
        ok += 1; checks.append({"factor": "Volume", "support": "BUY", "evidence": tv["volume"]["evidence"]})
    else:
        checks.append({"factor": "Volume", "support": "SELL", "evidence": tv["volume"]["evidence"]})
    # fundamentals
    if s["pe"] < 30 and s["roe"] > 12:
        ok += 1; checks.append({"factor": "Fundamentals", "support": "BUY", "evidence": f"P/E {s['pe']}, ROE {s['roe']}%"})
    else:
        checks.append({"factor": "Fundamentals", "support": "SELL", "evidence": f"P/E {s['pe']}, ROE {s['roe']}%"})
    # sentiment
    if s["news_sentiment"] >= 0.15:
        ok += 1; checks.append({"factor": "Sentiment", "support": "BUY", "evidence": f"sentiment {s['news_sentiment']:.2f}"})
    else:
        checks.append({"factor": "Sentiment", "support": "SELL", "evidence": f"sentiment {s['news_sentiment']:.2f}"})

    ratio = ok / len(checks)
    if ratio >= 0.8:
        signal = "BUY CANDIDATE"
    elif ratio <= 0.2:
        signal = "SELL / REDUCE RISK"
    elif ratio >= 0.5:
        signal = "HOLD / WATCH"
    else:
        signal = "NO SIGNAL"

    confidence = round(ratio * 100)
    return {
        "symbol": s["symbol"], "name": s["name"], "signal": signal, "confidence": confidence,
        "supporting": ok, "total_factors": len(checks),
        "checks": checks,
        "ai_score": sc["score"],
        "regime": market_regime()["regime"],
        "evidence_strength": "High" if ok >= 5 else "Moderate" if ok >= 3 else "Low",
        "model": "composite-signal-v1",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "disclaimer": "Signal is a research aid, not investment advice. Confidence reflects factor agreement, not a calibrated probability.",
    }


# --------------------------------------------------------------------------- regime engine

def regime_engine() -> dict[str, Any]:
    """Detect the market regime from index momentum and breadth."""
    idx = get_provider().get_indices()
    nifty = next((i for i in idx if i["symbol"] == "NIFTY 50"), idx[0])
    pos = sum(1 for i in idx if i["change"] > 0)
    breadth = pos / len(idx)
    nifty_up = nifty["change"] > 0

    if nifty_up and breadth >= 0.6:
        regime, tone = "Bull", "Risk-On"
    elif not nifty_up and breadth <= 0.4:
        regime, tone = "Bear", "Risk-Off"
    else:
        regime, tone = "Sideways", "Mixed"
    # volatility qualifier
    if abs(nifty["pct"]) > 1.0:
        regime = f"{regime} / High Volatility"
    return {
        "regime": regime, "tone": tone, "breadth": round(breadth, 2),
        "nifty_pct": nifty["pct"], "evidence": f"{pos}/{len(idx)} indices up; NIFTY {nifty['change']:+,.2f}",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


market_regime = regime_engine  # alias used by signal_engine


# --------------------------------------------------------------------------- screener

def screener(filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    f = filters or {}
    out: list[dict[str, Any]] = []
    for s in get_provider().list_stocks():
        score = market_score(s["symbol"])
        if f.get("min_market_cap") and s["market_cap"] < f["min_market_cap"]:
            continue
        if f.get("sector") and s["sector"] != f["sector"]:
            continue
        if f.get("min_rsi") and (score.get("__rsi", 50) if False else False):
            continue
        if f.get("max_pe") and s["pe"] > f["max_pe"]:
            continue
        if f.get("min_momentum") and s["momentum"] < f["min_momentum"]:
            continue
        if f.get("min_score") and score["score"] < f["min_score"]:
            continue
        out.append({"symbol": s["symbol"], "name": s["name"], "sector": s["sector"],
                    "price": s["price"], "change_pct": s["change_pct"], "market_cap": s["market_cap"],
                    "pe": s["pe"], "rsi": score.get("rsi"), "ai_score": score["score"]})
    out.sort(key=lambda r: r["ai_score"], reverse=True)
    return out


# --------------------------------------------------------------------------- risk engine

def position_size(symbol: str, capital: float, risk_per_trade_pct: float = 2.0,
                  stop_distance_pct: float | None = None) -> dict[str, Any]:
    """Position sizing from max risk per trade and stop distance. Never a blind quantity."""
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    price = s["price"]
    if stop_distance_pct is None:
        tv = technical_view(symbol)
        stop_distance_pct = max(2.0, (price - tv["support"]) / price * 100)
    max_risk = capital * risk_per_trade_pct / 100
    stop_dist = price * stop_distance_pct / 100
    qty = math.floor(max_risk / stop_dist)
    qty = max(1, qty)
    return {
        "symbol": s["symbol"], "price": price,
        "capital": capital, "risk_per_trade_pct": risk_per_trade_pct,
        "max_risk": round(max_risk, 2), "stop_distance_pct": round(stop_distance_pct, 2),
        "stop_loss_price": round(price - stop_dist, 2),
        "max_quantity": qty, "notional": round(price * qty, 2),
        "explanation": f"Max risk ₹{max_risk:,.2f} ÷ stop distance ₹{stop_dist:,.2f} = {qty} units",
    }


def portfolio_risk(positions: list[dict[str, Any]], capital: float) -> dict[str, Any]:
    """Exposure, concentration and drawdown checks for a set of positions."""
    total_value = sum(p.get("value", p.get("quantity", 0) * p.get("price", 0)) for p in positions)
    exposure = total_value / capital * 100 if capital else 0
    sector_exposure: dict[str, float] = {}
    for p in positions:
        s = get_provider().get_stock(p.get("symbol", ""))
        sector = s["sector"] if s else "Unknown"
        sector_exposure[sector] = sector_exposure.get(sector, 0) + p.get("value", 0)
    top_sector = max(sector_exposure.items(), key=lambda kv: kv[1]) if sector_exposure else ("None", 0)
    top_pct = top_sector[1] / total_value * 100 if total_value else 0
    concentration = max((p.get("value", 0) / total_value * 100 if total_value else 0) for p in positions) if positions else 0
    flags = []
    if exposure > 90: flags.append("⚠ Portfolio exposure above 90%")
    if top_pct > 40: flags.append(f"⚠ Sector concentration: {top_sector[0]} {top_pct:.0f}%")
    if concentration > 50: flags.append(f"⚠ Single-position concentration {concentration:.0f}%")
    if not flags: flags.append("✓ Within configured risk limits")
    return {
        "total_value": round(total_value, 2), "exposure_pct": round(exposure, 1),
        "top_sector": top_sector[0], "top_sector_pct": round(top_pct, 1),
        "max_position_pct": round(concentration, 1),
        "flags": flags,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# --------------------------------------------------------------------------- backtest

def backtest(symbol: str, entry: str, exit_rule: str, stop_loss_pct: float = 8.0,
             target_pct: float | None = None, days: int = 500,
             commission: float = 0.03, slippage: float = 0.05) -> dict[str, Any]:
    """Simple strategy backtest on real Moneycontrol OHLC (mock fallback offline).

    entry/exit are human-readable labels (recorded, not executed by a black box).
    Returns standard metrics. Past performance ≠ future results.
    """
    provider = get_provider()
    s = provider.get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    series = provider.ohlc(symbol, days)
    closes = [c["close"] for c in series]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    r = rsi(closes, 14)

    trades: list[dict[str, Any]] = []
    in_position = False
    entry_price = 0.0
    for i in range(1, len(series)):
        c = closes[i]
        e20v, e50v, rv = e20[i], e50[i], r[i]
        if not in_position and e20v is not None and e50v is not None and e20v > e50v and rv is not None and rv > 50:
            in_position = True
            entry_price = c
        elif in_position:
            stop = entry_price * (1 - stop_loss_pct / 100)
            target = entry_price * (1 + target_pct / 100) if target_pct else None
            if c <= stop or (target and c >= target):
                exit_price = c
                ret = (exit_price - entry_price) / entry_price * 100 - commission - slippage
                trades.append({"entry": round(entry_price, 2), "exit": round(exit_price, 2),
                               "return_pct": round(ret, 2)})
                in_position = False

    equity = 100.0
    curve = [100.0]
    for t in trades:
        equity *= (1 + t["return_pct"] / 100)
        curve.append(round(equity, 2))
    while len(curve) < 30:
        curve.append(round(curve[-1], 2))

    returns = [t["return_pct"] / 100 for t in trades]
    n = len(returns)
    total_return = (equity / 100 - 1) * 100
    years = max(days / 252, 0.01)
    cagr = ((equity / 100) ** (1 / years) - 1) * 100 if equity > 0 else -100
    max_dd = _max_drawdown(curve)
    avg = sum(returns) / n if n else 0
    sd = math.sqrt(sum((r - avg) ** 2 for r in returns) / n) if n else 0
    sharpe = avg / sd * math.sqrt(252) if sd > 0 else 0
    downside = [r for r in returns if r < 0]
    dsd = math.sqrt(sum(r * r for r in downside) / n) if n else 0
    sortino = avg / dsd * math.sqrt(252) if dsd > 0 else 0
    wins = [r for r in returns if r > 0]
    win_rate = len(wins) / n * 100 if n else 0
    gross_win = sum(w for w in wins)
    gross_loss = -sum(r for r in returns if r < 0)
    profit_factor = gross_win / gross_loss if gross_loss else 0
    rr = avg / abs(sum(r for r in returns if r < 0) / max(len([x for x in returns if x < 0]), 1)) if any(r < 0 for r in returns) else 0

    return {
        "symbol": s["symbol"], "entry_rule": entry, "exit_rule": exit_rule,
        "stop_loss_pct": stop_loss_pct, "target_pct": target_pct,
        "days": days, "n_trades": n,
        "total_return_pct": round(total_return, 2), "cagr_pct": round(cagr, 2),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe": float(round(sharpe, 2)), "sortino": float(round(sortino, 2)),
        "win_rate_pct": round(win_rate, 1), "profit_factor": round(profit_factor, 2),
        "avg_trade_pct": round(avg * 100, 2), "risk_reward": round(rr, 2),
        "equity_curve": curve,
        "strategy_quality": _strategy_quality(cagr, max_dd, sharpe, n),
        "data_source": provider.name(), "quality": provider_quality(provider),
        "disclaimer": "Backtest uses real historical OHLC where available (delayed demo fallback offline). Past performance does not guarantee future results.",
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)
    return max_dd


def _strategy_quality(cagr: float, max_dd: float, sharpe: float, n_trades: int) -> dict[str, str]:
    if n_trades < 10:
        return {"grade": "Caution", "reason": "Too few trades to evaluate."}
    if sharpe > 1.0 and max_dd < 20 and cagr > 0:
        return {"grade": "Good", "reason": f"Sharpe {sharpe:.2f}, max drawdown {max_dd:.1f}%, positive CAGR."}
    if sharpe < 0.5 or max_dd > 35:
        return {"grade": "Poor", "reason": f"Sharpe {sharpe:.2f} or max drawdown {max_dd:.1f}% — high risk of overfitting."}
    return {"grade": "Caution", "reason": "Mixed risk/reward profile; validate out-of-sample."}


# --------------------------------------------------------------------------- paper trading

def _paper_file() -> Path:
    return DATA_DIR / "paper_portfolio.json"


def _load_paper() -> dict[str, Any]:
    f = _paper_file()
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"cash": 1_000_000, "positions": [], "trades": [], "capital": 1_000_000}


def _save_paper(state_: dict[str, Any]) -> None:
    _paper_file().parent.mkdir(parents=True, exist_ok=True)
    _paper_file().write_text(json.dumps(state_, indent=2, ensure_ascii=False), encoding="utf-8")


def paper_portfolio() -> dict[str, Any]:
    st = _load_paper()
    positions = []
    total_value = st["cash"]
    for p in st["positions"]:
        s = get_provider().get_stock(p["symbol"])
        price = s["price"] if s else p.get("entry", 0)
        value = price * p["quantity"]
        total_value += value
        positions.append({**p, "price": price, "value": round(value, 2),
                          "unrealized_pnl": round((price - p["entry"]) * p["quantity"], 2),
                          "pnl_pct": round((price - p["entry"]) / p["entry"] * 100, 2) if p["entry"] else 0})
    st["positions"] = positions
    st["total_value"] = round(total_value, 2)
    st["pnl"] = round(total_value - st["capital"], 2)
    st["return_pct"] = round(st["pnl"] / st["capital"] * 100, 2) if st["capital"] else 0
    st["exposure_pct"] = round((total_value - st["cash"]) / total_value * 100, 1) if total_value else 0
    realized = sum(t.get("pnl", 0) for t in st["trades"])
    wins = sum(1 for t in st["trades"] if t.get("pnl", 0) > 0)
    st["realized_pnl"] = round(realized, 2)
    st["win_rate_pct"] = round(wins / len(st["trades"]) * 100, 1) if st["trades"] else 0
    st["mode"] = "paper"
    return st


def _daily_loss_equity_provider() -> dict[str, Any]:
    """Equity snapshot for the daily-loss breaker from the paper portfolio."""
    st = paper_portfolio()
    return {"equity": st["total_value"], "realized_today": st["realized_pnl"]}


cb.get_guard().set_equity_provider(_daily_loss_equity_provider)


def _quote_age_hours(s: dict[str, Any]) -> float | None:
    """Best-effort age in hours of a quote from its timestamp; None if unparseable."""
    ts = (s or {}).get("timestamp", "")
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("T", " "))
            dt = dt.replace(tzinfo=None) if dt.tzinfo else dt
        except ValueError:
            return None
    age = (datetime.now() - dt).total_seconds()
    return max(0.0, age) / 3600.0


MAX_QUOTE_AGE_HOURS = 72.0


def _fresh_quote(s: dict[str, Any], max_age: float = MAX_QUOTE_AGE_HOURS) -> tuple[bool, str]:
    """Paper-trading freshness gate: refuse stale/unparseable quotes (fail-closed)."""
    if not s:
        return False, "no quote available"
    age = _quote_age_hours(s)
    if age is None:
        return False, "quote timestamp unavailable"
    if age > max_age:
        return False, f"quote {age:.1f} hours old exceeds {max_age:g}-h limit"
    return True, f"quote {age:.2f} hours old"


def _paper_gate(s: dict[str, Any]) -> None:
    fresh, why = _fresh_quote(s)
    if not fresh:
        raise ValueError(f"paper trade refused (fail-closed): {why}")


def paper_buy(symbol: str, quantity: int, stop_loss: float | None = None, target: float | None = None) -> dict[str, Any]:
    safety_gate("paper_stock", f"BUY {symbol} x {quantity}")
    cb.check_open(f"paper buy {symbol} x {quantity}")
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    _paper_gate(s)
    st = _load_paper()
    price = s["price"]
    cost = price * quantity
    if cost > st["cash"]:
        raise ValueError(f"insufficient cash: need ₹{cost:,.2f}, have ₹{st['cash']:,.2f}")
    st["cash"] -= cost
    st["positions"].append({
        "symbol": s["symbol"], "quantity": quantity, "entry": price,
        "stop_loss": stop_loss or round(price * 0.92, 2),
        "target": target, "opened": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    st["trades"].append({"type": "BUY", "symbol": s["symbol"], "quantity": quantity,
                         "price": price, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "pnl": 0})
    _save_paper(st)
    return {"status": "PAPER TRADE executed (simulated, no real money)", "symbol": s["symbol"],
            "quantity": quantity, "price": price, "cash": round(st["cash"], 2)}


def paper_sell(symbol: str, quantity: int) -> dict[str, Any]:
    safety_gate("paper_stock", f"SELL {symbol} x {quantity}")
    s = get_provider().get_stock(symbol)
    if not s:
        raise ValueError(f"unknown symbol: {symbol}")
    _paper_gate(s)
    st = _load_paper()
    pos = next((p for p in st["positions"] if p["symbol"] == s["symbol"]), None)
    if not pos or pos["quantity"] < quantity:
        raise ValueError("position not found or insufficient quantity")
    price = s["price"]
    pos["quantity"] -= quantity
    pnl = (price - pos["entry"]) * quantity
    st["cash"] += price * quantity
    if pos["quantity"] == 0:
        st["positions"].remove(pos)
    st["trades"].append({"type": "SELL", "symbol": s["symbol"], "quantity": quantity,
                         "price": price, "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "pnl": round(pnl, 2)})
    _save_paper(st)
    cb.record_trade(round(pnl, 2))
    return {"status": "PAPER TRADE executed (simulated, no real money)", "symbol": s["symbol"],
            "quantity": quantity, "price": price, "pnl": round(pnl, 2), "cash": round(st["cash"], 2)}


# --------------------------------------------------------------------------- news / sentiment

NEWS = [
    {"title": "RBI keeps repo rate unchanged, signals liquidity support", "source": "Mock News Wire", "sector": "Economy", "sentiment": 0.4, "time": "2h ago"},
    {"title": "IT majors report steady Q2; deal pipeline stable", "source": "Mock News Wire", "sector": "IT Services", "sentiment": 0.3, "time": "4h ago"},
    {"title": "Crude prices ease, supporting refining margins", "source": "Mock News Wire", "sector": "Energy", "sentiment": 0.35, "time": "5h ago"},
    {"title": "Auto sales volumes pick up ahead of festive season", "source": "Mock News Wire", "sector": "Automobile", "sentiment": 0.45, "time": "6h ago"},
    {"title": "Banking credit growth remains healthy", "source": "Mock News Wire", "sector": "Banking", "sentiment": 0.28, "time": "8h ago"},
    {"title": "Regulatory review of FMCG pricing continues", "source": "Mock News Wire", "sector": "FMCG", "sentiment": -0.15, "time": "10h ago"},
    {"title": "Infrastructure capex announcements support demand outlook", "source": "Mock News Wire", "sector": "Infrastructure", "sentiment": 0.4, "time": "12h ago"},
    {"title": "Global markets mixed on rate-cut expectations", "source": "Mock News Wire", "sector": "Global", "sentiment": 0.1, "time": "1d ago"},
]


def news_sentiment(symbol: str | None = None) -> list[dict[str, str]]:
    if symbol:
        s = get_provider().get_stock(symbol)
        sector = s["sector"] if s else ""
        items = [n for n in NEWS if n["sector"] in (sector, "Economy", "Global")]
        return [{**n, "sentiment": ("Positive" if n["sentiment"] > 0.2 else "Negative" if n["sentiment"] < -0.1 else "Neutral")} for n in items]
    return [{**n, "sentiment": ("Positive" if n["sentiment"] > 0.2 else "Negative" if n["sentiment"] < -0.1 else "Neutral")} for n in NEWS]


# --------------------------------------------------------------------------- market brief

def market_brief() -> dict[str, Any]:
    regime = regime_engine()
    idx = get_provider().get_indices()
    top_gainers = [i for i in idx if i["change"] > 0][:3]
    top_losers = [i for i in idx if i["change"] < 0][:3]
    quality = provider_quality()
    live = "Live" in quality
    return {
        "summary": f"Markets are in a {regime['regime']} regime ({regime['tone']}). "
                   f"NIFTY 50 is {idx[0]['change']:+,.2f} ({idx[0]['pct']:+.2f}%). "
                   f"Breadth: {regime['breadth'] * 100:.0f}% of tracked indices up.",
        "regime": regime,
        "indices": idx,
        "top_gainers": [{"symbol": i["symbol"], "change_pct": i["pct"]} for i in top_gainers],
        "top_losers": [{"symbol": i["symbol"], "change_pct": i["pct"]} for i in top_losers],
        "news": news_sentiment()[:4],
        "ai_interpretation": (
            "Live interpretation from Moneycontrol data: breadth supports the current tone but "
            "volatility and market structure mean no certainty. Treat as research, not a call."
            if live else
            "Demo interpretation (provider offline, using delayed fallback): breadth supports the "
            "current tone but data is delayed and demo. Treat as research, not a call."
        ),
        "data_status": quality,
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
