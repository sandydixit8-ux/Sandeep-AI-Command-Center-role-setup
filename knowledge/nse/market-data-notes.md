# NSE Market Data & Trading Conventions

## Trading hours
- Equity cash and derivatives trading: Monday to Friday, 09:15–15:30 IST.
- Pre-open auction for cash equities: 09:00–09:15 IST.
- No trading on NSE holidays; the data feeds this system uses reflect the
  exchange calendar.

## Derivative conventions
- Underlyings supported: NIFTY, BANKNIFTY, FINNIFTY, SENSEX (index options).
- Expiry: index weekly expiries on the day fixed by the exchange. Expiry day is
  the settlement day; positions are marked to the closing spot.
- Option chain fields: open interest (OI), OI change, implied volatility (IV),
  PCR (put/call ratio), max pain (strike with most OI forcing max loss for
  option buyers), expected move, and moneyness.

## Data semantics
- Max pain: the strike where option buyers lose the most at expiry; a popular
  magnetic level, not a guarantee.
- PCR > 1 signals put-heavy positioning (often bearish sentiment), PCR < 1
  signals call-heavy positioning (often bullish sentiment). Extreme readings can
  invert as contrarian signals.
- IV rank / IV percentile compare current IV against its own history; rich IV
  favours premium-selling strategies, cheap IV favours buying.
- Unusual activity flags large OI or volume changes at single strikes relative
  to their own recent history.
