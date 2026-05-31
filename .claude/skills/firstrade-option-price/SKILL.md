---
name: firstrade-option-price
description: >-
  Fetch option prices from Firstrade for a ticker at a target days-to-
  expiration. Use when the user asks for option prices, an option chain, or
  option quotes (e.g. "get option prices for TLT", "show me 45-day options on
  SPY"). Defaults to ticker TLT and 30 days to expiration unless the user
  states otherwise. If no expiration exists exactly at the target horizon, it
  walks forward one day at a time until it finds the next available
  expiration. Depends on the firstrade-login skill for authentication.
---

# Firstrade option price

Fetch the option chain (pricing) for a ticker at a target number of days to
expiration, using the `firstrade` MCP server.

## Defaults

- **Ticker:** `TLT` unless the user names another symbol.
- **Days to expiration (DTE):** `30` unless the user states a different
  horizon (e.g. "45 day", "two weeks out" → 14).
- **Option type / strikes:** all, unless the user asks for calls only, puts
  only, or a strike range.

## Dependency: authentication

This skill requires an authenticated Firstrade session.

1. Call `auth_status`.
2. If `authenticated` is `false`, **invoke the `firstrade-login` skill** and
   complete login before continuing. Do not attempt to fetch prices while
   unauthenticated — the option tools will fail.

## Steps

1. **Resolve inputs.** Determine `ticker` (default `TLT`) and `target_dte`
   (default `30`) from the user's request.

2. **List expirations.** Call `get_option_expirations(symbol=ticker)`. The
   response has an `items` list; each item has an `exp_date` (the expiration
   date) and `day_left` (days from today until that expiration).

3. **Select the expiration** — "target DTE, else add a day until data is
   found." Expirations are discrete dates, so walk forward from the target:
   - Start at `d = target_dte`.
   - If an expiration exists with `day_left == d`, select it.
   - Otherwise increment `d` by 1 and check again, repeating until a match is
     found. In practice this means: **select the earliest expiration whose
     `day_left` is greater than or equal to `target_dte`** (the next available
     expiration on or after the target horizon).
   - If no expiration has `day_left >= target_dte` (the target is beyond the
     furthest listed expiration), select the **latest** available expiration
     and tell the user it is the furthest one offered.
   - If `items` is empty, report that the ticker has no listed options and
     stop.

4. **Fetch prices.** Call `get_option_chain(symbol=ticker,
   exp_date=<selected exp_date>)`. Pass `option_type`, `min_strike`, and/or
   `max_strike` only if the user asked to narrow the result.

5. **Report.** State clearly:
   - The ticker, the **selected expiration date**, and its actual days-to-
     expiration (note if it differs from the requested target and why).
   - The pricing for the contracts: at minimum bid / ask / last per strike.
     For a broad chain, summarize around the money rather than dumping every
     strike, unless the user wants the full chain.

## Notes

- "Add a day until it finds data" is satisfied by picking the next expiration
  at or after the target; you do not need to call the API once per day.
- `get_option_chain` returns the full chain for one expiration (the API has no
  single-contract endpoint); use the `option_type` / strike-window filters to
  keep large chains manageable.
- For greeks (delta/gamma/theta/vega/IV) on the selected expiration, call
  `get_option_greeks(symbol=ticker, exp_date=<selected exp_date>)`.
