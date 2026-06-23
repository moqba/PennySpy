import json
from logging import getLogger

from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.rbc_bank.delay_seconds import DelaySeconds
from pennyspy.scrapers.zen_scraper import By, ZenScraper

logger = getLogger(__name__)


class RebrowserBotDetector(ZenScraper, BotDetectionChecker):
    URL = r"https://bot-detector.rebrowser.net/"

    def get_test_result(self) -> list[dict[str, object]]:
        self._navigate("open Rebrowser bot-detection page", self.URL)
        self.driver.implicitly_wait(DelaySeconds.PAGE_LOADING)
        result_text_area = self._find_element("read Rebrowser bot-detection JSON result", By.ID, "detections-json")

        # The textarea exists immediately but the page fills its JSON asynchronously. The
        # CDP-native engine reads fast enough to beat that population, so poll until the JSON
        # parses to a non-empty list rather than reading once.
        def detections_ready(_driver) -> list[dict[str, object]] | None:
            raw_value = result_text_area.get_attribute("value")
            if not raw_value or not raw_value.strip():
                return None
            try:
                parsed = json.loads(raw_value)
            except json.JSONDecodeError:
                return None
            return parsed or None

        return self._wait_until(
            "populate Rebrowser bot-detection JSON result",
            detections_ready,
            DelaySeconds.PAGE_LOADING,
        )

    def _is_test_skipped(self, test_result: dict[str, object]) -> bool:
        return bool(test_result["rating"] == 0)

    def _is_test_passed(self, test_result: dict[str, object]) -> bool:
        return bool(test_result["rating"] == -1)

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
