"""Offline tests for the message that tells the user where NBC sent the code."""

from __future__ import annotations

import pytest

from pennyspy.scrapers.nbc.otp_notice import FALLBACK, describe_otp, otp_channel, otp_destination

EMAIL_SENTENCE = "A verification code has been sent to you by email to confirm your identity."


@pytest.mark.parametrize(
    ("sentence", "channel"),
    [
        (EMAIL_SENTENCE, "by email"),
        ("A verification code has been sent to you by text message to confirm your identity.", "by text message"),
        ("A verification code has been sent to you by SMS.", "by text message"),
        ("You will receive a phone call with your verification code.", "by phone call"),
        ("Un code de vérification vous a été envoyé par courriel.", "by email"),
        ("A verification code has been sent to you.", None),
    ],
)
def test_otp_channel(sentence: str, channel: str | None) -> None:
    assert otp_channel(sentence) == channel


@pytest.mark.parametrize(
    ("alert", "destination"),
    [
        ("Code sent to\nj...e@example.com", "j...e@example.com"),
        ("Code sent to ***-***-1234", "***-***-1234"),
        ("Code envoyé à j...e@example.com", "j...e@example.com"),
        ("Code sent to", None),
        ("", None),
    ],
)
def test_otp_destination(alert: str, destination: str | None) -> None:
    assert otp_destination(alert) == destination


def test_describe_otp_names_channel_and_destination() -> None:
    assert describe_otp(EMAIL_SENTENCE, "Code sent to\nj...e@example.com") == (
        "NBC sent a 6-digit code by email to j...e@example.com. Enter it to continue."
    )


def test_describe_otp_works_with_half_the_details() -> None:
    assert (
        describe_otp("", "Code sent to ***-***-1234")
        == "NBC sent a 6-digit code to ***-***-1234. Enter it to continue."
    )
    assert describe_otp(EMAIL_SENTENCE, "") == "NBC sent a 6-digit code by email. Enter it to continue."


def test_describe_otp_falls_back_when_the_page_says_nothing_useful() -> None:
    assert describe_otp("Verification", "") == FALLBACK
