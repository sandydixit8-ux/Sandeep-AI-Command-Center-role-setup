# EMA + RSI Trend-Following Strategy

The system's default stock-strategy engine. It combines a moving-average trend
filter with an overbought/oversold oscillator to produce entry signals on real
historical OHLC data.

## Signal rules
- Trend filter: price relative to a short EMA (commonly 20) and a long EMA
  (commonly 50). The trend is UP when the short EMA sits above the long EMA and
  price is above both; DOWN when the reverse holds.
- RSI oscillator: a 14-period Relative Strength Index. RSI above 70 is
  overbought, below 30 is oversold.
- BUY candidate: trend is UP and RSI is strengthening but not extreme.
- SELL candidate: trend is DOWN and RSI is weakening.
- No trade: trend and RSI disagree, or RSI is in an extreme zone where chasing
  is risky. A NO TRADE is a valid signal and never an error.

## Exit rules
- Stop loss: default 8% below entry for longs (configurable). The stop distance
  is the sizing input — never place a trade without a stop.
- Target: optional configurable take-profit. When unset, exit on trend/RSI
  reversal or at the stop.

## Caveats
- Every composite signal weighs supporting and opposing factors; a single
  indicator never triggers an order by itself.
- Backtests report anti-overfit grading, not future performance.
- Regime awareness: in Bear / high-volatility regimes, risk-per-trade scales
  down and fewer BUY candidates pass.
