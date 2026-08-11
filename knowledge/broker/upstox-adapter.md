# Broker Adapter — Upstox Integration Notes

## Modes
- sandbox: exchange-simulated environment. Order placement and cancellation are
  exercised end-to-end, but funds, holdings, positions, order history and
  detailed order status return 404/403 — the sandbox is order-only. Treat
  sandbox fills as simulation.
- live: real orders. Never used unless LIVE_TRADING_ENABLED=true and the access
  token is fresh. The safety gate enforces this per order.

## Authentication
- OAuth2 access token from the configured API key/secret pair. The token is
  refreshed via the broker's token endpoint; API sessions are expected to
  auto-logout before each new trading day per the SEBI algo framework.

## Order flow
- Every order (place, modify, cancel) passes through the compliance engine and
  the daily-loss circuit breaker before reaching the broker. Risk-reducing
  orders (reduce/close) may bypass the daily-loss gate but never the kill
  switch.
- Order details and IDs are recorded in the execution audit trail with an
  idempotency key so duplicates are prevented on retry.

## Known sandbox limitations
- order_details, order history and funds endpoints are not available on the
  sandbox; the paper-trading engine is the source of truth for portfolio P&L in
  that mode.
