"""Long-lived session state for the Firstrade MCP server.

A single :class:`SessionState` instance is shared across all tool calls for
the lifetime of the (stdio) server process. It owns the stateful
:class:`~firstrade.account.FTSession` and lazily builds
:class:`~firstrade.account.FTAccountData`.
"""

from firstrade.account import FTAccountData, FTSession
from firstrade.mcp.config import Config


class NotAuthenticatedError(RuntimeError):
    """Raised when an authed action is attempted before login completes."""

    def __init__(self) -> None:
        super().__init__(
            "Not authenticated. Call the `login` tool first "
            "(and `submit_mfa_code` if a code is required).",
        )


class SessionState:
    """Holds the active Firstrade session and account data."""

    def __init__(self, config: Config) -> None:
        """Initialize empty state bound to a configuration."""
        self.config = config
        self.session: FTSession | None = None
        self._account_data: FTAccountData | None = None
        self.awaiting_mfa: bool = False

    @property
    def is_authenticated(self) -> bool:
        """Whether a usable, account-loaded session exists."""
        return self.session is not None and self._account_data is not None

    def start_login(self) -> bool:
        """Begin login using configured credentials.

        Returns:
            bool: ``True`` if an MFA code is required (call
            :meth:`finish_mfa` next), ``False`` if login completed.

        """
        # debug is intentionally hard-disabled: debug mode logs tokens and
        # full responses, which must never happen inside an MCP server.
        self.session = FTSession(
            username=self.config.username,
            password=self.config.password,
            pin=self.config.pin,
            email=self.config.email,
            phone=self.config.phone,
            mfa_secret=self.config.mfa_secret,
            profile_path=self.config.profile_path,
            save_session=self.config.save_session,
            debug=False,
        )
        self._account_data = None
        need_code = self.session.login()
        self.awaiting_mfa = bool(need_code)
        if not self.awaiting_mfa:
            self._load_account_data()
        return self.awaiting_mfa

    def finish_mfa(self, code: str) -> None:
        """Complete a two-step login with an emailed/SMS code."""
        if self.session is None or not self.awaiting_mfa:
            raise RuntimeError(
                "No login is awaiting an MFA code. Call `login` first.",
            )
        self.session.login_two(code)
        self.awaiting_mfa = False
        self._load_account_data()

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
