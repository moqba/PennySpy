"""Calling Wealthsimple's GraphQL API with the web app's own credentials.

Why not just send the request from Python
-----------------------------------------
``FetchAccountGraphData`` is an authenticated call: it needs whatever credentials the web app
stamps on its requests — a bearer token, the ``x-ws-*`` client headers, a session cookie, or
some combination that changes between deploys. None of it sits anywhere Python can reliably
read, and chasing it through whatever the current bundle happens to store it in is exactly
the kind of breakage this codebase keeps hitting.

Watch the browser, not the page
-------------------------------
So the credentials are taken from a request the app already made. They are recorded by the
browser itself, through CDP ``Network.requestWillBeSent``
(:class:`~pennyspy.scrapers.zen_scraper.RequestLog`), rather than by a script injected into
the page that wraps ``window.fetch``.

That distinction is the whole point. An injected wrapper only sees requests that go through
the exact function it wrapped, in the world it was injected into, and only if it ran before
the page's own scripts. A request issued over XMLHttpRequest, from a worker, or by a bundle
that kept its own reference to ``fetch`` is invisible to it, and so is every request on a page
where the injection never landed — a case indistinguishable, from Python, from the user being
signed out. The browser's own view has none of those blind spots.

Choosing and replaying
----------------------
:func:`select_graph_headers` picks which recorded request to borrow from, in plain Python so
it can be tested without a browser. The replay itself still happens *in the page*
(:func:`build_graph_fetch_expression`), because that is what supplies the cookies and the
right origin.

The recorded headers are credentials. They pass through this module and into a script
argument; nothing here logs them, and callers describe them by name only.
"""

from __future__ import annotations

import json
import re
from typing import Any, Final

from pennyspy.scrapers.wealthsimple.account_earnings import (
    GRAPH_OPERATION_NAME,
    WEALTHSIMPLE_GRAPHQL,
)

# Only the headers a page is allowed to set on a request it makes itself. The browser
# refuses the rest (Cookie, Origin, Referer, sec-*, User-Agent) — and supplies them itself —
# so replaying them would just make the fetch throw. Cookies still ride along: the replay
# sends ``credentials: 'include'``.
REPLAYABLE_HEADERS: Final[re.Pattern[str]] = re.compile(
    r"^(authorization|content-type|accept|accept-language|x-platform-os|x-ws-[a-z0-9-]+)$"
)

OPERATION_NAME_HEADER: Final[str] = "x-ws-operation-name"
OPERATION_HASH_HEADER: Final[str] = "x-ws-operation-hash"

# The GraphQL endpoint's path, as it appears in every request to it whether the app wrote the
# URL absolute or relative.
GRAPHQL_PATH: Final[str] = "/graphql"


def replayable_headers(headers: dict[str, str]) -> dict[str, str]:
    """``headers`` reduced to the ones a page script is allowed to set itself."""
    return {name: value for name, value in headers.items() if REPLAYABLE_HEADERS.match(name)}


def select_graph_headers(captures: list[dict[str, str]]) -> tuple[dict[str, str], bool] | None:
    """Pick the recorded request to borrow credentials from, and say whether it was the graph one.

    Best first, most recent within each tier:

    1. ``FetchAccountGraphData`` itself — the only capture whose ``x-ws-operation-hash``
       describes the document being replayed, so that hash is worth keeping
    2. any request carrying an ``authorization`` header — same credentials, wrong operation
    3. any request to the endpoint at all — WS may authenticate this session by cookie, which
       the replay sends regardless, and a capture with no ``authorization`` header is
       therefore still worth trying

    Returns ``(headers, is_graph_operation)``, or None when nothing was recorded.
    """
    usable = [replayable_headers(headers) for headers in captures]

    for candidate in reversed(usable):
        if candidate.get(OPERATION_NAME_HEADER) == GRAPH_OPERATION_NAME and candidate.get("authorization"):
            return candidate, True
    for candidate in reversed(usable):
        if candidate.get("authorization"):
            return candidate, False
    for candidate in reversed(usable):
        if candidate:
            return candidate, False
    return None


def graph_request_headers(headers: dict[str, str], *, is_graph_operation: bool) -> dict[str, str]:
    """The header set to replay with, given the capture chosen by :func:`select_graph_headers`.

    Unless the capture was the graph operation itself, ``x-ws-operation-hash`` is dropped: the
    hash identifies the operation document, and sending another operation's hash is worse than
    sending none. The full query travels in the body either way, so the server does not need it.
    """
    replay = dict(headers)
    replay["content-type"] = "application/json"
    replay[OPERATION_NAME_HEADER] = GRAPH_OPERATION_NAME
    if not is_graph_operation:
        replay.pop(OPERATION_HASH_HEADER, None)
    return replay


def build_graph_fetch_expression(body: dict[str, Any], *, headers: dict[str, str], timeout_seconds: int) -> str:
    """A JavaScript expression that POSTs ``body`` to the WS GraphQL API and resolves.

    It always resolves, never rejects, so a failure arrives as a value to inspect rather
    than as an evaluation error with nothing in it. The shape is
    ``{ok, status, payload, error, body}``: ``payload`` is the decoded JSON when there was
    any, and ``body`` a short excerpt of whatever came back instead when there was not.
    """
    return f"""
(async () => {{
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), {timeout_seconds * 1000});
  try {{
    const response = await fetch({json.dumps(WEALTHSIMPLE_GRAPHQL)}, {{
      method: 'POST',
      headers: {json.dumps(headers)},
      body: {json.dumps(json.dumps(body))},
      credentials: 'include',
      mode: 'cors',
      signal: controller.signal,
    }});
    const text = await response.text();
    let payload = null;
    try {{ payload = JSON.parse(text); }} catch (e) {{ payload = null; }}
    return {{
      ok: response.ok,
      status: response.status,
      payload,
      body: payload ? null : String(text).slice(0, 500),
    }};
  }} catch (e) {{
    return {{ ok: false, error: String((e && e.message) || e) }};
  }} finally {{
    clearTimeout(timer);
  }}
}})()
"""
