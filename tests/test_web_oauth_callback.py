import httpx

from app.clients.strava import StravaTokens
from app.web import oauth_callback


class FakeTokenStore:
    def __init__(self) -> None:
        self.saved: StravaTokens | None = None

    def save(self, tokens: StravaTokens) -> None:
        self.saved = tokens


def _http(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_denied_authorization_returns_friendly_message():
    resp = oauth_callback.handle_get({"error": "access_denied"}, consume_state=lambda s: True, client_id="c", client_secret="s", token_store=FakeTokenStore())
    assert resp.status == 200
    assert b"denied" in resp.body


def test_missing_code_or_state_is_400():
    resp = oauth_callback.handle_get({}, consume_state=lambda s: True, client_id="c", client_secret="s", token_store=FakeTokenStore())
    assert resp.status == 400


def test_invalid_state_rejected_without_exchanging_code():
    store = FakeTokenStore()
    resp = oauth_callback.handle_get(
        {"code": "abc", "state": "replayed"}, consume_state=lambda s: False,
        client_id="c", client_secret="s", token_store=store,
    )
    assert resp.status == 400
    assert store.saved is None


def test_valid_callback_exchanges_code_and_saves_tokens():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a1", "refresh_token": "r1", "expires_at": 1234567890})

    store = FakeTokenStore()
    resp = oauth_callback.handle_get(
        {"code": "abc", "state": "valid-once"}, consume_state=lambda s: True,
        client_id="c", client_secret="s", token_store=store, http_client=_http(handler),
    )
    assert resp.status == 200
    assert store.saved.access_token == "a1"
    assert store.saved.refresh_token == "r1"
