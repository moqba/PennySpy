import pytest

from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.bot_detection_checker.dandb_bot_detector import DAndBBotDetector
from pennyspy.scrapers.bot_detection_checker.rebrowser_bot_detector import RebrowserBotDetector
from pennyspy.scrapers.zen_scraper import ZenScraper


@pytest.mark.parametrize("bot_detector_factory", [RebrowserBotDetector, DAndBBotDetector])
def test_bot_detection(bot_detector_factory: type[BotDetectionChecker]):
    bot_detector = bot_detector_factory()  # type: ignore[call-arg]
    try:
        bot_detector.assert_is_not_detected()
    finally:
        # The detectors run on the CDP-native engine, whose browser + event-loop thread must be
        # torn down explicitly; otherwise interpreter-exit cleanup can hang.
        if isinstance(bot_detector, ZenScraper):
            bot_detector.quit()
