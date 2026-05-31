"""Long-lived session state for the Firstrade MCP server.

A single :class:`SessionState` instance is shared across all tool calls for
the lifetime of the (stdio) server process. It owns the stateful
:class:`~firstrade.account.FTSession` and lazily builds
:class:`~firstrade.account.FTAccountData`.

The login flow is interactive and supports MFA codes that only arrive *after*
the login attempt (SMS/email OTP) without any secret being shared up front:

    login -> (server returns OTP recipient choices)
          -> request_otp(index)   # triggers the SMS/email
          -> submit_mfa_code(code)

PIN, TOTP-secret, and valid-saved-cookie logins are delegated to the
library's own ``FTSession.login`` since those complete without a second step.
"""

import json

from firstrade import urls
from firstrade.account import FTAccountData, FTSession
from firstrade.exceptions import LoginResponseError
from firstrade.mcp.config import Config

# Mirrors the base URL hit by FTSession.login before the credential POST.
_BASE_URL = "https://api3x.firstrade.com/"


class NotAuthenticatedError(RuntimeError):
    """Raised when an authed action is attempted before login completes."""

    def __init__(self) -> None:
        super().__init__(
            "Not authenticated. Call the `login` tool first "
            "(and `request_otp` / `submit_mfa_code` if a code is required).",
        )


class SessionState:
    """Holds the active Firstrade session and account data."""

    def __init__(self, config: Config) -> None:
        """Initialize empty state bound to a configuration."""
        self.config = config
        self.session: FTSession | None = None
        self._account_data: FTAccountData | None = None
        self.awaiting_mfa: bool = False
        # One of: None, "authenticator", "otp".
        self.mfa_mode: str | None = None
        self.otp_options: list[dict[str, str]] = []
        self.otp_requested: bool = False

    @property
    def is_authenticated(self) -> bool:
        """Whether a usable, account-loaded session exists."""
        return self.session is not None and self._account_data is not None

    # ------------------------------------------------------------------
    # Login orchestration
    # ------------------------------------------------------------------

    def start_login(self) -> dict[str, object]:
        """Begin login using configured credentials.

        Returns a status dict whose ``status`` is one of:

        - ``"authenticated"``: login completed (e.g. PIN, TOTP secret, or a
          valid saved session cookie).
        - ``"mfa_authenticator"``: a code from an authenticator app is needed;
          call :meth:`finish_mfa`.
        - ``"mfa_otp"``: an SMS/email code is needed; the dict also contains
          ``"options"``. Call :meth:`request_otp` then :meth:`finish_mfa`.

        """
        cfg = self.config
        self.session = FTSession(
            username=cfg.username,
            password=cfg.password,
            pin=cfg.pin,
            email=cfg.email,
            phone=cfg.phone,
            mfa_secret=cfg.mfa_secret,
            profile_path=cfg.profile_path,
            save_session=cfg.save_session,
            # debug is intentionally hard-disabled: debug mode logs tokens and
            # full responses, which must never happen inside an MCP server.
            debug=False,
        )
        self._account_data = None
        self.awaiting_mfa = False
        self.mfa_mode = None
        self.otp_options = []
        self.otp_requested = False

        # PIN, TOTP-secret, and valid saved cookies are fully handled by the
        # library's own login(); only the "code arrives later" paths need
        # manual orchestration.
        if cfg.pin or cfg.mfa_secret:
            need_code = self.session.login()
            if need_code:
                # Unexpected for pin/secret, but keep the door open.
                self.mfa_mode = "authenticator"
                self.awaiting_mfa = True
                return {"status": "mfa_authenticator"}
            self._load_account_data()
            return {"status": "authenticated"}

        return self._manual_login()

    def _manual_login(self) -> dict[str, object]:
        """Drive the pre-MFA login sequence, stopping at the MFA decision.

        Mirrors :meth:`FTSession.login` up to the credential POST but, instead
        of auto-selecting an OTP recipient (which requires email/phone up
        front), it surfaces the available recipients so the caller can choose.
        """
        s = self.session
        assert s is not None  # noqa: S101 - set by start_login

        s.session.headers.update(urls.session_headers())
        ftat = s._load_cookies()
        if ftat:
            s.session.headers["ftat"] = ftat
        s._request("get", url=_BASE_URL, timeout=10)
        s.session.headers["access-token"] = urls.access_token()

        resp = s._request(
            "post",
            url=urls.login(),
            data={"username": self.config.username, "password": self.config.password},
        )
        try:
            login_json = resp.json()
        except json.JSONDecodeError as exc:
            raise LoginResponseError("Invalid JSON is your account funded?") from exc
        s.login_json = login_json

        # Already authenticated via a saved ftat cookie.
        if (
            "mfa" not in login_json
            and "ftat" in login_json
            and not login_json.get("error")
        ):
            s.session.headers["sid"] = login_json["sid"]
            s.session.headers["ftat"] = login_json["ftat"]
            self._load_account_data()
            return {"status": "authenticated"}

        if login_json.get("error"):
            raise LoginResponseError(login_json["error"])

        s.t_token = login_json.get("t_token")

        # Authenticator-app TOTP: the code is entered directly, no send step.
        if login_json.get("mfa"):
            self.mfa_mode = "authenticator"
            self.awaiting_mfa = True
            return {"status": "mfa_authenticator"}

        # SMS/email OTP: surface recipients for an explicit choice.
        otp = login_json.get("otp")
        if otp:
            self.otp_options = list(otp)
            self.mfa_mode = "otp"
            return {"status": "mfa_otp", "options": self.otp_choices()}

        raise LoginResponseError(
            "MFA required but the server offered no authenticator or OTP option.",
        )

    def otp_choices(self) -> list[dict[str, object]]:
        """Return the maskable OTP recipient choices for the caller."""
        return [
            {
                "index": i,
                "channel": item.get("channel"),
                "recipient": item.get("recipientMask"),
            }
            for i, item in enumerate(self.otp_options)
        ]

    def request_otp(self, index: int = 0) -> dict[str, object]:
        """Trigger sending an OTP code to the chosen recipient.

        Args:
            index: Position in the list returned by :meth:`otp_choices`.

        Returns:
            dict: The ``channel`` and masked ``recipient`` the code was sent
            to.

        """
        if self.session is None or self.mfa_mode != "otp":
            raise RuntimeError(
                "No OTP login is in progress. Call `login` first.",
            )
        if index < 0 or index >= len(self.otp_options):
            raise ValueError(
                f"Invalid option index {index}. Available: {self.otp_choices()}",
            )
        item = self.otp_options[index]
        resp = self.session._request(
            "post",
            urls.request_code(),
            data={"recipientId": item["recipientId"], "t_token": self.session.t_token},
        )
        otp_json = resp.json()
        # Mirror the library: subsequent verify reads sid/t_token, and
        # login_two branches on login_json (no "mfa" key -> OTP path).
        self.session.login_json = otp_json
        if otp_json.get("error"):
            raise LoginResponseError(otp_json["error"])
        self.session.session.headers["sid"] = otp_json["verificationSid"]
        self.otp_requested = True
        self.awaiting_mfa = True
        return {"channel": item.get("channel"), "recipient": item.get("recipientMask")}

    def finish_mfa(self, code: str) -> None:
        """Complete login with an authenticator/SMS/email code."""
        if self.mfa_mode == "otp" and not self.otp_requested:
            raise RuntimeError(
                "Request a code first via `request_otp` before submitting it.",
            )
        if self.session is None or not self.awaiting_mfa:
            raise RuntimeError(
                "No login is awaiting a code. Call `login` first.",
            )
        self.session.login_two(code)
        self.awaiting_mfa = False
        self.mfa_mode = None
        self.otp_requested = False
        self._load_account_data()

    # ------------------------------------------------------------------
    # Account data access
    # ------------------------------------------------------------------

    def _load_account_data(self) -> None:
        """Fetch account data for the authenticated session."""
        assert self.session is not None  # noqa: S101 - guarded by callers
        self._account_data = FTAccountData(self.session)

    @property
    def account_data(self) -> FTAccountData:
        """Return account data, raising if not authenticated."""
        if self._account_data is None:
            raise NotAuthenticatedError()
        return self._account_data

    def require_session(self) -> FTSession:
        """Return the session, raising if not authenticated."""
        if self.session is None or self._account_data is None:
            raise NotAuthenticatedError()
        return self.session

    def resolve_account(self, account: str | None) -> str:
        """Return ``account`` or default to the first account number."""
        numbers = self.account_data.account_numbers
        if not numbers:
            raise RuntimeError("No accounts available on this login.")
        if account is None or account == "":
            return numbers[0]
        if account not in numbers:
            raise ValueError(
                f"Unknown account '{account}'. Available: {numbers}",
            )
        return account
