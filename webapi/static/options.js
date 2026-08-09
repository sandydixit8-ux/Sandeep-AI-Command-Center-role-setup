/* ============================================================
   AI COMMAND CENTER — Option Chain Intelligence API client
   Thin fetch wrapper over the /api/v1/options/* endpoints.
   ============================================================ */
window.OptionsClient = (() => {
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

  return {
    analysis: (underlying, expiry) => get("/options/analysis" + qs({ underlying, expiry })),
    expiries: (underlying) => get("/options/expiries" + qs({ underlying })),
    metrics: (underlying, expiry) => get("/options/metrics" + qs({ underlying, expiry })),
    supportResistance: (underlying, expiry) => get("/options/support-resistance" + qs({ underlying, expiry })),
    scenarios: (underlying, expiry) => get("/options/scenarios" + qs({ underlying, expiry })),
    signal: (underlying, expiry) => get("/options/signal" + qs({ underlying, expiry })),
    strategies: (underlying, expiry) => get("/options/strategies" + qs({ underlying, expiry })),
    chain: (underlying, expiry, limit) => get("/options/chain" + qs({ underlying, expiry, limit })),
    unusualActivity: (underlying, expiry) => get("/options/unusual-activity" + qs({ underlying, expiry })),
    paperPositions: () => get("/options/paper/positions"),
    paperOpen: (p) => post("/options/paper/open" + qs(p)),
    backtest: (p) => get("/options/backtest" + qs(p)),
    backtestHistory: () => get("/options/backtest/history"),
    intel: (underlying, expiry, record) => get("/options/intel" + qs({ underlying, expiry, record })),
    futures: (underlying, expiry) => get("/options/futures" + qs({ underlying, expiry })),
    expiryCompare: (underlying, expiry) => get("/options/expiry" + qs({ underlying, expiry })),
    vol: (underlying, expiry) => get("/options/vol" + qs({ underlying, expiry })),
    velocity: (underlying, minutes) => get("/options/velocity" + qs({ underlying, minutes })),
    noTrade: (underlying, expiry) => get("/options/no-trade" + qs({ underlying, expiry })),
    signalPerformance: () => get("/options/signal-performance"),
    events: () => get("/options/events"),
    qs,
  };
})();
