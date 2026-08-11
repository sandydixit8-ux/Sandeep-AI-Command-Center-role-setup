# BSE Market Notes

## Coverage
- BSE is a secondary Indian equity exchange. Cash-equity quotes are available
  through the same market-data provider used for NSE names.
- The options engine in this system operates on NSE index underlyings; BSE
  shares are traded as cash equities only.

## Conventions
- Trading hours match NSE: 09:15–15:30 IST, Monday to Friday.
- Prices are in Indian Rupees (₹/INR). Order sizes are in shares (board lot for
  derivatives; cash equities trade in units).

## Reconciliation
- Broker-reported positions must reconcile with the expected positions derived
  from the order audit trail regardless of exchange. Any drift raises a flag and
  halts autonomous opening of new positions until resolved.
