from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pennyspy.scrapers.scraper import BrowserConfig, Scraper


@dataclass
class AuthStep:
    """Represents one step in a scraper's authentication flow.

    - ``needs_otp``: the scraper is waiting for the user to provide an OTP code.
    - ``waiting_for_external``: the scraper is waiting for the user to approve
      a push notification, click an email link, complete 2SV in their app, etc.
    - ``authenticated``: authentication is complete and the scraper is ready to
      download transactions.
    """

    status: Literal["needs_otp", "waiting_for_external", "authenticated"]
    message: str = ""


class BankScraper(Scraper, ABC):
    """Abstract interface that every bank scraper must implement.

    Each concrete scraper should accept typed keyword arguments in its
    ``start_auth`` and ``download_transactions`` overrides rather than
    relying on untyped ``**kwargs``.  The ``**kwargs`` in the ABC signature
    exists only so the generic router can forward arbitrary per-bank
    parameters without knowing the concrete type.
    """

    def __init__(self, config: BrowserConfig = BrowserConfig()):
        super().__init__(config=config)

    @abstractmethod
    def start_auth(self, **kwargs: Any) -> AuthStep:
        """Begin the authentication flow (navigate to login page, enter credentials).

        Returns an ``AuthStep`` indicating what the caller should do next.
        """

    @abstractmethod
    def continue_auth(self, *, otp_code: str | None = None) -> AuthStep:
        """Continue (or complete) the authentication flow.

        For scrapers that require an OTP, pass it via ``otp_code``.
        For scrapers waiting on external approval, call with no arguments.
        """

    @abstractmethod
    def download_transactions(self, *, export_directory: Path, **kwargs: Any) -> Path:
        """Download transactions and return the path to the resulting file."""
