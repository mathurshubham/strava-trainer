import contextlib
import time

import httpx
import pytest

from app.clients.strava import StravaClient, StravaRateLimitExceeded, StravaTokens


class InMemoryTokenStore:
    def __init__(self, tokens: StravaTokens) -> None:
        self.tokens = tokens
        self.save_calls: list[StravaTokens] = []

    def get(self) -> StravaTokens:
        return self.tokens

    def save(self, tokens: StravaTokens) -> None:
        self.tokens = tokens
        self.save_calls.append(tokens)


class CountingLock:
    def __init__(self) -> None:
        self.acquire_count = 0

    def __call__(self):
        self.acquire_count += 1
        return contextlib.nullcontext()


def _make_client(token_store, lock, requests_log):
    def handler(request: httpx.Request) -> httpx.Response:
        requests_log.append(request)
        if request.url.path == "/oauth/token":
            return httpx.Response(
                200,
                json={"access_token": "refreshed-access", "refresh_token": "refreshed-refresh", "expires_at": int(time.time()) + 21600},
            )
        if request.url.path == "/api/v3/athlete":
            return httpx.Response(200, json={"id": 555}, headers={"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "85,900"})
        if request.url.path.endswith("/streams"):
            return httpx.Response(200, json={"heartrate": {"data": [100, 110]}}, headers={"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "10,50"})
        if request.url.path == "/api/v3/athlete/zones":
            return httpx.Response(200, json={"heart_rate": {}}, headers={"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "10,50"})
        return httpx.Response(404, json={"message": "not found"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = StravaClient(
        client_id="cid",
        client_secret="csecret",
        api_base="https://www.strava.com/api/v3",
        token_store=token_store,
        lock_factory=lock,
        http_client=http_client,
    )
    return client


def test_fresh_token_skips_refresh():
    store = InMemoryTokenStore(StravaTokens(access_token="still-good", refresh_token="r1", expires_at=int(time.time()) + 3600))
    lock = CountingLock()
    log: list[httpx.Request] = []
    client = _make_client(store, lock, log)

    client.get_athlete()

    assert lock.acquire_count == 0
    assert store.save_calls == []
    auth_header = log[0].headers["authorization"]
    assert auth_header == "Bearer still-good"


def test_near_expiry_token_triggers_refresh_under_lock():
    store = InMemoryTokenStore(StravaTokens(access_token="stale", refresh_token="r1", expires_at=int(time.time()) + 60))
    lock = CountingLock()
    log: list[httpx.Request] = []
    client = _make_client(store, lock, log)

    client.get_athlete()

    assert lock.acquire_count == 1
    assert store.save_calls[-1].access_token == "refreshed-access"
    assert store.save_calls[-1].refresh_token == "refreshed-refresh"
    # the actual API call must use the newly refreshed token
    api_call = [r for r in log if r.url.path == "/api/v3/athlete"][0]
    assert api_call.headers["authorization"] == "Bearer refreshed-access"


def test_rate_limit_headers_are_parsed():
    store = InMemoryTokenStore(StravaTokens(access_token="ok", refresh_token="r1", expires_at=int(time.time()) + 3600))
    client = _make_client(store, CountingLock(), [])

    client.get_athlete()

    assert client.rate_limit.limit_15min == 100
    assert client.rate_limit.usage_15min == 85
    assert client.rate_limit.limit_daily == 1000
    assert client.rate_limit.usage_daily == 900
    assert client.rate_limit.over_threshold() is True  # 85/100 = 85% > 80%


def test_non_essential_call_refused_when_over_threshold():
    store = InMemoryTokenStore(StravaTokens(access_token="ok", refresh_token="r1", expires_at=int(time.time()) + 3600))
    client = _make_client(store, CountingLock(), [])

    client.get_athlete()  # pushes usage to 85%
    with pytest.raises(StravaRateLimitExceeded):
        client.get_athlete_zones()


def test_streams_endpoint_builds_expected_params():
    store = InMemoryTokenStore(StravaTokens(access_token="ok", refresh_token="r1", expires_at=int(time.time()) + 3600))
    log: list[httpx.Request] = []
    client = _make_client(store, CountingLock(), log)

    client.get_activity_streams(123, ["time", "heartrate"], resolution="medium")

    req = [r for r in log if r.url.path.endswith("/streams")][0]
    assert req.url.params["keys"] == "time,heartrate"
    assert req.url.params["key_by_type"] == "true"
    assert req.url.params["resolution"] == "medium"


def test_refresh_keeps_old_refresh_token_when_none_returned():
    # simulate Strava NOT rotating the refresh token
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "new-access", "expires_at": int(time.time()) + 21600})
        return httpx.Response(200, json={}, headers={"X-RateLimit-Limit": "100,1000", "X-RateLimit-Usage": "1,1"})

    store = InMemoryTokenStore(StravaTokens(access_token="stale", refresh_token="original-refresh", expires_at=int(time.time()) + 10))
    client = StravaClient(
        client_id="cid", client_secret="csecret", api_base="https://www.strava.com/api/v3",
        token_store=store, lock_factory=CountingLock(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    client.get_athlete()
    assert store.tokens.refresh_token == "original-refresh"
