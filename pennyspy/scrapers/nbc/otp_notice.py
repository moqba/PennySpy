"""The one-line message that tells the user where NBC sent their code.

NBC sends the code by email, text message or phone call depending on the profile, and says
which on the code page: "A verification code has been sent to you by email to confirm your
identity.", then "Code sent to j...e@example.com". NBC masks the destination itself, so it is
safe to show and to log.
"""

from __future__ import annotations

import re
from typing import Final

FALLBACK: Final[str] = "NBC sent a verification code. Check your email or phone, then enter it here."

# Checked in order; the first keyword found in the page's sentence names the channel.
_CHANNELS: Final[tuple[tuple[tuple[str, ...], str], ...]] = (
    (("email", "e-mail", "courriel"), "by email"),
    (("text message", "sms", "texto"), "by text message"),
    (("phone call", "voice", "call", "appel"), "by phone call"),
    (("notification", "app"), "to the National Bank app"),
)
_CODE_SENT_TO: Final[re.Pattern[str]] = re.compile(r"^\s*(code sent to|code envoy[ée] [àa])\s*:?\s*", re.IGNORECASE)


def otp_channel(sentence: str) -> str | None:
    """``"... sent to you by email ..."`` → ``"by email"``; ``None`` when no channel is named."""
    lowered = sentence.lower()
    for keywords, channel in _CHANNELS:
        if any(re.search(rf"\b{re.escape(keyword)}\b", lowered) for keyword in keywords):
            return channel
    return None


def otp_destination(alert_text: str) -> str | None:
    """``"Code sent to\\nj...e@example.com"`` → ``"j...e@example.com"``."""
    destination = " ".join(_CODE_SENT_TO.sub("", " ".join(alert_text.split())).split())
    return destination or None


def describe_otp(sentence: str, alert_text: str) -> str:
    """The message shown in the web UI while NBC waits for the code."""
    channel = otp_channel(sentence)
    destination = otp_destination(alert_text)
    if channel is None and destination is None:
        return FALLBACK
    message = "NBC sent a 6-digit code"
    if channel:
        message += f" {channel}"
    if destination:
        message += f" to {destination}"
    return f"{message}. Enter it to continue."
