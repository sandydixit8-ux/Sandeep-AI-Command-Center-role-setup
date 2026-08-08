/* ============================================================
   AI COMMAND CENTER — Option Chain Intelligence API client
   Thin fetch wrapper over the /api/v1/options/* endpoints.
   ============================================================ */
window.OptionsClient = (() => {
  "use strict";

  const BASE = "/api/v1";

  async function get(path) {
    const res = await fetch(BASE + path);
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
      headers: { "Content-Type": "application/json" },
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
    qs,
  };
})();
