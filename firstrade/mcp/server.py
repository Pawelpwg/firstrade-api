"""FastMCP server exposing the Firstrade API as agent tools.

Run as a local stdio MCP server::

    firstrade-mcp

Credentials and the live-trading switch are read from environment variables
(see :mod:`firstrade.mcp.config`). Login is two-step: call ``login`` and, if a
code is required, ``submit_mfa_code``.
"""

import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from firstrade import order, symbols
from firstrade.mcp.config import Config
from firstrade.mcp.state import SessionState

# stdio transport uses stdout for the protocol; all logs must go to stderr.
logging.basicConfig(stream=sys.stderr, level=logging.INFO)
logger = logging.getLogger("firstrade.mcp")

mcp = FastMCP("firstrade")
config = Config.from_env()
state = SessionState(config)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _quote_to_dict(quote: symbols.SymbolQuote) -> dict[str, Any]:
    """Serialize a SymbolQuote, dropping the non-serializable session."""
    return {k: v for k, v in vars(quote).items() if k != "ft_session"}


def _enum_lookup(enum_cls: Any, value: str) -> Any:
    """Resolve an order enum from a human-friendly NAME or raw value."""
    try:
        return enum_cls[value.strip().upper()]
    except KeyError:
        # Fall back to matching the underlying value (e.g. "2", "B").
        for member in enum_cls:
            if member.value == value:
                return member
        valid = ", ".join(m.name for m in enum_cls)
        raise ValueError(
            f"Invalid {enum_cls.__name__} '{value}'. Valid options: {valid}",
        ) from None


def _trading_gate(*, dry_run: bool, confirm: bool) -> tuple[bool, str | None]:
    """Apply the double-gate guard for live orders.

    Returns the effective ``dry_run`` flag and an optional note explaining
    why a requested live order was downgraded to a dry run.
    """
    if dry_run:
        return True, None
    if not config.enable_trading:
        return True, (
            "Live trading is disabled (set FIRSTRADE_ENABLE_TRADING=true to "
            "allow it); request was downgraded to a dry run."
        )
    if not confirm:
        return True, (
            "Live order requires confirm=True; request was downgraded to a "
            "dry run."
        )
    return False, None


def _as_float(item: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    """Best-effort extraction of a numeric field from an option item."""
    for key in keys:
        if key in item:
            try:
                return float(item[key])
            except (TypeError, ValueError):
                return None
    return None


def _detect_option_type(item: dict[str, Any]) -> str | None:
    """Best-effort detection of call/put for an option chain item."""
    for key in ("opt_type", "type", "cp", "pc", "call_put", "callput", "put_call"):
        if key in item and isinstance(item[key], str):
            val = item[key].strip().upper()
            if val.startswith("C"):
                return "C"
            if val.startswith("P"):
                return "P"
    return None


def _filter_option_chain(
    payload: dict[str, Any],
    option_type: str | None,
    min_strike: float | None,
    max_strike: float | None,
) -> dict[str, Any]:
    """Apply best-effort client-side filtering to an option chain.

    Items whose strike/type cannot be determined are kept rather than
    silently dropped, since the API field names are not guaranteed.
    """
    items = payload.get("items")
    if not isinstance(items, list) or not (option_type or min_strike or max_strike):
        return payload

    want_type = option_type.strip().upper()[:1] if option_type else None
    kept: list[Any] = []
    for item in items:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        detected = _detect_option_type(item)
        if want_type and detected is not None and detected != want_type:
            continue
        strike = _as_float(item, ("strike", "strike_price", "strikeprice"))
        if strike is not None:
            if min_strike is not None and strike < min_strike:
                continue
            if max_strike is not None and strike > max_strike:
                continue
        kept.append(item)

    return {
        **payload,
        "items": kept,
        "_filter": {
            "option_type": option_type,
            "min_strike": min_strike,
            "max_strike": max_strike,
            "returned": len(kept),
            "original": len(items),
        },
    }


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@mcp.tool()
def login() -> dict[str, Any]:
    """Log in to Firstrade using credentials from the environment.

    Reads FIRSTRADE_USERNAME / FIRSTRADE_PASSWORD (and optional PIN, EMAIL,
    PHONE, MFA_SECRET). If an emailed/SMS code is required, returns
    ``mfa_required: true`` — then call ``submit_mfa_code`` with the code.
    """
    if not config.has_credentials:
        return {
            "error": "Missing credentials. Set FIRSTRADE_USERNAME and "
            "FIRSTRADE_PASSWORD in the server environment.",
        }
    need_code = state.start_login()
    if need_code:
        return {
            "mfa_required": True,
            "message": "A verification code was sent. Call submit_mfa_code "
            "with the code from your email/phone.",
        }
    return {
        "mfa_required": False,
        "authenticated": True,
        "accounts": state.account_data.account_numbers,
    }


@mcp.tool()
def submit_mfa_code(code: str) -> dict[str, Any]:
    """Finish a two-step login by submitting the MFA verification code."""
    state.finish_mfa(code)
    return {
        "authenticated": True,
        "accounts": state.account_data.account_numbers,
    }


@mcp.tool()
def auth_status() -> dict[str, Any]:
    """Report whether the server holds an authenticated Firstrade session."""
    return {
        "authenticated": state.is_authenticated,
        "awaiting_mfa": state.awaiting_mfa,
        "trading_enabled": config.enable_trading,
        "accounts": state.account_data.account_numbers
        if state.is_authenticated
        else [],
    }


# ---------------------------------------------------------------------------
# Account data
# ---------------------------------------------------------------------------

@mcp.tool()
def list_accounts() -> dict[str, Any]:
    """List account numbers and their current total values."""
    data = state.account_data
    return {
        "account_numbers": data.account_numbers,
        "balances": data.account_balances,
    }


@mcp.tool()
def get_balances(account: str | None = None) -> dict[str, Any]:
    """Get full balance details for an account (defaults to the first)."""
    acct = state.resolve_account(account)
    return state.account_data.get_account_balances(acct)


@mcp.tool()
def get_balance_overview(
    account: str | None = None,
    keywords: list[str] | None = None,
) -> dict[str, Any]:
    """Get a flattened view of key balance fields (cash, buying power, etc.)."""
    acct = state.resolve_account(account)
    return state.account_data.get_balance_overview(acct, keywords)


@mcp.tool()
def get_positions(account: str | None = None) -> dict[str, Any]:
    """Get currently held positions for an account (defaults to the first)."""
    acct = state.resolve_account(account)
    return state.account_data.get_positions(acct)


@mcp.tool()
def get_account_history(
    account: str | None = None,
    date_range: str = "ytd",
    custom_range: list[str] | None = None,
) -> dict[str, Any]:
    """Get account transaction history.

    ``date_range`` is one of today, 1w, 1m, 2m, mtd, ytd, ly, cust. When
    ``cust``, pass ``custom_range`` as ["YYYY-MM-DD", "YYYY-MM-DD"].
    """
    acct = state.resolve_account(account)
    return state.account_data.get_account_history(acct, date_range, custom_range)


@mcp.tool()
def get_orders(account: str | None = None, per_page: int = 0) -> Any:
    """Get existing/placed orders for an account (per_page=0 returns all)."""
    acct = state.resolve_account(account)
    return state.account_data.get_orders(acct, per_page)


# ---------------------------------------------------------------------------
# Market data
# ---------------------------------------------------------------------------

@mcp.tool()
def get_quote(symbol: str, account: str | None = None) -> dict[str, Any]:
    """Get a real-time equity/ETF quote for a symbol."""
    acct = state.resolve_account(account)
    quote = symbols.SymbolQuote(state.require_session(), acct, symbol)
    return _quote_to_dict(quote)


@mcp.tool()
def get_ohlc(symbol: str, range_: str = "1d") -> dict[str, Any]:
    """Get OHLC candle data for a symbol (range: 24h, 1d, 1w, 1m, 1y)."""
    ohlc = symbols.SymbolOHLC(state.require_session(), symbol, range_=range_)
    return {
        "symbol": ohlc.symbol,
        "range": ohlc.range,
        "start_of_day": ohlc.start_of_day,
        "candles": [
            {
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": low,
                "close": c,
                "volume": vol,
            }
            for ts, o, h, low, c, vol in ohlc.candles
        ],
    }


# ---------------------------------------------------------------------------
# Option pricing
# ---------------------------------------------------------------------------

@mcp.tool()
def get_option_expirations(symbol: str) -> dict[str, Any]:
    """Get available option expiration dates for an underlying symbol."""
    return symbols.OptionQuote(state.require_session(), symbol).option_dates


@mcp.tool()
def get_option_chain(
    symbol: str,
    exp_date: str,
    option_type: str | None = None,
    min_strike: float | None = None,
    max_strike: float | None = None,
) -> dict[str, Any]:
    """Get the option chain (pricing) for one expiration date.

    Returns bid/ask/last and contract symbols for every strike in the
    expiration. Optional filters narrow the result client-side:
    ``option_type`` ("C"/"P"/"call"/"put") and a ``min_strike``/``max_strike``
    window. Items whose fields cannot be parsed are kept, not dropped.
    """
    quote = symbols.OptionQuote(state.require_session(), symbol)
    payload = quote.get_option_quote(symbol, exp_date)
    return _filter_option_chain(payload, option_type, min_strike, max_strike)


@mcp.tool()
def get_option_greeks(symbol: str, exp_date: str) -> dict[str, Any]:
    """Get option greeks/analytics (delta, gamma, theta, vega, IV)."""
    quote = symbols.OptionQuote(state.require_session(), symbol)
    return quote.get_greek_options(symbol, exp_date)


# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------

def _watchlist() -> Any:
    from firstrade.watchlist import Watchlist

    return Watchlist(state.require_session())


@mcp.tool()
def list_watchlists() -> dict[str, Any]:
    """List all watchlists for the current user."""
    return _watchlist().get_watchlists()


@mcp.tool()
def get_watchlist(list_id: int) -> dict[str, Any]:
    """Get the contents (symbols and quotes) of a specific watchlist."""
    return _watchlist().get_watchlist(list_id)


@mcp.tool()
def create_watchlist(name: str) -> dict[str, Any]:
    """Create a new watchlist with the given display name."""
    return _watchlist().create_watchlist(name)


@mcp.tool()
def add_watchlist_symbol(
    list_id: int,
    symbol: str,
    sec_type: str = "1",
) -> dict[str, Any]:
    """Add a symbol to a watchlist (sec_type "1" for equities/ETFs)."""
    return _watchlist().add_symbol(list_id, symbol, sec_type)


@mcp.tool()
def remove_watchlist_symbol(watchlist_id: int) -> dict[str, Any]:
    """Remove an item from a watchlist by its per-item watchlist_id."""
    return _watchlist().remove_symbol(watchlist_id)


@mcp.tool()
def delete_watchlist(list_id: int) -> dict[str, Any]:
    """Delete an entire watchlist by its list_id."""
    return _watchlist().delete_watchlist(list_id)


# ---------------------------------------------------------------------------
# Trading (guarded)
# ---------------------------------------------------------------------------

@mcp.tool()
def place_equity_order(
    symbol: str,
    price_type: str,
    order_type: str,
    duration: str,
    account: str | None = None,
    quantity: int = 0,
    price: float = 0.0,
    stop_price: float | None = None,
    notional: bool = False,
    order_instruction: str = "NONE",
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Place (or preview) an equity order.

    Guarded: a live order requires BOTH FIRSTRADE_ENABLE_TRADING=true AND
    confirm=True with dry_run=False; otherwise it is downgraded to a preview.

    price_type: LIMIT, MARKET, STOP, STOP_LIMIT, TRAILING_STOP_DOLLAR,
        TRAILING_STOP_PERCENT.
    order_type: BUY, SELL, SELL_SHORT, BUY_TO_COVER.
    duration: DAY, DAY_EXT, OVERNIGHT, GT90.
    order_instruction: NONE, AON, OPG, CLO.
    """
    acct = state.resolve_account(account)
    effective_dry_run, note = _trading_gate(dry_run=dry_run, confirm=confirm)
    ft_order = order.Order(state.require_session())
    result = ft_order.place_order(
        account=acct,
        symbol=symbol,
        price_type=_enum_lookup(order.PriceType, price_type),
        order_type=_enum_lookup(order.OrderType, order_type),
        duration=_enum_lookup(order.Duration, duration),
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        dry_run=effective_dry_run,
        notional=notional,
        order_instruction=_enum_lookup(order.OrderInstructions, order_instruction),
    )
    return {"dry_run": effective_dry_run, "note": note, "result": result}


@mcp.tool()
def place_option_order(
    option_symbol: str,
    price_type: str,
    order_type: str,
    contracts: int,
    duration: str,
    account: str | None = None,
    price: float = 0.0,
    stop_price: float | None = None,
    order_instruction: str = "NONE",
    dry_run: bool = True,
    confirm: bool = False,
) -> dict[str, Any]:
    """Place (or preview) an option order.

    Guarded identically to ``place_equity_order``. ``option_symbol`` is the
    contract symbol from ``get_option_chain`` (the ``opt_symbol`` field).
    order_type for options: BUY_OPTION, SELL_OPTION.
    """
    acct = state.resolve_account(account)
    effective_dry_run, note = _trading_gate(dry_run=dry_run, confirm=confirm)
    ft_order = order.Order(state.require_session())
    result = ft_order.place_option_order(
        account=acct,
        option_symbol=option_symbol,
        price_type=_enum_lookup(order.PriceType, price_type),
        order_type=_enum_lookup(order.OrderType, order_type),
        contracts=contracts,
        duration=_enum_lookup(order.Duration, duration),
        stop_price=stop_price,
        price=price,
        dry_run=effective_dry_run,
        order_instruction=_enum_lookup(order.OrderInstructions, order_instruction),
    )
    return {"dry_run": effective_dry_run, "note": note, "result": result}


@mcp.tool()
def cancel_order(order_id: str, confirm: bool = False) -> dict[str, Any]:
    """Cancel a placed order by its order_id.

    Guarded: requires FIRSTRADE_ENABLE_TRADING=true AND confirm=True.
    """
    if not config.enable_trading:
        return {
            "cancelled": False,
            "note": "Live trading is disabled (set FIRSTRADE_ENABLE_TRADING="
            "true to allow cancellations).",
        }
    if not confirm:
        return {
            "cancelled": False,
            "note": "Cancellation requires confirm=True.",
        }
    result = state.account_data.cancel_order(order_id)
    return {"cancelled": True, "result": result}


def main() -> None:
    """Run the MCP server over stdio."""
    logger.info("Starting Firstrade MCP server (trading_enabled=%s)", config.enable_trading)
    mcp.run()


if __name__ == "__main__":
    main()
