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

## Authentication flow (two-step)

1. Call the **`login`** tool. It uses the env credentials.
2. If it returns `mfa_required: true`, call **`submit_mfa_code`** with the code
   sent to your email/phone. (With `FIRSTRADE_PIN` or `FIRSTRADE_MFA_SECRET`,
   login usually completes in one step.)
3. **`auth_status`** reports session/trading state.

## Trading safety (double gate)

Order tools default to **dry-run (preview)**. A live order is only sent when
**both**:

- `FIRSTRADE_ENABLE_TRADING=true` is set in the environment, **and**
- the tool is called with `dry_run=false` **and** `confirm=true`.

Otherwise the request is automatically downgraded to a dry run and the
response explains why via its `note` field.

## Tools

- **Auth:** `login`, `submit_mfa_code`, `auth_status`
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
