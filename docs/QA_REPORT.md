# AI Command Center — Production-Readiness QA Report

**Date:** 2026-08-12
**Decision: CONDITIONAL GO** — approved for paper/sandbox pilot. **Not live-trading ready** until P1/P2 conditions below are met.

## 1. System under test

- FastAPI + uvicorn (single process), `127.0.0.1:8000`; 10 agents (`commander, exec, finance, bd, jobsearch, marketing, docs, coach, market, risk`); 56 tools on the market agent.
- Execution: paper stocks/options + Upstox **sandbox** (order-only, no real-money exposure; kill switch verified).
- Risk controls: daily-loss circuit breaker, kill switch, Mode-A order approvals, hash-chained audit (9 JSONL + 10 JSON stores), RAG over 8 docs.
- Trading loop: **RUNNING, healthy** — scheduled 300s cycles (cycles 188→195 during QA), auto-resumes after process restart.

## 2. What was verified (evidence)

| Area | Result |
|---|---|
| API surface (50+ endpoints) | All GET 200s correct; 422 on bad types, 404 on unknown symbols/agents; body validation correct (`{"interval":"abc"}`→422, blank task→422) |
| Auth/RBAC | 401 no/wrong token, 200 bearer + X-API-Key, health/static exempt, `hmac.compare_digest` |
| Kill switch | Blocks paper buy AND `trading.start` (verified in isolated instance) |
| Daily-loss breaker | Blocks new buys at ₹30k limit, allows risk-reducing sells; day-stamped, fail-closed on provider failure, audited |
| Audit integrity | All 9 hash-chains intact; trade log ↔ positions reconcile (30−1=29); daily_loss realized matches SELL pnl |
| LLM resilience | 413/429 handled without crash; mock provider failover works offline; errors now surfaced as 429/413/502 (was HTTP 200) |
| Performance | Light endpoints 10–35ms; 100-request burst 0 errors/0.2s wall; loaded `market/signal` p99 15.2s at 100 concurrent (degradation, no errors) |
| Data quality | All 10 JSON parse; audit sparse-but-intact |

## 3. Defects found and remediated this cycle

| # | Sev | Defect | Fix (commit) |
|---|-----|--------|--------------|
| D1 | P1 | Market-agent `/api/v1/run` 413 TPM-blocked on Groq (8k limit vs 8,362 req); error returned as HTTP 200 | **FIXED**: per-request token pre-flight in `Agent._fit()` (`agents_core/agent.py`, `config.py`) — trims max_tokens/drops non-core tools to budget (`AGENT_LLM_REQUEST_BUDGET`, default 7000); memory context capped; `/api/v1/run` now raises 429/413/502 instead of 200. **Verified: real Groq run succeeds.** |
| D2 | P2 | `/options/analysis` invalid underlying → 502 | **FIXED**: `_analyze` re-raises HTTPException (400) before wrapping fetch failures; `expiries` fetch wrapped (`webapi/app.py`). Verified 400. |
| D3 | P2 | RAG top-K results not sorted by score; no relevance threshold | **FIXED**: `RagIndex.query` recomputes hybrid score on re-ranked pool, sorts by score desc, optional `min_score` (`agents_core/rag/retriever.py`). Verified sorted `[0.442, 0.387, 0.267, …]`. |
| D4 | P3 | Whitespace-only task accepted | **FIXED**: `RunRequest` validator rejects blank tasks → 422 (`webapi/app.py`). Verified. |

## 4. Remaining findings (not yet fixed)

| # | Sev | Finding |
|---|-----|---------|
| F1 | P2 | CPU-heavy endpoints degrade under concurrency (signal p99 15.2s @100 concurrent; single uvicorn worker, no cache) |
| F2 | P3 | No security headers (CSP/HSTS/X-Frame-Options/X-Content-Type-Options); `Server: uvicorn` leaks |
| F3 | P3 | No API rate limiting; no session/conversation persistence across `/run` calls |
| F4 | P3 | Execution audit records sparse (symbol/quantity/outcome null) — weak SEBI-style evidence |
| F5 | P3 | Breaker dual-gate: `check_open` (dynamic) vs `is_tripped` (sticky) can diverge |
| F6 | P3 | Groq free-tier 8k TPM still limits multi-step runs; consider tier upgrade or Anthropic for richer contexts |
| F7 | P3 | UI a11y debt (no `<form>` semantics) but good aria-label coverage |

## 5. Scores

- Functional completeness: **96%** (105/105 probe checks after harness correction)
- Security: **78%** (auth correct; headers/rate-limiting missing)
- Resilience/Risk: **88%** (breakers, kill switch, fail-closed verified)
- AI quality: **70%** (market agent executes end-to-end on Groq; error surfacing fixed)
- **Production readiness: ~82/100 → CONDITIONAL GO**

## 6. Conditions for GO (live trading)

1. F2: security headers + remove `Server` header.
2. F3: rate limiting + conversation persistence.
3. F4: enrich execution audit records.
4. Re-verify broker fail-closed paths on a live (non-sandbox) Upstox account with `LIVE_TRADING_ENABLED=true`.

## 7. QA harness

`qa_probe.py` (105 HTTP checks), `qa_rag.py`, `qa_data.py`, `qa_perf.py`, `qa_resil.py` (kill switch/breaker/failover) live in the opencode temp dir. All QA-created trades/positions closed; server left RUNNING.
