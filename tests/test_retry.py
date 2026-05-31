"""Tests for the shared HTTP retry helper."""

from __future__ import annotations

import httpx
import pytest

from ankery.providers.retry import _parse_retry_after, request_with_retry


def _resp(status: int, headers: dict | None = None) -> httpx.Response:
    return httpx.Response(status, headers=headers or {})


def test_retries_on_429_then_succeeds():
    statuses = [429, 429, 200]
    calls: list[int] = []

    def send() -> httpx.Response:
        status = statuses[len(calls)]
        calls.append(status)
        return _resp(status)

    slept: list[float] = []
    response = request_with_retry(send, sleep=slept.append)
    assert response.status_code == 200
    assert len(calls) == 3
    assert len(slept) == 2  # one wait before each retry


def test_gives_up_after_max_attempts():
    calls: list[int] = []

    def send() -> httpx.Response:
        calls.append(1)
        return _resp(429)

    slept: list[float] = []
    response = request_with_retry(send, max_attempts=3, sleep=slept.append)
    assert response.status_code == 429
    assert len(calls) == 3
    assert len(slept) == 2  # no sleep after the final attempt


def test_non_429_returns_immediately():
    calls: list[int] = []

    def send() -> httpx.Response:
        calls.append(1)
        return _resp(500)

    def boom(_: float) -> None:
        raise AssertionError("should not sleep on a non-429 response")

    response = request_with_retry(send, sleep=boom)
    assert response.status_code == 500
    assert len(calls) == 1


def test_honours_retry_after_seconds():
    statuses = [429, 200]
    calls: list[int] = []

    def send() -> httpx.Response:
        status = statuses[len(calls)]
        calls.append(status)
        headers = {"Retry-After": "5"} if status == 429 else {}
        return _resp(status, headers)

    slept: list[float] = []
    request_with_retry(send, sleep=slept.append)
    assert slept == [5.0]


def test_exponential_backoff_without_header():
    def send() -> httpx.Response:
        return _resp(429)

    slept: list[float] = []
    request_with_retry(send, max_attempts=4, base_delay=1.0, sleep=slept.append)
    assert slept == [1.0, 2.0, 4.0]


def test_retry_after_capped_at_max_delay():
    def send() -> httpx.Response:
        return _resp(429, {"Retry-After": "999"})

    slept: list[float] = []
    request_with_retry(send, max_attempts=2, max_delay=30.0, sleep=slept.append)
    assert slept == [30.0]


@pytest.mark.parametrize(
    "value, expected",
    [
        ("5", 5.0),
        (None, None),
        ("garbage", None),
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_parse_retry_after_past_http_date_is_negative():
    # An HTTP-date already in the past yields a negative delta; the caller clamps.
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") < 0
