"""LoadPageClient request shape, retry behaviour and error handling (mocked HTTP, no network)."""

from __future__ import annotations

import asyncio

import httpx
import orjson
import pytest

from app.core.exceptions import ConfigError, LoaderError
from app.fetch.rate_limiter import RateLimiter
from app.fetch.retry import RetryPolicy, parse_retry_after
from app.loader import LoadPageClient

ENDPOINT = "http://load-page.test/"
NO_WAIT = RetryPolicy(max_attempts=3, backoff_seconds=0, max_backoff_seconds=0)


def client_for(handler, retry: RetryPolicy = NO_WAIT, **kwargs) -> LoadPageClient:
    transport = httpx.MockTransport(handler)
    return LoadPageClient(endpoint=ENDPOINT, api_key="secret-key", retry=retry,
                          client=httpx.AsyncClient(transport=transport), **kwargs)


def load(client: LoadPageClient, url: str = "https://example.com/"):
    return asyncio.run(client.load(url))


def test_sends_expected_request_and_returns_page():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="<html><title>Hi</title></html>",
                              headers={"content-type": "text/html", "x-final-url": "https://example.com/home"})

    page = load(client_for(handler))
    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == ENDPOINT
    assert request.headers["X-API-KEY"] == "secret-key"
    assert request.headers["Content-Type"] == "application/json"
    assert orjson.loads(request.content) == {"url": "https://example.com/", "render_js": True, "json_return": False}
    assert page.html.startswith("<html>")
    assert page.final_url == "https://example.com/home"
    assert page.status_code == 200
    assert page.attempts == 1
    assert page.loader == "load-page"
    assert page.render_js is True


def test_retries_transient_errors_then_succeeds():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ReadTimeout("slow", request=request)
        if calls["n"] == 2:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text="<html>ok</html>")

    page = load(client_for(handler))
    assert calls["n"] == 3
    assert page.attempts == 3


def test_gives_up_after_max_attempts():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(LoaderError) as info:
        load(client_for(handler))
    assert calls["n"] == 3
    assert info.value.attempts == 3
    assert info.value.status_code == 502


def test_non_retryable_status_fails_immediately():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, text="bad key")

    with pytest.raises(LoaderError) as info:
        load(client_for(handler))
    assert calls["n"] == 1
    assert info.value.status_code == 401


def test_rate_limited_429_honours_retry_after_and_pauses_limiter():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, text="slow down", headers={"retry-after": "7"})
        return httpx.Response(200, text="<html>ok</html>")

    policy = RetryPolicy(max_attempts=2, backoff_seconds=0, max_backoff_seconds=0)
    assert policy.wait_seconds(1, type("E", (), {"retry_after": 7.0})()) == 0  # capped by max_backoff_seconds
    assert RetryPolicy(backoff_seconds=1, max_backoff_seconds=30, jitter=0).wait_seconds(
        1, type("E", (), {"retry_after": 7.0})()) == 7

    async def run():
        limiter = RateLimiter(1000, 2)
        client = client_for(handler, retry=policy, limiter=limiter)
        page = await client.load("https://example.com/")
        return page, limiter

    page, limiter = asyncio.run(run())
    assert page.attempts == 2
    assert limiter._paused_until > 0


def test_target_status_header_is_respected():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>gone</html>", headers={"x-status-code": "404"})

    with pytest.raises(LoaderError) as info:
        load(client_for(handler))
    assert info.value.status_code == 404


def test_empty_body_is_retried():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text="   " if calls["n"] == 1 else "<html>ok</html>")

    assert load(client_for(handler)).attempts == 2


def test_json_envelope_is_unwrapped():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"html": "<html>wrapped</html>"})

    assert load(client_for(handler)).html == "<html>wrapped</html>"


def test_missing_api_key_is_a_config_error():
    with pytest.raises(ConfigError):
        LoadPageClient(endpoint=ENDPOINT, api_key=None)


def test_backoff_grows_exponentially_and_is_capped():
    policy = RetryPolicy(backoff_seconds=2, max_backoff_seconds=10, jitter=0)
    assert [policy.wait_seconds(n) for n in (1, 2, 3, 4)] == [2, 4, 8, 10]


def test_parse_retry_after():
    assert parse_retry_after("12") == 12
    assert parse_retry_after(None) is None
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0


def test_rate_limiter_spaces_requests():
    now = {"t": 0.0}
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        now["t"] += seconds

    async def run():
        limiter = RateLimiter(2, 5, clock=lambda: now["t"], sleep=fake_sleep)
        for _ in range(3):
            async with limiter:
                pass

    asyncio.run(run())
    assert sleeps == [0.5, 0.5]
