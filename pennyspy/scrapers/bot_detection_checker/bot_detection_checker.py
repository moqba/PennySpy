from abc import ABC, abstractmethod


class BotDetectionChecker(ABC):
    @abstractmethod
    def assert_is_not_detected(self):
        pass