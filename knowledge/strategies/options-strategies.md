# Options Strategies — Evaluation & Selection Guide

The options lab evaluates seven strategies per underlying/expiry from the live
NSE option chain (OI, PCR, IV, max pain, expected move) and returns payoff,
breakevens, max profit/loss and estimated margin.

## Strategy catalogue
- long_call — bullish outright purchase. Max loss = premium paid; used when the
  expected move is strongly up.
- long_put — bearish outright purchase. Max loss = premium paid; used when the
  expected move is strongly down.
- bull_call_spread — buy ATM call, sell OTM call. Reduces cost and caps both
  profit and loss; used for moderately bullish views.
- bear_put_spread — buy ATM put, sell OTM put. Capped-risk bearish trade.
- strangle — buy OTM call and OTM put. Profits from a large move either way;
  used when IV is low and a big breakout is expected.
- iron_condor — sell OTM call spread + sell OTM put spread. Profits from range
  contraction (theta); used when IV is rich and the market is expected to stay
  inside a range.
- covered_call — hold the underlying and sell an OTM call. Collects premium
  against a stock position; caps upside.

## Selection logic
- Directional view from the chain (PCR, OI build-up) maps to call/put-biased
  candidates: long_call/bull_call_spread/covered_call for bullish, long_put/
  bear_put_spread for bearish.
- Volatile, direction-agnostic regimes select strangle or iron_condor.
- The no-trade gate returns an explicit NO TRADE when the edge is too weak, IV
  is extreme, or liquidity/data quality is poor.

## Risk notes
- Option paper positions carry mark-to-market P&L and exit prices; all options
  activity is simulated paper trading in this system.
- Expiry risk: index options settle to the closing spot on expiry day; theta
  accelerates as DTE falls. See nse/market-data-notes for expiry conventions.
