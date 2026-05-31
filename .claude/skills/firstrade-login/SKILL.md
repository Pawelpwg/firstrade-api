---
name: firstrade-login
description: >-
  Authenticate to Firstrade through the firstrade.mcp MCP server before any
  account or market-data action. Use when the user asks to "log in to
  Firstrade", when a Firstrade tool reports it is not authenticated, or as a
  prerequisite for other Firstrade skills (e.g. fetching option prices).
  Handles the interactive SMS/email OTP flow where the code arrives after the
  login attempt, as well as PIN, authenticator-app (TOTP), and saved-session
  logins.
---

# Firstrade login

Establish an authenticated Firstrade session via the `firstrade.mcp` MCP server.
Other Firstrade skills depend on this one.

## Prerequisites

The `firstrade` MCP server must be attached to the agent and its credentials
configured via environment variables (at minimum `FIRSTRADE_USERNAME` and
`FIRSTRADE_PASSWORD`). See `firstrade/mcp/README.md` in this repo for setup.

If the MCP tools below are not available, tell the user the `firstrade.mcp` MCP
server is not connected and point them at `firstrade/mcp/README.md` — do not
attempt to log in by any other means.

## Steps

1. **Check first.** Call `auth_status`. If `authenticated` is `true`, stop —
   the session is ready, report success and do nothing else.

2. **Start login.** Call `login`. Branch on the returned `status`:
   - `authenticated` — done. Report success.
   - `mfa_authenticator` — the user has an authenticator app. Ask them for
     the current 6-digit code, then go to step 4.
   - `mfa_otp` — a code must be sent by SMS/email. Continue to step 3.

3. **Request and collect the OTP** (only for `status: "mfa_otp"`):
   - The `login` response contains `options`: a list of masked recipients,
     each with an `index`, `channel` (`sms`/`email`), and `recipient`.
   - If there is more than one option, show the masked recipients and ask the
     user which to use. If there is exactly one, use `index: 0`.
   - Call `request_otp` with the chosen `index`. This sends the code.
   - Ask the user to paste the code they received.

4. **Submit the code.** Call `submit_mfa_code` with the code. On success it
   returns `authenticated: true` and the account numbers.

5. **Confirm.** Report that login succeeded and list the available account
   numbers. Never echo the password, OTP code, or any token back to the user.

## Notes

- Do not ask the user for an MFA secret. The OTP flow is designed to work with
  a code that is generated and sent on the fly.
- If `submit_mfa_code` fails, the code was likely wrong or expired: for the
  OTP path call `request_otp` again to resend; for the authenticator path ask
  for a fresh code.
