# CLAUDE.md

Guidance for Claude / agents working in this repository.

## What this repo is

`firstrade` is an unofficial Python client for the Firstrade brokerage. The
core library lives in `firstrade/` and talks only to `api3x.firstrade.com`.

This repo also ships an **optional MCP server** and **agent skills** layered on
top of the library:

- `firstrade/mcp/` — a local stdio [MCP](https://modelcontextprotocol.io)
  server (`firstrade.mcp`) exposing the library as agent tools. Installed via
  the `mcp` extra. See `firstrade/mcp/README.md`.
- `.claude/skills/` — repo skills that orchestrate the MCP tools:
  - `firstrade-login` — authenticate (handles PIN, authenticator TOTP, and the
    interactive SMS/email OTP flow where the code arrives after login).
  - `firstrade-option-price` — fetch option prices for a ticker at a target
    days-to-expiration (defaults: ticker `TLT`, 30 DTE). Depends on
    `firstrade-login`.

## Requirement: the skills need the MCP server

**The skills only work when the `firstrade` MCP server is attached to the
agent.** They call its tools (`auth_status`, `login`, `request_otp`,
`submit_mfa_code`, `get_option_expirations`, `get_option_chain`, etc.). They do
not call the Python library directly.

To enable them:

1. Install the server: `pip install -e ".[mcp]"` (adds the `firstrade-mcp`
   command).
2. Configure credentials via environment variables — at minimum
   `FIRSTRADE_USERNAME` and `FIRSTRADE_PASSWORD`. Full list and the
   live-trading switch (`FIRSTRADE_ENABLE_TRADING`) are in
   `firstrade/mcp/README.md`.
3. Attach the server to your agent, e.g. for Claude Code:
   `claude mcp add firstrade -- firstrade-mcp`.

If the MCP tools are unavailable, the skills should report that the `firstrade`
MCP server is not connected and point to `firstrade/mcp/README.md` rather than
attempting any other login path.

## Safety

- Trading tools default to **dry-run**. A live order requires **both**
  `FIRSTRADE_ENABLE_TRADING=true` and `confirm=True` on the call.
- Never echo passwords, OTP codes, or session tokens back to the user. The
  server force-disables the library's `debug` logging so tokens are never
  written to logs.

## Development

- Tests: `pytest tests/` (offline; mocks the session — no network or
  credentials needed). Lint: `ruff check`.
- Note the `.gitignore` rule `*_*.py` matches underscore-named Python files
  (including test files), so new tests must be added with `git add -f`.
- Keep changes to the core `firstrade/` library minimal; prefer adding
  orchestration in the `firstrade/mcp/` layer.
