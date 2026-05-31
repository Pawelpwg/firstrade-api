"""Offline tests for the Firstrade MCP server.

These tests exercise pure logic (guards, enum resolution, option-chain
filtering, serialization) and tool wiring with the session layer mocked, so
no network or real credentials are required.
"""

import types

import pytest

from firstrade import order
from firstrade.mcp import server
from firstrade.mcp.config import Config, _env_bool
from firstrade.mcp.state import NotAuthenticatedError, SessionState


def call(tool, *args, **kwargs):
    """Invoke a registered MCP tool via its underlying function.

    ``@mcp.tool()`` wraps each function in a FunctionTool object; ``.fn`` is
    the original callable.
    """
    fn = getattr(tool, "fn", tool)
    return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_env_bool(monkeypatch):
    monkeypatch.setenv("X", "TrUe")
    assert _env_bool("X") is True
    monkeypatch.setenv("X", "no")
    assert _env_bool("X") is False
    monkeypatch.delenv("X", raising=False)
    assert _env_bool("X", default=True) is True


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("FIRSTRADE_USERNAME", "u")
    monkeypatch.setenv("FIRSTRADE_PASSWORD", "p")
    monkeypatch.setenv("FIRSTRADE_ENABLE_TRADING", "1")
    cfg = Config.from_env()
    assert cfg.has_credentials is True
    assert cfg.enable_trading is True
    assert cfg.profile_path is None


def test_config_no_credentials():
    cfg = Config(
        username="", password="", pin="", email="", phone="",
        mfa_secret="", profile_path=None, save_session=False,
        enable_trading=False,
    )
    assert cfg.has_credentials is False


# ---------------------------------------------------------------------------
# Trading guard (double gate)
# ---------------------------------------------------------------------------

def test_trading_gate_dry_run_passthrough():
    server.config.enable_trading = True
    assert server._trading_gate(dry_run=True, confirm=True) == (True, None)


def test_trading_gate_blocks_when_disabled():
    server.config.enable_trading = False
    effective, note = server._trading_gate(dry_run=False, confirm=True)
    assert effective is True
    assert "disabled" in note


def test_trading_gate_requires_confirm():
    server.config.enable_trading = True
    effective, note = server._trading_gate(dry_run=False, confirm=False)
    assert effective is True
    assert "confirm" in note


def test_trading_gate_allows_live():
    server.config.enable_trading = True
    assert server._trading_gate(dry_run=False, confirm=True) == (False, None)


# ---------------------------------------------------------------------------
# Enum resolution
# ---------------------------------------------------------------------------

def test_enum_lookup_by_name():
    assert server._enum_lookup(order.OrderType, "buy") is order.OrderType.BUY
    assert server._enum_lookup(order.PriceType, "LIMIT") is order.PriceType.LIMIT


def test_enum_lookup_by_value():
    assert server._enum_lookup(order.OrderType, "B") is order.OrderType.BUY


def test_enum_lookup_invalid():
    with pytest.raises(ValueError, match="Invalid"):
        server._enum_lookup(order.OrderType, "nonsense")


# ---------------------------------------------------------------------------
# Option-chain filtering
# ---------------------------------------------------------------------------

CHAIN = {
    "items": [
        {"opt_type": "C", "strike": "100"},
        {"opt_type": "P", "strike": "100"},
        {"opt_type": "C", "strike": "250"},
        {"no": "parseable fields"},
    ],
}


def test_filter_calls_in_window():
    out = server._filter_option_chain(CHAIN, "call", 90, 150)
    # C@100 matches; unparseable item kept; P and C@250 dropped.
    assert out["_filter"]["returned"] == 2
    assert out["_filter"]["original"] == 4


def test_filter_no_args_returns_payload_unchanged():
    out = server._filter_option_chain(CHAIN, None, None, None)
    assert out is CHAIN
    assert "_filter" not in out


def test_filter_puts_only():
    out = server._filter_option_chain(CHAIN, "P", None, None)
    types_kept = [i.get("opt_type") for i in out["items"] if "opt_type" in i]
    assert "C" not in types_kept


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_quote_to_dict_drops_session():
    fake = types.SimpleNamespace(ft_session="SECRET", symbol="INTC", last="3.37")
    out = server._quote_to_dict(fake)
    assert "ft_session" not in out
    assert out == {"symbol": "INTC", "last": "3.37"}


# ---------------------------------------------------------------------------
# State machine (mocked FTSession / FTAccountData)
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _InnerSession:
    """Stand-in for requests.Session (headers + cookies only)."""

    def __init__(self):
        self.headers = {}


class _FakeSession:
    """Fake FTSession that scripts the login HTTP sequence.

    ``responses`` maps URL substrings to payload dicts returned by
    ``_request``; the credential POST drives which MFA branch is taken.
    """

    def __init__(self, login_payload, *, pin="", mfa_secret=""):
        self._login_payload = login_payload
        self.pin = pin
        self.mfa_secret = mfa_secret
        self.session = _InnerSession()
        self.login_json = {}
        self.t_token = None
        self.login_two_called_with = None
        self.requests = []
        self.loaded_cookie = ""

    # Library-style helpers used by SessionState._manual_login.
    def _load_cookies(self):
        return self.loaded_cookie

    def _request(self, method, url, **kwargs):
        self.requests.append((method, url))
        if url.endswith("/login"):
            return _FakeResponse(self._login_payload)
        if "request_code" in url:
            return _FakeResponse(
                {"error": "", "verificationSid": "VSID", "t_token": "tok"},
            )
        return _FakeResponse({"error": ""})

    # Used only on the pin/secret fast path.
    def login(self):
        self.login_json = {"sid": "S", "ftat": "F", "error": ""}
        return False

    def login_two(self, code):
        self.login_two_called_with = code


class _FakeAccountData:
    def __init__(self, session):
        self.account_numbers = ["12345678"]
        self.account_balances = {"12345678": "1000.00"}


# Login server payloads for each MFA branch.
_OTP_PAYLOAD = {
    "error": "",
    "t_token": "tok",
    "otp": [
        {"channel": "sms", "recipientMask": "***-***-1234", "recipientId": "r1"},
        {"channel": "email", "recipientMask": "j****@e****.com", "recipientId": "r2"},
    ],
}
_AUTHENTICATOR_PAYLOAD = {"error": "", "mfa": True, "t_token": "tok"}
_SAVED_SESSION_PAYLOAD = {"error": "", "ftat": "F", "sid": "S"}


@pytest.fixture
def patched_state(monkeypatch):
    cfg = Config(
        username="u", password="p", pin="", email="", phone="",
        mfa_secret="", profile_path=None, save_session=False,
        enable_trading=False,
    )
    st = SessionState(cfg)
    monkeypatch.setattr(server, "config", cfg)
    monkeypatch.setattr(server, "state", st)
    monkeypatch.setattr("firstrade.mcp.state.FTAccountData", _FakeAccountData)
    return st


def _use_session(monkeypatch, session):
    monkeypatch.setattr(
        "firstrade.mcp.state.FTSession", lambda **kw: session,
    )
    return session


def test_login_saved_session_authenticates(monkeypatch, patched_state):
    _use_session(monkeypatch, _FakeSession(_SAVED_SESSION_PAYLOAD))
    res = call(server.login)
    assert res["status"] == "authenticated"
    assert res["accounts"] == ["12345678"]
    assert patched_state.is_authenticated is True


def test_login_pin_fast_path(monkeypatch, patched_state):
    server.config.pin = "1234"
    _use_session(monkeypatch, _FakeSession({}, pin="1234"))
    res = call(server.login)
    assert res["status"] == "authenticated"
    assert patched_state.is_authenticated is True


def test_login_authenticator_then_code(monkeypatch, patched_state):
    sess = _use_session(monkeypatch, _FakeSession(_AUTHENTICATOR_PAYLOAD))
    res = call(server.login)
    assert res["status"] == "mfa_authenticator"
    assert patched_state.awaiting_mfa is True
    assert patched_state.is_authenticated is False

    res2 = call(server.submit_mfa_code, "999999")
    assert res2["authenticated"] is True
    assert sess.login_two_called_with == "999999"


def test_login_otp_sms_flow(monkeypatch, patched_state):
    """The core requested flow: login -> request SMS -> submit code."""
    sess = _use_session(monkeypatch, _FakeSession(_OTP_PAYLOAD))
    res = call(server.login)
    assert res["status"] == "mfa_otp"
    # Masked recipients surfaced for the caller to choose.
    assert res["options"][0]["channel"] == "sms"
    assert res["options"][0]["recipient"] == "***-***-1234"
    assert patched_state.awaiting_mfa is False  # not until code requested

    sent = call(server.request_otp, 0)
    assert sent["code_sent"] is True
    assert sent["channel"] == "sms"
    assert patched_state.awaiting_mfa is True
    assert ("post", server.symbols.urls.request_code()) in sess.requests

    res2 = call(server.submit_mfa_code, "123456")
    assert res2["authenticated"] is True
    assert sess.login_two_called_with == "123456"
    assert patched_state.is_authenticated is True


def test_otp_submit_before_request_is_rejected(monkeypatch, patched_state):
    _use_session(monkeypatch, _FakeSession(_OTP_PAYLOAD))
    call(server.login)
    # Must request the code before submitting it.
    with pytest.raises(RuntimeError, match="Request a code first"):
        call(server.submit_mfa_code, "123456")


def test_request_otp_invalid_index(monkeypatch, patched_state):
    _use_session(monkeypatch, _FakeSession(_OTP_PAYLOAD))
    call(server.login)
    with pytest.raises(ValueError, match="Invalid option index"):
        call(server.request_otp, 9)


def test_request_otp_without_login(patched_state):
    with pytest.raises(RuntimeError, match="No OTP login"):
        call(server.request_otp, 0)


def test_login_missing_credentials(patched_state):
    server.config.username = ""
    res = call(server.login)
    assert "error" in res


def test_unauthenticated_account_access(patched_state):
    with pytest.raises(NotAuthenticatedError):
        call(server.list_accounts)


def test_resolve_account_defaults_and_validates(monkeypatch, patched_state):
    _use_session(monkeypatch, _FakeSession(_SAVED_SESSION_PAYLOAD))
    call(server.login)
    assert patched_state.resolve_account(None) == "12345678"
    with pytest.raises(ValueError, match="Unknown account"):
        patched_state.resolve_account("00000000")


# ---------------------------------------------------------------------------
# Trading tools honour the guard end-to-end
# ---------------------------------------------------------------------------

class _FakeOrder:
    last_kwargs = None

    def __init__(self, session):
        pass

    def place_order(self, **kwargs):
        _FakeOrder.last_kwargs = kwargs
        return {"error": "", "result": {}}


def _login(monkeypatch):
    _use_session(monkeypatch, _FakeSession(_SAVED_SESSION_PAYLOAD))
    call(server.login)


def test_place_equity_order_downgraded_when_disabled(monkeypatch, patched_state):
    _login(monkeypatch)
    server.config.enable_trading = False
    monkeypatch.setattr(order, "Order", _FakeOrder)

    res = call(
        server.place_equity_order,
        symbol="INTC", price_type="LIMIT", order_type="BUY", duration="DAY",
        quantity=1, price=1.0, dry_run=False, confirm=True,
    )
    # Guard forced a dry run despite dry_run=False/confirm=True.
    assert res["dry_run"] is True
    assert _FakeOrder.last_kwargs["dry_run"] is True
    assert "disabled" in res["note"]


def test_place_equity_order_live_when_enabled(monkeypatch, patched_state):
    _login(monkeypatch)
    server.config.enable_trading = True
    monkeypatch.setattr(order, "Order", _FakeOrder)

    res = call(
        server.place_equity_order,
        symbol="INTC", price_type="LIMIT", order_type="BUY", duration="DAY",
        quantity=1, price=1.0, dry_run=False, confirm=True,
    )
    assert res["dry_run"] is False
    assert _FakeOrder.last_kwargs["dry_run"] is False


def test_cancel_order_guarded(patched_state):
    server.config.enable_trading = False
    res = call(server.cancel_order, "abc")
    assert res["cancelled"] is False
