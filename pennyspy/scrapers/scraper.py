from __future__ import annotations

import logging
import os
import random
import re
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from browserforge.headers import HeaderGenerator
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support.ui import WebDriverWait

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
_WaitResult = TypeVar("_WaitResult")
Locator = tuple[str, str]

DEFAULT_HEADLESS: bool = True
_DOCKER_DATA_DIR = Path("/app/data")
_DOCKER_SCREENSHOT_DIR = _DOCKER_DATA_DIR / "screenshots"
_FAILURE_HTML_DIR_ENV = "PENNYSPY_FAILURE_HTML_DIR"

_ACCEPT_LANGUAGE = "en-US,en;q=0.9"

# Injected on every new document (before page scripts run) via CDP so the patches
# survive navigations, unlike a one-shot execute_script against about:blank. Each
# patch is guarded so a failure in one cannot abort the rest.
_STEALTH_JS = r"""
(() => {
  const def = (obj, prop, value) => {
    try {
      Object.defineProperty(obj, prop, { get: () => value, configurable: true });
    } catch (e) {}
  };

  // navigator.webdriver -> undefined (persists across navigations)
  def(navigator, 'webdriver', undefined);

  // navigator.languages -> realistic, non-empty
  def(navigator, 'languages', ['en-US', 'en']);

  // window.chrome -> present (missing in headless)
  try {
    if (!window.chrome) {
      window.chrome = { runtime: {} };
    }
  } catch (e) {}

  // navigator.plugins / mimeTypes -> non-empty (empty in headless)
  try {
    const makePlugin = (name, filename, desc) => {
      const plugin = { name, filename, description: desc, length: 1 };
      Object.defineProperty(plugin, '0', { value: { type: 'application/pdf' } });
      return plugin;
    };
    const plugins = [
      makePlugin('Chrome PDF Plugin', 'internal-pdf-viewer', 'Portable Document Format'),
      makePlugin('Chrome PDF Viewer', 'mhjfbmdgcfjbbpaeojofohoefgiehjai', ''),
      makePlugin('Native Client', 'internal-nacl-plugin', ''),
    ];
    def(navigator, 'plugins', plugins);
    def(navigator, 'mimeTypes', [{ type: 'application/pdf', suffixes: 'pdf' }]);
  } catch (e) {}

  // permissions.query -> reconcile the headless Notification quirk
  try {
    const original = navigator.permissions && navigator.permissions.query;
    if (original) {
      navigator.permissions.query = (parameters) =>
        parameters && parameters.name === 'notifications'
          ? Promise.resolve({ state: Notification.permission })
          : original.call(navigator.permissions, parameters);
    }
  } catch (e) {}

  // WebGL vendor/renderer -> mask SwiftShader (forced by --disable-gpu in Docker)
  try {
    const spoof = (proto) => {
      const getParameter = proto.getParameter;
      proto.getParameter = function (parameter) {
        if (parameter === 37445) return 'Intel Inc.';            // UNMASKED_VENDOR_WEBGL
        if (parameter === 37446) return 'Intel Iris OpenGL Engine'; // UNMASKED_RENDERER_WEBGL
        return getParameter.call(this, parameter);
      };
    };
    if (window.WebGLRenderingContext) spoof(WebGLRenderingContext.prototype);
    if (window.WebGL2RenderingContext) spoof(WebGL2RenderingContext.prototype);
  } catch (e) {}
})();
"""


@dataclass(frozen=True)
class DelayRange:
    """Validated range of delay values in seconds."""

    minimum: float
    maximum: float

    def __post_init__(self) -> None:
        if self.minimum < 0 or self.maximum < 0:
            raise ValueError("delays must be non-negative")
        if self.minimum > self.maximum:
            raise ValueError("delay minimum cannot exceed maximum")

    def sample(self) -> float:
        return random.uniform(self.minimum, self.maximum)


@dataclass(frozen=True)
class BrowserConfig:
    """Per-scraper browser configuration."""

    browser: Literal["chrome", "firefox"] = "chrome"
    headless: bool = DEFAULT_HEADLESS
    extra_arguments: list[str] = field(default_factory=list)
    action_delay: DelayRange = field(default_factory=lambda: DelayRange(0.4, 1.0))
    typing_delay: DelayRange = field(default_factory=lambda: DelayRange(0.04, 0.12))


def create_browser(config: BrowserConfig) -> tuple[WebDriver, Path]:
    """Create a WebDriver and temp profile directory from a BrowserConfig."""
    user_agent = HeaderGenerator(browser=config.browser).generate().get("User-Agent", "")

    parent = os.environ.get("BROWSER_USER_DATA_DIR") or os.environ.get("CHROME_USER_DATA_DIR")
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
        user_data_dir = Path(tempfile.mkdtemp(dir=parent))
    else:
        user_data_dir = Path(tempfile.mkdtemp())

    if config.browser == "firefox":
        driver = _create_firefox(config, user_agent, user_data_dir)
    else:
        chrome = _create_chrome(config, user_agent, user_data_dir)
        _apply_stealth(chrome, user_agent)
        driver = chrome
    return driver, user_data_dir


def _build_user_agent_metadata(user_agent: str) -> dict[str, Any]:
    """Derive Chrome client-hint metadata from a UA string so sec-ch-ua stays consistent."""
    version_match = re.search(r"Chrome/(\d+)(\.[\d.]+)?", user_agent)
    major = version_match.group(1) if version_match else "138"
    full_version = f"{major}{version_match.group(2)}" if version_match and version_match.group(2) else f"{major}.0.0.0"

    if "Windows" in user_agent:
        platform, platform_version = "Windows", "10.0.0"
    elif "Macintosh" in user_agent or "Mac OS X" in user_agent:
        platform, platform_version = "macOS", "13.0.0"
    else:
        platform, platform_version = "Linux", ""

    brands = [
        {"brand": "Not)A;Brand", "version": "99"},
        {"brand": "Google Chrome", "version": major},
        {"brand": "Chromium", "version": major},
    ]
    full_version_brands = [
        {"brand": b["brand"], "version": full_version if b["version"] == major else "99.0.0.0"} for b in brands
    ]

    return {
        "brands": brands,
        "fullVersionList": full_version_brands,
        "fullVersion": full_version,
        "platform": platform,
        "platformVersion": platform_version,
        "architecture": "x86",
        "bitness": "64",
        "model": "",
        "mobile": False,
        "wow64": False,
    }


def _apply_stealth(driver: webdriver.Chrome, user_agent: str) -> None:
    """Install persistent stealth patches and sync the UA with its client hints via CDP."""
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {"source": _STEALTH_JS})
    if user_agent:
        driver.execute_cdp_cmd(
            "Network.setUserAgentOverride",
            {
                "userAgent": user_agent,
                "acceptLanguage": _ACCEPT_LANGUAGE,
                "userAgentMetadata": _build_user_agent_metadata(user_agent),
            },
        )


def _create_chrome(config: BrowserConfig, user_agent: str, user_data_dir: Path) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if config.headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    # Headless defaults to a ~800x600 window, a strong automation signal.
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=en-US")
    # Kept for Docker stability; the WebGL vendor/renderer the resulting SwiftShader
    # backend reports is masked by the stealth script in _apply_stealth.
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument(f"user-agent={user_agent}")
    for arg in config.extra_arguments:
        options.add_argument(arg)
    return webdriver.Chrome(options=options)


def _create_firefox(config: BrowserConfig, user_agent: str, user_data_dir: Path) -> webdriver.Firefox:
    options = webdriver.FirefoxOptions()
    options.set_preference("general.useragent.override", user_agent)
    options.set_preference("dom.webdriver.enabled", False)
    options.set_preference("useAutomationExtension", False)
    options.set_preference("profile", str(user_data_dir))
    if config.headless:
        options.add_argument("--headless")
    for arg in config.extra_arguments:
        options.add_argument(arg)
    return webdriver.Firefox(options=options)


def _repo_checkout_screenshot_dir() -> Path | None:
    repo_root = Path(__file__).resolve().parents[2]
    if (repo_root / "pyproject.toml").is_file():
        return repo_root / "pennyspy-data" / "screenshots"
    return None


def _home_screenshot_dir() -> Path:
    return Path.home() / ".pennyspy" / "screenshots"


def _home_failure_html_dir() -> Path:
    return Path.home() / ".pennyspy" / "failure-html"


def _resolve_screenshot_dir() -> Path:
    env_dir = os.environ.get("PENNYSPY_SCREENSHOT_DIR")
    if env_dir:
        return Path(env_dir)
    if _DOCKER_DATA_DIR.exists() and os.access(_DOCKER_DATA_DIR, os.W_OK):
        return _DOCKER_SCREENSHOT_DIR
    checkout_dir = _repo_checkout_screenshot_dir()
    if checkout_dir is not None:
        return checkout_dir
    return _home_screenshot_dir()


def _resolve_failure_html_dir() -> Path | None:
    env_dir = os.environ.get(_FAILURE_HTML_DIR_ENV)
    if not env_dir:
        return None
    return Path(env_dir)


def _ensure_artifact_dir(preferred: Path, fallback_root: Path, label: str) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except PermissionError as exc:
        fallback = fallback_root / preferred.name
        logger.warning(
            "cannot write %s to %s (%s); falling back to %s",
            label,
            preferred,
            exc,
            fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def _ensure_screenshot_dir(preferred: Path) -> Path:
    return _ensure_artifact_dir(preferred, _home_screenshot_dir(), "screenshots")


def _ensure_failure_html_dir(preferred: Path) -> Path:
    return _ensure_artifact_dir(preferred, _home_failure_html_dir(), "failure HTML")


def _verification_screenshot_name(description: str) -> str:
    slug = "".join(char if char.isalnum() else "_" for char in description.lower()).strip("_")
    return f"verify_failed_{slug}" if slug else "verify_failed"


class Scraper:
    driver: WebDriver

    def __init__(self, config: BrowserConfig = BrowserConfig()):
        self._config = config
        self.driver, self._user_data_dir = create_browser(config)

    def quit(self):
        try:
            if self.driver is not None:
                self.driver.quit()
                self.driver = None  # type: ignore[assignment]
        finally:
            shutil.rmtree(self._user_data_dir, ignore_errors=True)

    def _navigate(self, description: str, url: str) -> None:
        logger.info("Starting action: %s; navigating to %s", description, url)
        try:
            self.driver.get(url)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}; target URL: {url}") from e
        logger.info("Completed action: %s; current URL: %s", description, self.driver.current_url)

    def _wait_until(
        self,
        description: str,
        condition: Callable[[WebDriver], _WaitResult],
        timeout: int,
        *,
        screenshot_name: str | None = None,
        timeout_log_level: int = logging.ERROR,
    ) -> _WaitResult:
        logger.info("Waiting to %s (timeout: %ss)", description, timeout)
        try:
            result = WebDriverWait(self.driver, timeout).until(condition)
        except TimeoutException as e:
            if screenshot_name:
                self._save_screenshot(screenshot_name)
            logger.log(timeout_log_level, "Timed out while %s after %ss", description, timeout)
            raise TimeoutException(f"Timed out while {description} after {timeout}s") from e
        logger.info("Finished waiting to %s", description)
        return result

    def _find_element(self, description: str, by: str, locator: str) -> WebElement:
        logger.info("Finding element to %s (%s=%s)", description, by, locator)
        try:
            element = self.driver.find_element(by, locator)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while finding element to {description} ({by}={locator})") from e
        logger.info("Found element to %s", description)
        return element

    def _action_delay(self, *, paced: bool) -> None:
        if paced:
            time.sleep(self._config.action_delay.sample())

    def _typing_delay(self, *, paced: bool) -> None:
        if paced:
            time.sleep(self._config.typing_delay.sample())

    def _click(self, description: str, element: WebElement, *, paced: bool = True) -> None:
        logger.info("Starting action: %s", description)
        try:
            ActionChains(self.driver).move_to_element(element).click().perform()
            self._action_delay(paced=paced)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _submit(self, description: str, element: WebElement, *, paced: bool = True) -> None:
        logger.info("Starting action: %s", description)
        try:
            ActionChains(self.driver).move_to_element(element).click().send_keys(Keys.ENTER).perform()
            self._action_delay(paced=paced)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _send_keys(
        self,
        description: str,
        element: WebElement,
        value: Any,
        *,
        sensitive: bool = False,
        paced: bool = True,
    ) -> None:
        logger.info("Starting action: %s%s", description, " (sensitive value redacted)" if sensitive else "")
        try:
            ActionChains(self.driver).move_to_element(element).click().perform()
            characters = list(value) if isinstance(value, str) else [value]
            for index, character in enumerate(characters):
                ActionChains(self.driver).send_keys(character).perform()
                if index < len(characters) - 1:
                    self._typing_delay(paced=paced)
            self._action_delay(paced=paced)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _clear_field(self, description: str, element: WebElement, *, paced: bool = True) -> None:
        logger.info("Clearing field before re-entry to %s", description)
        try:
            (
                ActionChains(self.driver)
                .move_to_element(element)
                .click()
                .send_keys(Keys.CONTROL + "a")
                .send_keys(Keys.DELETE)
                .perform()
            )
            self._action_delay(paced=paced)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while clearing field to {description}") from e

    def _send_keys_verified(
        self,
        description: str,
        element: WebElement,
        value: str,
        *,
        sensitive: bool = False,
        paced: bool = True,
        max_attempts: int = 3,
        screenshot_name: str | None = None,
    ) -> None:
        """Type ``value`` and confirm the field holds exactly that, retrying up to
        ``max_attempts`` times. Guards against entry being interrupted (e.g. by an
        overlay/cookie banner stealing focus and clearing the field mid-typing)."""
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self._clear_field(description, element, paced=paced)
            self._send_keys(description, element, value, sensitive=sensitive, paced=paced)
            try:
                actual = element.get_attribute("value")
            except WebDriverException as e:
                self._save_screenshot(screenshot_name or _verification_screenshot_name(description))
                raise WebDriverException(f"Failed while verifying value for {description}") from e
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
        raise WebDriverException(
            f"Field for {description} did not match expected after {max_attempts} attempts"
        )

    def _save_screenshot(self, filename: str):
        now = datetime.now()
        filename += f"_{now.time().strftime('%H_%M_%S')}"
        screenshot_dir = _ensure_screenshot_dir(_resolve_screenshot_dir() / now.strftime("%Y_%m_%d"))
        screenshot_file_path = screenshot_dir / f"{filename}.png"
        self.driver.save_screenshot(str(screenshot_file_path))
        logger.info("saved screenshot at %s", screenshot_file_path)
        self._save_failure_html_if_enabled(filename, now)

    def _save_failure_html_if_enabled(self, filename: str, timestamp: datetime) -> None:
        html_dir = _resolve_failure_html_dir()
        if html_dir is None:
            return

        try:
            failure_html_dir = _ensure_failure_html_dir(html_dir / timestamp.strftime("%Y_%m_%d"))
            html_file_path = failure_html_dir / f"{filename}.html"
            html_file_path.write_text(self.driver.page_source, encoding="utf-8")
        except WebDriverException as e:
            logger.warning("could not read failure page HTML for %s: %s", filename, e)
            return
        except OSError as e:
            logger.warning("could not save failure page HTML for %s: %s", filename, e)
            return

        logger.info("saved failure page HTML at %s", html_file_path)
