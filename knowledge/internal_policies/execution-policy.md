# Internal Execution Policy — How Orders Are Gated

Every order path in this system — chat-triggered, tool-call, or the automatic
trading loop — flows through the SAME gated pipeline. No code path bypasses it.

## Gate order
1. safety_gate: checks the kill switch (env var or data/.kill_switch marker).
   Active kill switch blocks ALL execution, including paper orders, and cannot
   be overridden at runtime.
2. daily-loss circuit breaker: refuses new opening orders once the day's
   realised + unrealised loss reaches the limit. Risk-reducing orders remain
   allowed. Resets each trading day.
3. compliance engine: pre-trade checks (algo tagging intent, rate limits,
   daily-loss) produce an explicit decision recorded to the audit trail.
4. approval flow: actions above the approval threshold require explicit human
   approval (approve/reject) before execution.
5. execution audit: every allowed/blocked action is appended to a hash-chained
   JSONL audit file so the trail cannot be silently rewritten.
6. broker: the order reaches the broker adapter (live or sandbox) with an
   idempotency key.

## Automatic trading loop
- Fail-closed: default state STOPPED; only an explicit start() runs it.
- Each cycle is the trading/risk agent run through the identical tool path a
  human chat would use, so no guardrail is skipped.
- The loop persists its state and stops (never resumes) on server restart.
- Kill switch at cycle time halts the loop and records the reason.

## Paper trading
- Paper trades execute at live quoted prices but move no real money.
- Paper orders are subject to the same kill switch, daily-loss gate and audit
  as live orders, so the simulation is representative.
