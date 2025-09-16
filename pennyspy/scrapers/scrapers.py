import os
import tempfile
from datetime import datetime
from pathlib import Path
from random import random
from typing import Final

from fake_useragent import UserAgent

from selenium import webdriver
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

FALLBACK_USER_AGENTS: Final[list[str]] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
]

class Scraper:
    def __init__(self, headless=True):
        self.init_chrome_driver(headless)

    def init_chrome_driver(self, headless):
        options = webdriver.ChromeOptions()
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--enable-javascript")
        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)
        if headless:
            options.add_argument('--headless=new')
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        user_data_dir = Path(os.environ.get("CHROME_USER_DATA_DIR", tempfile.mkdtemp()))
        user_data_dir.mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument(f"user-agent={self._get_random_user_agent()}")
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def _get_random_user_agent(self):
        try:
            ua = UserAgent()
            return ua.random
        except Exception:
            logger.exception("Failed to fetch a random user agent, will use a fallback agent")
            return random.choice(FALLBACK_USER_AGENTS)

    def _save_screenshot(self, filename: str):
        filename += f"_{datetime.now().time().strftime('%H_%M_%S')}.png"
        screenshot_dir = Path(__file__).parent / f"screenshots_{datetime.now().strftime('%Y_%m_%d')}"
        screenshot_dir.mkdir(exist_ok=True, parents=True)
        screenshot_file_path = screenshot_dir / filename
        self.driver.save_screenshot(screenshot_file_path)
        logger.info("saved screenshot at %s", screenshot_file_path)
