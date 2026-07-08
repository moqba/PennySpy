"""CDP-native browser engine (zendriver) behind a synchronous Selenium-like facade.

Why this exists
---------------
Bank WAFs (BMO uses a Shape/F5-class defense) detect stock Selenium + ChromeDriver via
tells that survive the visible-Chrome hardening we previously did: ChromeDriver's injected
``cdc_*`` variables, the CDP ``Runtime.enable`` leak, and the WebDriver protocol layer.
``zendriver`` (the maintained fork of nodriver / successor to undetected-chromedriver) drives
Chrome directly over the DevTools Protocol with *no* chromedriver binary and *no*
``Runtime.enable`` call sequence, which removes those tells at the source.

zendriver is async; the rest of PennySpy (the ``BankScraper`` interface, the FastAPI routes,
the session object held across ``start_auth`` -> ``continue_auth``) is synchronous. Rather than
turn the whole stack async, this module runs a single zendriver event loop on a dedicated
daemon thread and exposes the *same synchronous method names* the bank scrapers already call
(``_navigate``, ``_find_element``, ``_wait_until``, ``_click``, ``_send_keys`` ...), plus a thin
``self.driver`` shim, so migrating a scraper is mostly swapping imports.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, TypeVar, cast

import zendriver
from zendriver import Browser, Config, Element, KeyEvents, KeyPressEvent, SpecialKeys, Tab

# Shared config + screenshot/HTML artifact helpers (engine-independent).
from pennyspy.scrapers.scraper import (
    BrowserConfig,
    _ensure_failure_html_dir,  # noqa: F401  (re-exported parity)
    _ensure_screenshot_dir,
    _resolve_failure_html_dir,
    _resolve_screenshot_dir,
    _verification_screenshot_name,
)

logger = logging.getLogger(__name__)
_WaitResult = TypeVar("_WaitResult")


# ── Locators / exceptions (drop-in replacements for the selenium imports) ───────────


class By:
    """The subset of selenium ``By`` strategies the bank scrapers use."""

    XPATH = "xpath"
    CSS_SELECTOR = "css selector"
    ID = "id"


class ScraperError(Exception):
    """Base error for the zendriver engine (parity with selenium ``WebDriverException``)."""


# Names mirror the selenium exceptions the bank code catches, so ``except`` clauses are
# unchanged after the import swap.
WebDriverException = ScraperError


class TimeoutException(ScraperError):
    pass


class ElementNotFound(ScraperError):
    pass


class StaleElementReferenceException(ScraperError):
    pass


# ── Async -> sync bridge ────────────────────────────────────────────────────────────


class _AsyncLoop:
    """A private asyncio loop running on a daemon thread; coroutines are submitted from the
    synchronous facade and awaited via ``.result()``."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="zendriver-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(self, coro: Any) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        try:
            self._loop.close()
        except Exception:  # pragma: no cover - best effort teardown
            pass


def _make_user_data_dir() -> Path:
    """Ephemeral per-session profile, honoring the same env overrides as the Selenium engine."""
    parent = os.environ.get("BROWSER_USER_DATA_DIR") or os.environ.get("CHROME_USER_DATA_DIR")
    if parent:
        try:
            Path(parent).mkdir(parents=True, exist_ok=True)
            return Path(tempfile.mkdtemp(dir=parent))
        except OSError as e:
            # A misconfigured parent (e.g. owned by a different uid under mode 700 in the
            # container) must not crash login. The profile is ephemeral and rmtree'd on quit,
            # so the system temp dir is a safe home.
            logger.warning(
                "Configured browser user-data parent %r is not usable (%s); "
                "falling back to the system temp dir.",
                parent,
                e,
            )
    return Path(tempfile.mkdtemp())


# ── JavaScript snippets applied to an element node ──────────────────────────────────

_JS_IS_DISPLAYED = """
(el) => {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return !!(rect.width || rect.height) &&
         style.visibility !== 'hidden' &&
         style.display !== 'none';
}
"""

# Names that callers read as live DOM properties rather than static HTML attributes.
_PROPERTY_ATTRIBUTES = frozenset({"value", "textContent", "innerText", "innerHTML"})


# ── Element wrapper ─────────────────────────────────────────────────────────────────


class ElementHandle:
    """Synchronous, Selenium-ish wrapper around a zendriver ``Element``."""

    def __init__(self, scraper: ZenScraper, element: Element) -> None:
        self._scraper = scraper
        self._el = element

    # Selenium parity surface used by the bank scrapers.
    @property
    def text(self) -> str:
        return cast("str | None", self._scraper._run(self._text_async())) or ""

    async def _text_async(self) -> str | None:
        try:
            await self._el.update()
        except Exception:
            pass
        return cast("str | None", self._el.text)

    def get_attribute(self, name: str) -> str | None:
        return cast("str | None", self._scraper._run(self._get_attribute_async(name)))

    async def _get_attribute_async(self, name: str) -> str | None:
        # Live DOM *properties* (current input value, rendered text) rather than the static
        # HTML attribute. Mirrors selenium's get_attribute property fallback for these names.
        if name in _PROPERTY_ATTRIBUTES:
            return cast("str | None", await self._el.apply(f"(el) => el[{name!r}]"))
        # Everything else (e.g. boolean "disabled") uses getAttribute so absent -> null/None,
        # which is what callers like the pagination check rely on.
        return cast("str | None", await self._el.apply(f"(el) => el.getAttribute({name!r})"))

    def is_displayed(self) -> bool:
        return bool(self._scraper._run(self._el.apply(_JS_IS_DISPLAYED)))

    def find_element(self, by: str, locator: str) -> ElementHandle:
        child = self._scraper._run(self._find_child_async(by, locator))
        if child is None:
            raise ElementNotFound(f"No child element for {by}={locator}")
        return ElementHandle(self._scraper, child)

    async def _find_child_async(self, by: str, locator: str) -> Element | None:
        if by == By.XPATH:
            raise NotImplementedError("scoped XPath element handles are not supported; read text via JS")
        return await self._el.query_selector(_css(by, locator))


def _css(by: str, locator: str) -> str:
    if by == By.CSS_SELECTOR:
        return locator
    if by == By.ID:
        return f"#{locator}"
    raise ValueError(f"Unsupported CSS-style locator strategy: {by}")


# ── Driver shim ─────────────────────────────────────────────────────────────────────


class _DriverShim:
    """Exposes only the raw-driver members the bank scrapers touch directly."""

    def __init__(self, scraper: ZenScraper) -> None:
        self._scraper = scraper

    @property
    def current_url(self) -> str:
        return cast(str, self._scraper._run(self._scraper._tab.evaluate("window.location.href")))

    @property
    def title(self) -> str:
        return cast(str, self._scraper._run(self._scraper._tab.evaluate("document.title")))

    def implicitly_wait(self, seconds: float) -> None:
        self._scraper._implicit_wait = float(seconds)

    def execute_script(self, script: str, *args: Any) -> Any:
        # Selenium scripts use "return X;"; wrap in an IIFE so ``return`` is valid.
        return self._scraper._run(self._scraper._tab.evaluate(f"(() => {{ {script} }})()"))

    def find_element(self, by: str, locator: str) -> ElementHandle:
        return self._scraper._find_element_impl(by, locator, timeout=self._scraper._implicit_wait)

    def find_elements(self, by: str, locator: str) -> list[ElementHandle]:
        return self._scraper._find_elements_impl(by, locator, timeout=0)

    def get_cookies(self) -> list[dict[str, Any]]:
        return cast("list[dict[str, Any]]", self._scraper._run(self._scraper._get_cookies_async()))

    def save_screenshot(self, path: str) -> None:
        self._scraper._run(self._scraper._tab.save_screenshot(path, format="png"))


# ── The scraper base ────────────────────────────────────────────────────────────────


class ZenScraper:
    """Synchronous facade over a zendriver browser. Mirrors the public surface of
    :class:`pennyspy.scrapers.scraper.Scraper` so bank scrapers can swap engines."""

    driver: _DriverShim

    def __init__(self, config: BrowserConfig = BrowserConfig()) -> None:
        self._config = config
        self._implicit_wait: float = 0.0
        self._loop = _AsyncLoop()
        self._user_data_dir = _make_user_data_dir()
        self._browser: Browser | None = self._loop.run(self._start_browser(config))
        self._tab: Tab = self._browser.main_tab
        self.driver = _DriverShim(self)

    async def _start_browser(self, config: BrowserConfig) -> Browser:
        # Container-stability args. No UA/WebGL spoofing: a real visible Chrome already presents
        # a coherent native fingerprint, which is the entire reason for the CDP-native engine.
        args = [
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
            "--window-size=1920,1080",
            *config.extra_arguments,
        ]
        chrome_bin = os.environ.get("CHROME_BIN")
        executable = chrome_bin if chrome_bin and Path(chrome_bin).exists() else None
        # In the container (bundled Chrome, no usable sandbox) we must pass --no-sandbox, which
        # zendriver does when sandbox=False. Locally keep the sandbox on.
        sandbox = executable is None
        zconf = Config(
            user_data_dir=str(self._user_data_dir),
            headless=config.headless,
            browser_executable_path=executable,
            browser="chrome",
            sandbox=sandbox,
            browser_args=args,
        )
        return await zendriver.start(zconf)

    # ── lifecycle ──────────────────────────────────────────────────────────────────

    def _run(self, coro: Any) -> Any:
        return self._loop.run(coro)

    def quit(self) -> None:
        try:
            if self._browser is not None:
                try:
                    self._loop.run(self._browser.stop())
                except Exception:
                    pass
                self._browser = None
        finally:
            self._loop.close()
            shutil.rmtree(self._user_data_dir, ignore_errors=True)
            self.driver = None  # type: ignore[assignment]

    # ── navigation / finding ─────────────────────────────────────────────────────────

    def _navigate(self, description: str, url: str) -> None:
        logger.info("Starting action: %s; navigating to %s", description, url)
        try:
            self._run(self._tab.get(url))
        except Exception as e:
            raise ScraperError(f"Failed while {description}; target URL: {url}") from e
        logger.info("Completed action: %s; current URL: %s", description, self.driver.current_url)

    def _find_element_impl(self, by: str, locator: str, *, timeout: float) -> ElementHandle:
        elements = self._find_elements_impl(by, locator, timeout=timeout)
        if not elements:
            raise ElementNotFound(f"No element found for {by}={locator}")
        return elements[0]

    def _find_elements_impl(self, by: str, locator: str, *, timeout: float) -> list[ElementHandle]:
        raw = self._run(self._find_raw_async(by, locator, timeout))
        return [ElementHandle(self, el) for el in raw]

    async def _find_raw_async(self, by: str, locator: str, timeout: float) -> list[Element]:
        # zendriver raises asyncio.TimeoutError when nothing matches within the timeout; selenium's
        # find_elements returns [] instead. Translate so callers (find_elements, the present/visible/
        # clickable conditions, and `try: _find_element except` blocks) get the no-match-is-empty
        # contract they expect rather than an exception escaping the _wait_until retry loop.
        try:
            if by == By.XPATH:
                # zendriver's xpath returns nothing at timeout=0 (it needs one poll cycle), so floor
                # it. _wait_until polls anyway, so a small per-call floor is enough for present nodes.
                return cast("list[Element]", await self._tab.xpath(locator, timeout=max(timeout, 0.3)))
            return cast("list[Element]", await self._tab.select_all(_css(by, locator), timeout=timeout))
        except TimeoutError:
            return []

    def _find_element(self, description: str, by: str, locator: str) -> ElementHandle:
        logger.info("Finding element to %s (%s=%s)", description, by, locator)
        try:
            element = self._find_element_impl(by, locator, timeout=max(self._implicit_wait, 1.0))
        except ScraperError as e:
            raise ScraperError(f"Failed while finding element to {description} ({by}={locator})") from e
        logger.info("Found element to %s", description)
        return element

    def _wait_until(
        self,
        description: str,
        condition: Callable[[_DriverShim], _WaitResult | None],
        timeout: int,
        *,
        screenshot_name: str | None = None,
        timeout_log_level: int = logging.ERROR,
    ) -> _WaitResult:
        logger.info("Waiting to %s (timeout: %ss)", description, timeout)
        deadline = time.monotonic() + timeout
        while True:
            try:
                result = condition(self.driver)
            except ScraperError:
                result = None  # type: ignore[assignment]
            if result:
                logger.info("Finished waiting to %s", description)
                return result
            if time.monotonic() >= deadline:
                if screenshot_name:
                    self._save_screenshot(screenshot_name)
                logger.log(timeout_log_level, "Timed out while %s after %ss", description, timeout)
                raise TimeoutException(f"Timed out while {description} after {timeout}s")
            time.sleep(0.3)

    # ── delays ───────────────────────────────────────────────────────────────────────

    def _action_delay(self, *, paced: bool) -> None:
        if paced:
            time.sleep(self._config.action_delay.sample())

    # ── interactions ─────────────────────────────────────────────────────────────────

    def _click(self, description: str, element: ElementHandle, *, paced: bool = True, human: bool = False) -> None:
        logger.info("Starting action: %s", description)
        try:
            self._run(self._click_async(element, human))
            self._action_delay(paced=paced)
        except Exception as e:
            raise ScraperError(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    async def _click_async(self, element: ElementHandle, human: bool) -> None:
        el = element._el
        await el.scroll_into_view()
        if human:
            # Extra interaction entropy for the most heavily fingerprinted clicks (e.g. BMO's
            # device-trust "Next"): a real pointer move + dwell before the click.
            await el.mouse_move()
            await asyncio.sleep(random.uniform(0.12, 0.35))
        await el.mouse_click()

    def _submit(self, description: str, element: ElementHandle, *, paced: bool = True) -> None:
        logger.info("Starting action: %s", description)
        try:
            self._run(element._el.send_keys(SpecialKeys.ENTER))
            self._action_delay(paced=paced)
        except Exception as e:
            raise ScraperError(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _send_keys(
        self,
        description: str,
        element: ElementHandle,
        value: Any,
        *,
        sensitive: bool = False,
        paced: bool = True,
    ) -> None:
        logger.info("Starting action: %s%s", description, " (sensitive value redacted)" if sensitive else "")
        try:
            self._run(self._send_keys_async(element, str(value), paced))
            self._action_delay(paced=paced)
        except Exception as e:
            raise ScraperError(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    async def _send_keys_async(self, element: ElementHandle, value: str, paced: bool) -> None:
        el = element._el
        await el.scroll_into_view()
        await el.mouse_click()  # focus
        characters = list(value)
        for index, character in enumerate(characters):
            # Send a full key-down/key-up sequence rather than a bare `char` event.
            # zendriver's `send_keys(str)` emits only CHAR events (keypress/input, no
            # keydown/keyup), which fields that gate on `onKeyDown` — notably
            # Wealthsimple's one-time-code input — silently ignore. DOWN_AND_UP also
            # inserts the character text, and gracefully falls back to CHAR for keys
            # with no key code, so credentials with symbols/spaces still type correctly.
            await el.send_keys(KeyEvents.from_text(character, KeyPressEvent.DOWN_AND_UP))
            if paced and index < len(characters) - 1:
                await asyncio.sleep(self._config.typing_delay.sample())

    def _clear_field(self, description: str, element: ElementHandle, *, paced: bool = True) -> None:
        logger.info("Clearing field before re-entry to %s", description)
        try:
            self._run(self._clear_field_async(element))
            self._action_delay(paced=paced)
        except Exception as e:
            raise ScraperError(f"Failed while clearing field to {description}") from e

    async def _clear_field_async(self, element: ElementHandle) -> None:
        el = element._el
        await el.mouse_click()
        await el.clear_input()

    def _send_keys_verified(
        self,
        description: str,
        element: ElementHandle,
        value: str,
        *,
        sensitive: bool = False,
        paced: bool = True,
        max_attempts: int = 3,
        screenshot_name: str | None = None,
    ) -> None:
        """Type ``value`` and try to confirm the field holds exactly that, retrying
        up to ``max_attempts`` times (guards against an overlay stealing focus
        mid-typing).

        Best-effort: readback is only a reliability guard, not a correctness check —
        some fields (e.g. masked one-time-code inputs) never report the typed value
        even when the characters landed. So an exhausted or failed readback logs a
        warning and returns rather than raising, letting the flow still try to
        connect. Only genuine typing/focus failures (from ``_send_keys`` /
        ``_clear_field``) still propagate."""
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self._clear_field(description, element, paced=paced)
            self._send_keys(description, element, value, sensitive=sensitive, paced=paced)
            try:
                actual = element.get_attribute("value")
            except ScraperError:
                logger.warning(
                    "Could not read back value for %s; proceeding despite unverified field", description
                )
                break
            if actual == value:
                logger.info("Verified %s on attempt %d/%d", description, attempt, max_attempts)
                return
            logger.warning(
                "Field for %s did not match expected after attempt %d/%d; retrying",
                description,
                attempt,
                max_attempts,
            )
        self._save_screenshot(screenshot_name or _verification_screenshot_name(description))
        logger.warning(
            "Field for %s could not be verified after %d attempts; proceeding to try to connect anyway",
            description,
            max_attempts,
        )

    # ── cookies ──────────────────────────────────────────────────────────────────────

    async def _get_cookies_async(self) -> list[dict[str, Any]]:
        assert self._browser is not None
        cookies = await self._browser.cookies.get_all()
        return [{"name": c.name, "value": c.value, "domain": c.domain, "path": c.path} for c in cookies]

    # ── screenshots ──────────────────────────────────────────────────────────────────

    def _save_screenshot(self, filename: str) -> None:
        now = datetime.now()
        filename += f"_{now.time().strftime('%H_%M_%S')}"
        screenshot_dir = _ensure_screenshot_dir(_resolve_screenshot_dir() / now.strftime("%Y_%m_%d"))
        screenshot_file_path = screenshot_dir / f"{filename}.png"
        try:
            self._run(self._tab.save_screenshot(str(screenshot_file_path), format="png"))
            logger.info("saved screenshot at %s", screenshot_file_path)
        except Exception as e:  # pragma: no cover - diagnostics only
            logger.warning("could not save screenshot %s: %s", filename, e)
        self._save_failure_html_if_enabled(filename, now)

    def _save_failure_html_if_enabled(self, filename: str, timestamp: datetime) -> None:
        html_dir = _resolve_failure_html_dir()
        if html_dir is None:
            return
        try:
            failure_html_dir = _ensure_failure_html_dir(html_dir / timestamp.strftime("%Y_%m_%d"))
            html_file_path = failure_html_dir / f"{filename}.html"
            html = self._run(self._tab.get_content())
            html_file_path.write_text(html, encoding="utf-8")
        except Exception as e:
            logger.warning("could not save failure page HTML for %s: %s", filename, e)
            return
        logger.info("saved failure page HTML at %s", html_file_path)


# ── Selenium-EC-style condition builders for _wait_until ─────────────────────────────


def clickable(by: str, locator: str) -> Callable[[_DriverShim], ElementHandle | None]:
    def condition(driver: _DriverShim) -> ElementHandle | None:
        elements = driver.find_elements(by, locator)
        if not elements:
            return None
        element = elements[0]
        if element.get_attribute("disabled") is not None:
            return None
        if not element.is_displayed():
            return None
        return element

    return condition


def visible(by: str, locator: str) -> Callable[[_DriverShim], ElementHandle | None]:
    def condition(driver: _DriverShim) -> ElementHandle | None:
        for element in driver.find_elements(by, locator):
            if element.is_displayed():
                return element
        return None

    return condition


def present(by: str, locator: str) -> Callable[[_DriverShim], ElementHandle | None]:
    def condition(driver: _DriverShim) -> ElementHandle | None:
        elements = driver.find_elements(by, locator)
        return elements[0] if elements else None

    return condition


def invisible(by: str, locator: str) -> Callable[[_DriverShim], bool | None]:
    def condition(driver: _DriverShim) -> bool | None:
        elements = driver.find_elements(by, locator)
        if not elements:
            return True
        return True if not any(e.is_displayed() for e in elements) else None

    return condition


def url_to_be(url: str) -> Callable[[_DriverShim], bool | None]:
    def condition(driver: _DriverShim) -> bool | None:
        return True if driver.current_url == url else None

    return condition


def url_contains(fragment: str) -> Callable[[_DriverShim], bool | None]:
    def condition(driver: _DriverShim) -> bool | None:
        return True if fragment in driver.current_url else None

    return condition
