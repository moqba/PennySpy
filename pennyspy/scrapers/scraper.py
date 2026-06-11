from __future__ import annotations

import logging
import os
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypeVar

from browserforge.headers import HeaderGenerator
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
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


@dataclass(frozen=True)
class BrowserConfig:
    """Per-scraper browser configuration."""

    browser: Literal["chrome", "firefox"] = "chrome"
    headless: bool = DEFAULT_HEADLESS
    extra_arguments: list[str] = field(default_factory=list)


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
        driver = _create_chrome(config, user_agent, user_data_dir)

    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return driver, user_data_dir


def _create_chrome(config: BrowserConfig, user_agent: str, user_data_dir: Path) -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--enable-javascript")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    if config.headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument(f"--user-data-dir={user_data_dir}")
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


def _ensure_screenshot_dir(preferred: Path) -> Path:
    try:
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    except PermissionError as exc:
        fallback = _home_screenshot_dir() / preferred.name
        logger.warning(
            "cannot write screenshots to %s (%s); falling back to %s",
            preferred,
            exc,
            fallback,
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


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

    def _click(self, description: str, element: WebElement) -> None:
        logger.info("Starting action: %s", description)
        try:
            element.click()
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _submit(self, description: str, element: WebElement) -> None:
        logger.info("Starting action: %s", description)
        try:
            element.submit()
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _send_keys(self, description: str, element: WebElement, value: Any, *, sensitive: bool = False) -> None:
        logger.info("Starting action: %s%s", description, " (sensitive value redacted)" if sensitive else "")
        try:
            element.send_keys(value)
        except WebDriverException as e:
            raise WebDriverException(f"Failed while {description}") from e
        logger.info("Completed action: %s", description)

    def _save_screenshot(self, filename: str):
        now = datetime.now()
        filename += f"_{now.time().strftime('%H_%M_%S')}.png"
        screenshot_dir = _ensure_screenshot_dir(_resolve_screenshot_dir() / now.strftime("%Y_%m_%d"))
        screenshot_file_path = screenshot_dir / filename
        self.driver.save_screenshot(str(screenshot_file_path))
        logger.info("saved screenshot at %s", screenshot_file_path)
