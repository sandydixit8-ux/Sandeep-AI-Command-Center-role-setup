/* ============================================================
   AI COMMAND CENTER — Market Intelligence API client
   Thin fetch wrapper over the /api/v1/market/* endpoints.
   ============================================================ */
window.MarketClient = (() => {
  "use strict";

  const BASE = "/api/v1";

  function authHeaders(extra) {
    const token = window.API_TOKEN || (localStorage && localStorage.getItem("api_token")) || "";
    const h = { ...(extra || {}) };
    if (token) h["X-API-Key"] = token;
    return h;
  }

  async function get(path) {
    const res = await fetch(BASE + path, { headers: authHeaders() });
    if (!res.ok) {
      let detail = "Request failed (" + res.status + ")";
      try { const j = await res.json(); detail = j.detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  }

  async function post(path, body) {
    const res = await fetch(BASE + path, {
      method: "POST",
      headers: authHeaders({ "Content-Type": "application/json" }),
      body: body ? JSON.stringify(body) : null,
    });
    if (!res.ok) {
      let detail = "Request failed (" + res.status + ")";
      try { const j = await res.json(); detail = j.detail || detail; } catch { /* ignore */ }
      throw new Error(detail);
    }
    return res.json();
  }

  const qs = (params) => {
    const p = new URLSearchParams();
    Object.keys(params || {}).forEach(k => { const v = params[k]; if (v !== undefined && v !== null && v !== "") p.set(k, v); });
    const s = p.toString();
    return s ? "?" + s : "";
  };

  const q = (symbol) => "/market/quote/" + encodeURIComponent(symbol);

  return {
    status: () => get("/market/status"),
    indices: () => get("/market/indices"),
    stocks: () => get("/market/stocks"),
    brief: () => get("/market/brief"),
    regime: () => get("/market/regime"),
    news: (symbol) => get("/market/news" + (symbol ? qs({ symbol }) : "")),
    quote: (symbol) => get(q(symbol)),
    ohlc: (symbol, days) => get(q(symbol).replace("/quote/", "/ohlc/") + qs({ days })),
    technical: (symbol) => get(q(symbol).replace("/quote/", "/technical/")),
    fundamental: (symbol) => get(q(symbol).replace("/quote/", "/fundamental/")),
    score: (symbol) => get(q(symbol).replace("/quote/", "/score/")),
    signal: (symbol) => get(q(symbol).replace("/quote/", "/signal/")),
    screener: (filters) => get("/market/screener" + qs(filters)),
    positionSize: (symbol, capital, risk) => get("/market/position-size/" + encodeURIComponent(symbol) + qs({ capital, risk_per_trade_pct: risk })),
    backtest: (symbol, opts) => get("/market/backtest/" + encodeURIComponent(symbol) + qs(opts)),
    portfolioRisk: () => get("/market/portfolio-risk"),
    paperPortfolio: () => get("/paper-trading/portfolio"),
    paperBuy: (symbol, quantity) => post("/paper-trading/buy" + qs({ symbol, quantity })),
    paperSell: (symbol, quantity) => post("/paper-trading/sell" + qs({ symbol, quantity })),
    tradingStatus: () => get("/trading/status"),
    tradingStart: (interval, agent) => post("/trading/start" + qs({ interval, agent })),
    tradingStop: () => post("/trading/stop"),
    tradingPerformanceToday: () => get("/trading/performance/today"),
    qs,
  };
})();
