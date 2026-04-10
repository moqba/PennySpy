from __future__ import annotations

import logging
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from browserforge.headers import HeaderGenerator
from selenium import webdriver
from selenium.webdriver.remote.webdriver import WebDriver

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BrowserConfig:
    """Per-scraper browser configuration."""

    browser: Literal["chrome", "firefox"] = "chrome"
    headless: bool = True
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

    def _save_screenshot(self, filename: str):
        filename += f"_{datetime.now().time().strftime('%H_%M_%S')}.png"
        screenshot_dir = Path(__file__).parent / f"screenshots_{datetime.now().strftime('%Y_%m_%d')}"
        screenshot_dir.mkdir(exist_ok=True, parents=True)
        screenshot_file_path = screenshot_dir / filename
        self.driver.save_screenshot(screenshot_file_path)
        logger.info("saved screenshot at %s", screenshot_file_path)
