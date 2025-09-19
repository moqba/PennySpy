import pytest

from pennyspy.scrapers.bot_detection_checker.bot_detection_checker import BotDetectionChecker
from pennyspy.scrapers.bot_detection_checker.dandb_bot_detector import DAndBBotDetector
from pennyspy.scrapers.bot_detection_checker.rebrowser_bot_detector import RebrowserBotDetector

@pytest.mark.parametrize("bot_detector_factory", [RebrowserBotDetector, DAndBBotDetector])
def test_bot_detection(bot_detector_factory: BotDetectionChecker):
    bot_detector = bot_detector_factory()
    bot_detector .assert_is_not_detected()