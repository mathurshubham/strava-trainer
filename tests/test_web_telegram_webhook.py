"""PRD §11.3 security acceptance criteria this exercises directly:
- 'Telegram webhook rejects requests with wrong/absent secret token'
- 'Non-owner chat_id gets one refusal and is then ignored' (tagged for the worker here)
"""
import json

from app.web import telegram_webhook


class FakeQueue:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, destination_url, payload, delay_seconds=None):
        self.published.append((destination_url, payload, delay_seconds))
        return {"messageId": "fake"}


def _post(headers: dict, update: dict, queue=None):
    return telegram_webhook.handle_post(
        headers, json.dumps(update).encode(),
        webhook_secret="correct-secret", owner_chat_id=111,
        queue=queue or FakeQueue(), worker_url="https://app.example.com/api/worker",
    )


def test_missing_secret_header_silently_dropped():
    queue = FakeQueue()
    resp = _post({}, {"message": {"chat": {"id": 111}, "text": "hi"}}, queue=queue)
    assert resp.status == 200
    assert resp.body == b""
    assert queue.published == []


def test_wrong_secret_silently_dropped():
    queue = FakeQueue()
    resp = _post({"X-Telegram-Bot-Api-Secret-Token": "wrong"}, {"message": {"chat": {"id": 111}, "text": "hi"}}, queue=queue)
    assert resp.status == 200
    assert queue.published == []


def test_owner_chat_message_queued():
    queue = FakeQueue()
    resp = _post(
        {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
        {"message": {"chat": {"id": 111}, "text": "how was it?"}},
        queue=queue,
    )
    assert resp.status == 200
    assert len(queue.published) == 1
    assert queue.published[0][1]["kind"] == "telegram_update"


def test_non_owner_chat_tagged_and_not_processed_as_normal_update():
    queue = FakeQueue()
    resp = _post(
        {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
        {"message": {"chat": {"id": 999}, "text": "hi"}},
        queue=queue,
    )
    assert resp.status == 200
    assert queue.published[0][1] == {"kind": "telegram_non_owner", "chat_id": 999}


def test_callback_query_extracts_chat_id():
    queue = FakeQueue()
    resp = _post(
        {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"},
        {"callback_query": {"message": {"chat": {"id": 111}}, "data": "ask"}},
        queue=queue,
    )
    assert resp.status == 200
    assert queue.published[0][1]["kind"] == "telegram_update"


def test_malformed_body_does_not_crash():
    resp = telegram_webhook.handle_post(
        {"X-Telegram-Bot-Api-Secret-Token": "correct-secret"}, b"not json",
        webhook_secret="correct-secret", owner_chat_id=111, queue=FakeQueue(), worker_url="https://app.example.com/api/worker",
    )
    assert resp.status == 200
