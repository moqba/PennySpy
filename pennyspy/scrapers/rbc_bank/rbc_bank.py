from __future__ import annotations

from http import HTTPStatus
from pathlib import Path
from typing import Final
from time import sleep

import requests
import logging
import re

from selenium.common import TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By

from pennyspy.scrapers.rbc_bank.connection_element_id import ConnectionElementId
from pennyspy.scrapers.rbc_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.rbc_bank.get_default_filename import get_default_filename
from pennyspy.scrapers.rbc_bank.identifiers import USERNAME, PASSWORD
from pennyspy.scrapers.rbc_bank.request_options import Software, AccountInfo, Include
from pennyspy.scrapers.scrapers import Scraper

RBC_MAINPAGE: Final[str] = "https://www1.royalbank.com/cgi-bin/rbaccess/rbunxcgi?F6=1&F7=IB&F21=IB&F22=IB&REQUEST=ClientSignin&LANGUAGE=ENGLISH"

logger = logging.getLogger(__name__)

class RBCBank(Scraper):
    def __init__(self, headless=True):
        super().__init__(headless=headless)
        self.cookies = None

    def get_session_cookies(self):
        logger.info('Getting session cookies')
        self.driver.get(RBC_MAINPAGE)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        self._login(USERNAME, PASSWORD)
        self._accept_cookies_if_visible()
        self._wait_until_connected()
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        sleep(DelaySeconds.COOKIE_INIT)
        self.cookies = self.driver.get_cookies()
        self.driver.close()

    def _login(self, username, password):
        logger.info("logging in...")
        self.driver.find_element(By.ID , ConnectionElementId.USERNAME).send_keys(username)
        self.driver.find_element(By.ID, ConnectionElementId.PASSWORD).send_keys(password)
        self.driver.find_element(By.ID, ConnectionElementId.PASSWORD).submit()

    def _accept_cookies_if_visible(self):
        try:
            accept_cookies = WebDriverWait(self.driver, DelaySeconds.COOKIE_PROMPT_TIMEOUT.value).until(EC.presence_of_element_located((By.ID, "onetrust-accept-btn-handler")))
            logger.info("Accepting cookies")
            accept_cookies.click()
        except TimeoutException:
            pass

    def _wait_until_connected(self):
        try:
            WebDriverWait(self.driver, DelaySeconds.PAGE_TIMEOUT).until(EC.url_contains("rbc-mfa-app"), message="Was not able to log in user.")
        except TimeoutException as e:
            self._save_screenshot("login_failure")
            raise TimeoutException from e
        logger.info("waiting for 2FA...")
        try:
            WebDriverWait(self.driver, DelaySeconds.TWO_FACTOR_TIMEOUT).until(EC.url_contains("summary"), message="Timeout waiting for 2FA")
        except TimeoutException as e:
            self._save_screenshot("timeout_2fa")
            raise TimeoutException from e
        logger.info("Connected.")

    def download_transactions(self, software: Software, account_info: AccountInfo, include: Include, export_directory: Path | str | None = None) -> Path | None:
        assert self.cookies is not None, "Cookies have not been fetched yet, cannot download transactions. Please fetch cookies first."
        logger.info("Downloading transactions with software %s account info %s including %s", software.name, account_info.name, include.name)
        url = "https://www1.royalbank.com/sgw5/SECOLBH/3m00/ISAMSecureRequest/v1/eBGRenderPage"

        session = requests.Session()
        for cookie in self.cookies:
            session.cookies.set(cookie["name"], cookie["value"])

        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "en-CA,en;q=0.9,fr-CA;q=0.8,fr;q=0.7,en-GB;q=0.6,en-US;q=0.5",
            "cache-control": "max-age=0",
            "content-type": "application/x-www-form-urlencoded",
            "sec-ch-ua": '"Google Chrome";v="137", "Chromium";v="137", "Not/A)Brand";v="24"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "document",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "sec-fetch-user": "?1",
            "upgrade-insecure-requests": "1",
            "referer": "https://www1.royalbank.com/sgw5/SECOLBH/3m00/ISAMSecureRequest/v1/eBGRenderPage"
        }

        data = {
            "F22": "HTPCBINET",
            "REQUEST": "OFXTransactionInquiry",
            "FROM": "Download",
            "DLTYPE": "+",
            "STATEMENT": "P",
            "SOFTWARE": software.value,
            "ACCOUNT_INFO": account_info.value,
            "INCLUDE": include.value,
            "FROMDAY": "1",
            "FROMMONTH": "1",
            "TODAY": "1",
            "TOMONTH": "1"
        }
        if export_directory is None:
            export_directory = Path.cwd().parent / "downloaded_data"
        export_directory = Path(export_directory)
        export_directory.mkdir(parents=True, exist_ok=True)

        response = session.post(url, data=data, headers=headers)
        assert response.status_code == HTTPStatus.OK, f"Failed to download transaction data, status code : {response.status_code}"
        cd = response.headers.get("content-disposition")
        filename = self._get_filename_from_content_disposition(cd)
        if filename is None:
            filename = get_default_filename(software)
        file_path = export_directory / filename
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        logger.info(f"File saved as {file_path}")
        return file_path



    def _get_filename_from_content_disposition(self, content_disposition: str) -> str | None:
        if not content_disposition:
            return None
        match = re.search(r'filename="([^"]+)"', content_disposition)
        if not match:
            return None
        return match.group(1)