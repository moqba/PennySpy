import json
from logging import getLogger
from selenium.webdriver.common.by import By
from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.rbc_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.scrapers import Scraper

logger = getLogger(__name__)

class RebrowserBotDetector(Scraper, BotDetectionChecker):
    URL = r"https://bot-detector.rebrowser.net/"

    def get_test_result(self) -> dict:
        self.driver.get(self.URL)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        result_text_area = self.driver.find_element(By.ID, "detections-json")
        return json.loads(result_text_area.get_attribute("value"))

    def _is_test_skipped(self, test_result: dict) -> bool:
        return test_result["rating"] == 0

    def _is_test_passed(self, test_result: dict) -> bool:
        return test_result["rating"] == -1

    def assert_is_not_detected(self):
        test_results = self.get_test_result()
        failed_tests = []
        for test_result in test_results:
            if self._is_test_skipped(test_result):
                logger.info("Test %s is skipped, note : %s", test_result["type"], test_result["note"])
                continue
            if not self._is_test_passed(test_result):
                failed_tests.append(test_result)
            logger.info("Test %s : %s", test_result["type"], test_result["note"])
        assert not failed_tests, f"Following tests failed : {failed_tests}"