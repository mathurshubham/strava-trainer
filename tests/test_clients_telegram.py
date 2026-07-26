import json as json_module

import httpx
import pytest

from app.clients.telegram import TelegramClient


def _client(handler) -> tuple[TelegramClient, list[httpx.Request]]:
    log: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        log.append(request)
        return handler(request)

    return TelegramClient("fake-token", http_client=httpx.Client(transport=httpx.MockTransport(wrapped))), log


def test_send_message_success():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client, log = _client(handler)
    result = client.send_message(123, "<b>hello</b>")

    assert result["ok"] is True
    body = json_module.loads(log[0].content)
    assert body["parse_mode"] == "HTML"
    assert body["text"] == "<b>hello</b>"


def test_send_message_falls_back_on_400_with_stripped_tags():
    calls = []

    def handler(request):
        body = json_module.loads(request.content)
        calls.append(body)
        if body.get("parse_mode") == "HTML":
            return httpx.Response(200, json={"ok": False, "error_code": 400, "description": "bad entities"})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 2}})

    client, _ = _client(handler)
    result = client.send_message(123, "<b>hello</b> <script>evil</script>")

    assert result["ok"] is True
    assert len(calls) == 2
    assert "parse_mode" not in calls[1]
    assert "<b>" not in calls[1]["text"]
    assert "hello" in calls[1]["text"]


def test_send_rendered_only_attaches_keyboard_to_last_chunk():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    client, log = _client(handler)
    keyboard = {"inline_keyboard": [[{"text": "Ask", "callback_data": "ask"}]]}
    client.send_rendered(123, ["<b>part 1</b>", "<b>part 2</b>"], reply_markup=keyboard)

    bodies = [json_module.loads(r.content) for r in log]
    assert "reply_markup" not in bodies[0]
    assert bodies[1]["reply_markup"] == keyboard


def test_send_photo_sends_multipart_with_caption():
    def handler(request):
        assert request.method == "POST"
        assert b"chart.png" in request.content
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 3}})

    client, log = _client(handler)
    from io import BytesIO

    result = client.send_photo(123, BytesIO(b"\x89PNG fakepngdata"), caption_html="<b>Volume by muscle</b>")
    assert result["ok"] is True
    assert b"multipart/form-data" in log[0].headers.get("content-type", b"").encode() or "multipart" in log[0].headers.get("content-type", "")


def test_get_me_returns_bot_identity():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": {"id": 1, "username": "LiftMateBot"}})

    client, log = _client(handler)
    info = client.get_me()

    assert info["result"]["username"] == "LiftMateBot"
    assert log[0].url.path.endswith("/getMe")


def test_set_webhook_payload():
    def handler(request):
        return httpx.Response(200, json={"ok": True, "result": True})

    client, log = _client(handler)
    client.set_webhook("https://app.example.com/api/webhook/telegram", "secret123", ["message", "callback_query"])

    body = json_module.loads(log[0].content)
    assert body["url"] == "https://app.example.com/api/webhook/telegram"
    assert body["secret_token"] == "secret123"
    assert body["drop_pending_updates"] is True
