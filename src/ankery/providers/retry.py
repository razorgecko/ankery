"""HTTP retry helper: send a request, retry while the response is HTTP 429.

429 (Too Many Requests) is transient — the server is asking us to back off.
:func:`request_with_retry` retries it transparently, honouring the server's
``Retry-After`` when given and otherwise backing off exponentially, and returns
the final response unchanged so the caller handles every other status itself.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_ATTEMPTS = 4
DEFAULT_BASE_DELAY = 1.0
DEFAULT_MAX_DELAY = 30.0


def request_with_retry(
    send: Callable[[], httpx.Response],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    base_delay: float = DEFAULT_BASE_DELAY,
    max_delay: float = DEFAULT_MAX_DELAY,
    sleep: Callable[[float], None] = time.sleep,
) -> httpx.Response:
    """Send a request, retrying while the response is HTTP 429.

    ``send`` performs one request and returns its :class:`httpx.Response`; it is
    re-invoked for each attempt. Between attempts we wait the server's
    ``Retry-After`` (delta-seconds or an HTTP-date), falling back to exponential
    backoff (``base_delay * 2**attempt``); either way the wait is capped at
    ``max_delay`` to bound an unhelpful server. The last response is returned
    regardless of status, so the caller still handles non-429 codes itself.

    ``sleep`` is injectable so tests need not wait in real time.
    """
    for attempt in range(max_attempts):
        response = send()
        if response.status_code != 429 or attempt == max_attempts - 1:
            return response
        delay = _retry_delay(response, base_delay * 2**attempt, max_delay)
        logger.info("HTTP 429, retrying in %.1fs (attempt %d)", delay, attempt + 1)
        sleep(delay)
    return response  # unreachable: the loop returns on its final attempt


def _retry_delay(response: httpx.Response, fallback: float, max_delay: float) -> float:
    after = _parse_retry_after(response.headers.get("Retry-After"))
    delay = fallback if after is None else after
    return max(0.0, min(delay, max_delay))


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a ``Retry-After`` header (delta-seconds or HTTP-date); None if absent."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (when - datetime.now(timezone.utc)).total_seconds()
