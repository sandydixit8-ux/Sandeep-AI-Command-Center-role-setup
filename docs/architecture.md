# Architecture & Workflow

Fail-closed automated trading agent: every execution path (manual chat or the
auto-trading loop) passes through the same gate chain. There is no second,
bypassing path.

## End-to-end order lifecycle

```
User / Auto-trading cycle
   │
   ▼
┌─────────────────────────┐
│ AGENT (risk agent)      │  selects tools from registry.py:
│  "BUY RELIANCE"         │  market_brief, market_signal, compliance_status
└──────────┬──────────────┘
           │ calls order tool
           ▼
┌─────────────────────────┐        ┌──────────────────────────────┐
│ TOOLS layer             │        │ safety.py safety_gate()      │
│ paper_buy / live_order  │───────▶│  1. kill_switch_active()?    │──✗→ ExecutionBlockedError
└──────────┬──────────────┘        │  2. LIVE_TRADING_ENABLED?    │
           │                       └──────────────┬───────────────┘
           │                 (fail-closed, order   │ allowed
           │                  never bypasses)      ▼
           │                       ┌──────────────────────────────┐
           │                       │ ComplianceEngine.pre_trade() │──✗→ blocked
           │                       │  mode on? / algo-id set?     │
           │                       │  rate limit?                 │
           │                       └──────────────┬───────────────┘
           │                                      ▼ allowed
           │                       ┌──────────────────────────────┐
           │                       │ Daily-loss circuit breaker   │──✗→ blocked (open new pos)
           │                       │  start-of-day equity cap     │     (reducing closes allowed)
           │                       └──────────────┬───────────────┘
           │                                      ▼ allowed
           ▼                                       │
┌───────────────────────┐                         │
│ ORDER_APPROVAL_MODE?  │                         │
│  ┌──── on ────┐       │                         │
│  │ ApprovalFlow│      │                         │
│  │ submit() →  │      │                         │
│  │ PENDING     │      │  ┌───────────┐          │
│  │  wait       │◀─────┼──│ web UI /  │          │
│  │  human:     │      │  │ API POST  │          │
│  │  approve()  │──────┼──│ approvals │          │
│  └─────────────┘      │  └───────────┘          │
│  └──── off ────┘       │                         │
└──────────┬────────────┘                         │
           │  execute via SAME gated path          │
           ▼                                       │
┌─────────────────────────┐                        │
│ Upstox OrderManager     │ ◀──────────────────────┘
│ paper: portfolio apply  │   (never a 2nd path)
│ live: broker POST /v3/  │
└──────────┬──────────────┘
           ▼
   Audit trails (hash-chained JSONL):
   execution_audit.jsonl · compliance_audit.jsonl
   order_approvals_audit.jsonl · auto_trading_audit.jsonl
```

## How a cycle starts

| Path | Trigger | Loop |
|---|---|---|
| **Manual** | Chat message in web UI | One tool-call round, returns to user |
| **Auto** | ▶ Start button → `/api/v1/trading/start` | Daemon thread runs risk agent every `AUTO_TRADE_INTERVAL`s; each cycle restarts at the top of the lifecycle |

## Supporting layers

- **Live market feed** (`agents_core/market.py`) — `UpstoxMarketProvider` for
  quotes / OHLC / 5-level orderbook; falls back Moneycontrol → mock. Provider
  chosen by `MARKET_DATA_SOURCE`.
- **RAG knowledge** (`agents_core/rag/`) — hybrid BM25 + feature-hash embedding
  retrieval, section filters, drift detection, and term co-occurrence graph over
  the strategy/knowledge docs.
- **Compliance** (`agents_core/compliance.py`) — mode, SEBI algo-id tagging,
  rate limit, daily-loss checks; records to `compliance_audit.jsonl`.
- **Mode A approvals** (`agents_core/approval.py`) — human-in-the-loop drafts
  (PENDING → approve/reject); approved drafts execute through the same gate.
- **Auto-trading controller** (`agents_core/trading.py`) — fail-closed Start/Stop
  supervisor; state persists so a restart never silently resumes running.

## Fail-closed invariants

1. **No second execution path** — paper and live orders both pass through
   `safety_gate` → compliance → breaker.
2. **Default = STOPPED** — the auto-trading loop only runs after an explicit start.
3. **Kill switch wins** — env `AGENT_KILL_SWITCH` or `data/.kill_switch` marker
   blocks starts and halts a running loop mid-cycle.
4. **Audit never breaks** — audit writes are wrapped so a disk failure cannot
   block a safety decision.
