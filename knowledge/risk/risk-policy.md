# Trading Risk Policy — Hard Limits

These are the mandatory risk guardrails for every automated trading decision in
this system. The risk engine enforces them before any order reaches the
compliance gate.

## Position sizing
- Position size always = capital x risk-per-trade % / stop distance.
- Never recommend a blind fixed quantity. Default risk-per-trade is 2% of
  capital, scaled down in Bear / High-Volatility regimes.
- Maximum loss per single trade is capped by the stop-loss distance; if no stop
  can be set, the trade is refused.

## Exposure limits
- Max exposure per trade and per strategy is capped as a fraction of capital.
- Single-position concentration must not exceed the concentration limit.
- Total open notional exposure must stay under the portfolio limit.
- Sector concentration is monitored; a single sector must not dominate.

## Daily loss limit
- A daily loss circuit breaker stops all trading for the day once cumulative
  realised + unrealised loss reaches the daily loss limit.
- When tripped, no new orders are opened; open risk-reducing orders may still
  be placed. An alert is raised.
- The circuit breaker resets at the start of each new trading day.

## Kill switch
- An independent kill switch (env var or marker file) blocks ALL execution,
  including paper orders, immediately and can never be overridden at runtime.

## Reconciliation
- Expected positions derived from the order audit trail are reconciled against
  broker-reported positions. Any drift raises a flag and halts autonomous
  opening of new positions until resolved.

## No-trade decisions
- Staying flat is always a valid decision. When the no-trade engine returns
  NO TRADE, the correct action is to not open a position.
