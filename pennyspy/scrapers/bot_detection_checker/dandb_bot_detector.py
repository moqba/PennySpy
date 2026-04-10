import json
import time
from logging import getLogger

from selenium.webdriver.common.by import By

from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.scraper import Scraper

logger = getLogger(__name__)


class DAndBBotDetector(Scraper, BotDetectionChecker):
    URL = r"https://deviceandbrowserinfo.com/are_you_a_bot/"
    TEST_LOADING_DELAY_SEC = 3

    def get_test_result(self) -> dict:
        self.driver.get(self.URL)
        time.sleep(self.TEST_LOADING_DELAY_SEC)
        result_text = self.driver.find_element(By.ID, "jsonResult")
        raw_result_text = result_text.get_attribute("textContent")
        assert raw_result_text is not None, "Could not read test result text"
        return json.loads(raw_result_text)  # type: ignore[no-any-return]

    def assert_is_not_detected(self):
        test_results = self.get_test_result()
        failed_tests = []
        for test_name, is_detected in test_results["details"].items():
            logger.info("Test %s result is %s", test_name, is_detected)
            if is_detected:
                failed_tests.append(test_name)
        assert not failed_tests, f"Following tests failed : {failed_tests}"
