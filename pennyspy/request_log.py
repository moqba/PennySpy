"""Logs whether a scrape response actually reached the client.

The router logs the files it is about to serve, but that line is written while the response
is still being built — it says the export was read off disk, not that the browser received
it. When a scrape succeeds server-side and the web UI still shows an error, that
distinction is the whole question, so it is recorded here: total bytes written and elapsed
time on success, and a warning when the body is cut short or never fully written.

Only the long, single-shot scrape responses are watched; the rest of the API is chatty and
fast, and instrumenting it would bury the one line worth reading.
"""

from __future__ import annotations

import time
from logging import getLogger
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = getLogger(__name__)

WATCHED_PATH_SUFFIXES = ("/scrape",)


class ResponseDeliveryLogger:
    """ASGI middleware recording the delivery of the responses that are expensive to redo."""

    def __init__(self, app: ASGIApp, path_suffixes: tuple[str, ...] = WATCHED_PATH_SUFFIXES) -> None:
        self.app = app
        self.path_suffixes = path_suffixes

    def _watches(self, scope: Scope) -> bool:
        return scope["type"] == "http" and str(scope.get("path", "")).endswith(self.path_suffixes)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self._watches(scope):
            await self.app(scope, receive, send)
            return

        route = f"{scope.get('method', '?')} {scope.get('path', '?')}"
        started = time.monotonic()
        state: dict[str, Any] = {"status": None, "content_type": "", "bytes": 0, "finished": False}
        disconnected = False

        async def watched_receive() -> Message:
            nonlocal disconnected
            message = await receive()
            if message["type"] == "http.disconnect":
                disconnected = True
                logger.warning(
                    "Client disconnected from %s after %.1fs, before the response was written",
                    route,
                    time.monotonic() - started,
                )
            return message

        async def watched_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                state["status"] = message["status"]
                state["content_type"] = _header(message, b"content-type")
                logger.info(
                    "Responding to %s after %.1fs: %s %s",
                    route,
                    time.monotonic() - started,
                    state["status"],
                    state["content_type"] or "(no content-type)",
                )
            elif message["type"] == "http.response.body":
                state["bytes"] += len(message.get("body", b""))
                if not message.get("more_body", False):
                    state["finished"] = True
            await send(message)

        try:
            await self.app(scope, watched_receive, watched_send)
        except Exception:
            # A send that raises here is the connection dying mid-response: the work is done
            # and the payload is gone. Say so explicitly rather than letting it read as an
            # ordinary handler error.
            logger.warning(
                "Delivery of %s failed after %.1fs and %d byte(s) written (status %s)",
                route,
                time.monotonic() - started,
                state["bytes"],
                state["status"],
                exc_info=True,
            )
            raise

        elapsed = time.monotonic() - started
        if state["status"] is None:
            logger.warning("No response was produced for %s after %.1fs", route, elapsed)
        elif not state["finished"] or disconnected:
            # Some servers drop writes to a closed connection silently instead of raising,
            # so an unfinished body is the only trace left of a client that went away.
            logger.warning(
                "Response to %s was not fully written: %s, %d byte(s) after %.1fs (client disconnected: %s)",
                route,
                state["status"],
                state["bytes"],
                elapsed,
                disconnected,
            )
        else:
            logger.info(
                "Delivered %s: %s, %d byte(s) in %.1fs",
                route,
                state["status"],
                state["bytes"],
                elapsed,
            )


def _header(message: Message, name: bytes) -> str:
    for key, value in message.get("headers", []):
        if bytes(key).lower() == name:
            return str(bytes(value).decode("latin-1"))
    return ""
