# Firstrade MCP server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that
exposes the `firstrade` library to MCP-compatible agents (Claude Desktop,
Claude Code, etc.) over **stdio**. Credentials stay on your machine.

## Install

```bash
pip install -e ".[mcp]"   # from the repo root
```

This adds the `firstrade-mcp` console command.

## Configuration (environment variables)

| Variable | Required | Purpose |
| --- | --- | --- |
| `FIRSTRADE_USERNAME` | yes | Login username |
| `FIRSTRADE_PASSWORD` | yes | Login password |
| `FIRSTRADE_PIN` | no | Login PIN (completes login without a code) |
| `FIRSTRADE_MFA_SECRET` | no | TOTP secret for automated MFA |
| `FIRSTRADE_EMAIL` / `FIRSTRADE_PHONE` | no | Channel for emailed/SMS MFA codes |
| `FIRSTRADE_PROFILE_PATH` | no | Directory to persist the session token |
| `FIRSTRADE_SAVE_SESSION` | no | `true` to persist the session token |
| `FIRSTRADE_ENABLE_TRADING` | no | `true` to permit live orders/cancels (default `false`) |

## Authentication flow

Call **`login`** first; the returned `status` tells you what to do next:

- **`authenticated`** — done. Happens with `FIRSTRADE_PIN`,
  `FIRSTRADE_MFA_SECRET`, or a valid saved session token.
- **`mfa_authenticator`** — you use an authenticator app. Call
  **`submit_mfa_code`** with the current TOTP code.
- **`mfa_otp`** — a code must be sent to you by SMS/email. The response
  includes `options` (a list of masked recipients). Then:
  1. Call **`request_otp`** with the chosen `index` (default `0`) — this sends
     the SMS/email.
  2. Call **`submit_mfa_code`** with the code you receive.

Nothing about the code needs to be known in advance — it is generated and sent
on the fly, and you enter it through `submit_mfa_code`. This is the right flow
when you do **not** have a TOTP secret up front.

**`auth_status`** reports session/MFA/trading state at any point.

### Example (SMS)

```text
login()
  -> { "status": "mfa_otp",
       "options": [ { "index": 0, "channel": "sms",   "recipient": "***-***-1234" },
                    { "index": 1, "channel": "email", "recipient": "j****@e****.com" } ] }
request_otp(0)          -> { "code_sent": true, "channel": "sms", "recipient": "***-***-1234" }
submit_mfa_code("123456") -> { "authenticated": true, "accounts": ["12345678"] }
```

## Trading safety (double gate)

Order tools default to **dry-run (preview)**. A live order is only sent when
**both**:

- `FIRSTRADE_ENABLE_TRADING=true` is set in the environment, **and**
- the tool is called with `dry_run=false` **and** `confirm=true`.

Otherwise the request is automatically downgraded to a dry run and the
response explains why via its `note` field.

## Tools

- **Auth:** `login`, `request_otp`, `submit_mfa_code`, `auth_status`
- **Account:** `list_accounts`, `get_balances`, `get_balance_overview`,
  `get_positions`, `get_account_history`, `get_orders`
- **Market data:** `get_quote`, `get_ohlc`
- **Options (pricing):** `get_option_expirations`, `get_option_chain`
  (with optional `option_type` / strike-window filtering), `get_option_greeks`
- **Watchlists:** `list_watchlists`, `get_watchlist`, `create_watchlist`,
  `add_watchlist_symbol`, `remove_watchlist_symbol`, `delete_watchlist`
- **Trading (guarded):** `place_equity_order`, `place_option_order`,
  `cancel_order`

## Client configuration

### Claude Code

```bash
claude mcp add firstrade -- firstrade-mcp
```

### Claude Desktop (`claude_desktop_config.json`)

```jsonc
{
  "mcpServers": {
    "firstrade": {
      "command": "firstrade-mcp",
      "env": {
        "FIRSTRADE_USERNAME": "your-username",
        "FIRSTRADE_PASSWORD": "your-password",
        "FIRSTRADE_MFA_SECRET": "optional-totp-secret",
        "FIRSTRADE_ENABLE_TRADING": "false"
      }
    }
  }
}
```

## Notes

- `debug` logging in the underlying library is force-disabled here so tokens
  and raw responses are never written to logs.
- Option pricing always returns a full chain per expiration; there is no
  single-contract endpoint, so `get_option_chain` filters client-side.
