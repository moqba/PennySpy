import tempfile
from datetime import datetime

from selenium import webdriver
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

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
        user_data_dir = tempfile.mkdtemp()
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-data-dir={user_data_dir}")
        options.add_argument("--disable-gpu")
        self.driver = webdriver.Chrome(options=options)
        self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    def _save_screenshot(self, filename: str):
        filename += f"_{datetime.now().time().strftime('%H_%M_%S')}.png"
        self.driver.save_screenshot(filename)
        logger.info("saved screenshot at %s", filename)
