"""PRD §11.3 security acceptance criteria this exercises directly:
- 'Strava webhook rejects wrong verify_token, wrong subscription_id, wrong owner_id'
- 'Duplicate webhook events processed exactly once'
- POST always returns 200, even on malformed input or an internal error.
"""
import json

from app.web import strava_webhook
from app.web.types import WebResponse


class FakeEventLog:
    def __init__(self) -> None:
        self.claimed: set[str] = set()
        self.calls: list[tuple] = []

    def try_claim(self, *, provider, event_hash, payload) -> bool:
        self.calls.append((provider, event_hash, payload))
        if event_hash in self.claimed:
            return False
        self.claimed.add(event_hash)
        return True


class FakeQueue:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    def publish(self, destination_url, payload, delay_seconds=None):
        self.published.append((destination_url, payload, delay_seconds))
        return {"messageId": "fake"}


def test_get_validation_echoes_challenge_with_correct_token():
    resp = strava_webhook.handle_get({"hub.verify_token": "secret", "hub.challenge": "abc123"}, verify_token="secret")
    assert resp.status == 200
    assert json.loads(resp.body) == {"hub.challenge": "abc123"}


def test_get_validation_rejects_wrong_token():
    resp = strava_webhook.handle_get({"hub.verify_token": "wrong", "hub.challenge": "abc123"}, verify_token="secret")
    assert resp.status == 403


def _post(payload: dict, event_log=None, queue=None):
    return strava_webhook.handle_post(
        json.dumps(payload).encode(),
        expected_subscription_id=555,
        expected_athlete_id=999,
        event_log=event_log or FakeEventLog(),
        queue=queue or FakeQueue(),
        worker_url="https://app.example.com/api/worker",
    )


def test_post_valid_event_queues_and_returns_200():
    queue = FakeQueue()
    resp = _post(
        {"subscription_id": 555, "owner_id": 999, "object_id": 123, "object_type": "activity", "aspect_type": "create", "event_time": 1000},
        queue=queue,
    )
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "queued"
    assert len(queue.published) == 1
    assert queue.published[0][1]["object_id"] == 123


def test_post_wrong_subscription_id_ignored_and_not_queued():
    queue = FakeQueue()
    resp = _post(
        {"subscription_id": 1, "owner_id": 999, "object_id": 123, "object_type": "activity", "aspect_type": "create", "event_time": 1000},
        queue=queue,
    )
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "ignored_not_ours"
    assert queue.published == []


def test_post_wrong_owner_id_ignored_and_not_queued():
    queue = FakeQueue()
    resp = _post(
        {"subscription_id": 555, "owner_id": 1, "object_id": 123, "object_type": "activity", "aspect_type": "create", "event_time": 1000},
        queue=queue,
    )
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "ignored_not_ours"
    assert queue.published == []


def test_post_duplicate_event_processed_exactly_once():
    event_log = FakeEventLog()
    queue = FakeQueue()
    payload = {"subscription_id": 555, "owner_id": 999, "object_id": 123, "object_type": "activity", "aspect_type": "create", "event_time": 1000}

    first = _post(payload, event_log=event_log, queue=queue)
    second = _post(payload, event_log=event_log, queue=queue)

    assert json.loads(first.body)["status"] == "queued"
    assert json.loads(second.body)["status"] == "duplicate"
    assert len(queue.published) == 1  # only queued once despite two POSTs


def test_post_deauthorization_event_queued_specially():
    queue = FakeQueue()
    resp = _post({"subscription_id": 555, "owner_id": 999, "object_type": "athlete", "updates": {"authorized": "false"}}, queue=queue)
    assert resp.status == 200
    assert json.loads(resp.body)["status"] == "deauth_queued"
    assert queue.published[0][1]["kind"] == "strava_deauthorized"


def test_post_malformed_body_still_returns_200():
    resp = strava_webhook.handle_post(
        b"not json at all {{{", expected_subscription_id=555, expected_athlete_id=999,
        event_log=FakeEventLog(), queue=FakeQueue(), worker_url="https://app.example.com/api/worker",
    )
    assert resp.status == 200


def test_post_never_raises_even_if_queue_publish_fails():
    class ExplodingQueue:
        def publish(self, *a, **k):
            raise RuntimeError("QStash is down")

    resp = _post(
        {"subscription_id": 555, "owner_id": 999, "object_id": 123, "object_type": "activity", "aspect_type": "create", "event_time": 1000},
        queue=ExplodingQueue(),
    )
    assert resp.status == 200


def test_unconfigured_subscription_id_logs_an_error_not_a_quiet_ignore(caplog):
    """Regression: with STRAVA_SUBSCRIPTION_ID blank, api/index.py passes -1, so
    every real event failed the identity check and returned 200 'ignored_not_ours'
    — invisible in the logs and never retried by Strava. A misconfiguration must
    be loud; a genuinely foreign event stays quiet."""
    import logging

    queue = FakeQueue()
    event = {"subscription_id": 362712, "owner_id": 999, "object_id": 123,
             "object_type": "activity", "aspect_type": "create", "event_time": 1000}

    with caplog.at_level(logging.INFO):
        resp = strava_webhook.handle_post(
            json.dumps(event).encode(),
            expected_subscription_id=-1,          # the unconfigured sentinel
            expected_athlete_id=999,
            event_log=FakeEventLog(), queue=queue,
            worker_url="https://app.example.com/api/worker",
        )

    assert resp.status == 200
    assert queue.published == []
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "an unconfigured subscription id must log an ERROR"
    assert "STRAVA_SUBSCRIPTION_ID" in errors[0].getMessage()


def test_foreign_subscription_does_not_log_an_error(caplog):
    import logging

    with caplog.at_level(logging.INFO):
        _post({"subscription_id": 1, "owner_id": 999, "object_id": 123,
               "object_type": "activity", "aspect_type": "create", "event_time": 1000})

    assert [r for r in caplog.records if r.levelno >= logging.ERROR] == []
