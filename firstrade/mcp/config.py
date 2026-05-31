"""Environment-driven configuration for the Firstrade MCP server.

All secrets and runtime toggles are sourced from environment variables so
that credentials never have to be passed as tool arguments (which would land
in the agent's conversation transcript).
"""

import os
from dataclasses import dataclass


def _env_bool(name: str, *, default: bool = False) -> bool:
    """Return a boolean from an environment variable.

    Accepts ``1/true/yes/on`` (case-insensitive) as truthy values.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    """Resolved configuration for a server instance.

    Attributes:
        username: Firstrade login username (``FIRSTRADE_USERNAME``).
        password: Firstrade login password (``FIRSTRADE_PASSWORD``).
        pin: Optional login PIN (``FIRSTRADE_PIN``).
        email: Optional MFA email (``FIRSTRADE_EMAIL``).
        phone: Optional MFA phone (``FIRSTRADE_PHONE``).
        mfa_secret: Optional TOTP secret for automated MFA
            (``FIRSTRADE_MFA_SECRET``).
        profile_path: Directory used to persist the session token
            (``FIRSTRADE_PROFILE_PATH``).
        save_session: Persist the session token to disk
            (``FIRSTRADE_SAVE_SESSION``).
        enable_trading: Master switch that allows live (non-dry-run) orders
            (``FIRSTRADE_ENABLE_TRADING``). Defaults to ``False``.

    """

    username: str
    password: str
    pin: str
    email: str
    phone: str
    mfa_secret: str
    profile_path: str | None
    save_session: bool
    enable_trading: bool

    @classmethod
    def from_env(cls) -> "Config":
        """Build a :class:`Config` from the process environment."""
        return cls(
            username=os.environ.get("FIRSTRADE_USERNAME", ""),
            password=os.environ.get("FIRSTRADE_PASSWORD", ""),
            pin=os.environ.get("FIRSTRADE_PIN", ""),
            email=os.environ.get("FIRSTRADE_EMAIL", ""),
            phone=os.environ.get("FIRSTRADE_PHONE", ""),
            mfa_secret=os.environ.get("FIRSTRADE_MFA_SECRET", ""),
            profile_path=os.environ.get("FIRSTRADE_PROFILE_PATH") or None,
            save_session=_env_bool("FIRSTRADE_SAVE_SESSION", default=False),
            enable_trading=_env_bool("FIRSTRADE_ENABLE_TRADING", default=False),
        )

    @property
    def has_credentials(self) -> bool:
        """Whether a username and password are configured."""
        return bool(self.username and self.password)
