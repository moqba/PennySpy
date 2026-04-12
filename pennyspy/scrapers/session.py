from __future__ import annotations

import logging
import time
import uuid

from pennyspy.scrapers.base import BankScraper

logger = logging.getLogger(__name__)


class ScraperSessionManager:
    """Manages scraper instances keyed by session ID, with automatic TTL-based cleanup.

    Sessions are scoped to a scraper type so that a session created for one
    bank cannot be used to scrape a different bank.
    """

    def __init__(self, ttl_seconds: int = 600):
        self._ttl = ttl_seconds
        self._sessions: dict[str, tuple[BankScraper, type[BankScraper], float]] = {}

    def create(self, scraper: BankScraper) -> str:
        self._cleanup_stale()
        session_id = str(uuid.uuid4())
        self._sessions[session_id] = (scraper, type(scraper), time.monotonic())
        return session_id

    def get(self, session_id: str, expected_type: type[BankScraper] | None = None) -> BankScraper:
        entry = self._sessions.get(session_id)
        if entry is None:
            raise KeyError(f"Session {session_id} not found")
        scraper, scraper_type, _ = entry
        if expected_type is not None and scraper_type is not expected_type:
            raise KeyError(f"Session {session_id} belongs to {scraper_type.__name__}, not {expected_type.__name__}")
        return scraper

    def remove(self, session_id: str) -> None:
        entry = self._sessions.pop(session_id, None)
        if entry is not None:
            scraper, _, _ = entry
            try:
                scraper.quit()
            except Exception:
                logger.debug("Error quitting scraper for session %s", session_id, exc_info=True)

    def close_all(self) -> None:
        if not self._sessions:
            return
        count = len(self._sessions)
        for session_id, (scraper, _, _) in self._sessions.items():
            try:
                scraper.quit()
            except Exception:
                logger.warning("Error quitting scraper for session %s", session_id, exc_info=True)
        self._sessions.clear()
        logger.info("Closed %d active scraper session(s)", count)

    def _cleanup_stale(self) -> None:
        now = time.monotonic()
        stale = [sid for sid, (_, _, ts) in self._sessions.items() if now - ts > self._ttl]
        for sid in stale:
            logger.warning("Cleaning up stale session %s", sid)
            self.remove(sid)
