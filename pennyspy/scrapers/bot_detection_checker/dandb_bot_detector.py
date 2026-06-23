import json
from logging import getLogger
from typing import cast

from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.zen_scraper import By, ZenScraper

logger = getLogger(__name__)


class DAndBBotDetector(ZenScraper, BotDetectionChecker):
    URL = r"https://deviceandbrowserinfo.com/are_you_a_bot/"
    RESULT_TIMEOUT_SEC = 20

    def get_test_result(self) -> dict:
        self._navigate("open Device and Browser Info bot-detection page", self.URL)
        result_element = self._find_element(
            "read Device and Browser Info bot-detection JSON result", By.ID, "jsonResult"
        )

        # The result element is filled asynchronously once the in-page checks finish; poll for
        # valid JSON instead of a fixed sleep, which the fast CDP-native engine can outrun.
        def result_ready(_driver) -> dict | None:
            raw_result_text = result_element.get_attribute("textContent")
            if not raw_result_text or not raw_result_text.strip():
                return None
            try:
                return cast(dict, json.loads(raw_result_text))
            except json.JSONDecodeError:
                return None

        return self._wait_until(
            "populate Device and Browser Info bot-detection JSON result",
            result_ready,
            self.RESULT_TIMEOUT_SEC,
        )

    def assert_is_not_detected(self):
        test_results = self.get_test_result()
        failed_tests = []
        for test_name, is_detected in test_results["details"].items():
            logger.info("Test %s result is %s", test_name, is_detected)
            if is_detected:
                failed_tests.append(test_name)
        assert not failed_tests, f"Following tests failed : {failed_tests}"
