import httpx

from app.storage.queue import QStashQueue


def test_publish_sends_bearer_auth_and_json_body():
    log = []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        return httpx.Response(200, json={"messageId": "msg_123"})

    queue = QStashQueue("fake-qstash-token", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = queue.publish("https://app.example.com/api/worker", {"activity_id": 123})

    assert result["messageId"] == "msg_123"
    assert log[0].headers["authorization"] == "Bearer fake-qstash-token"
    assert log[0].url.path == "/v2/publish/https://app.example.com/api/worker"
    assert "delay" not in {k.lower() for k in log[0].headers.keys()} or "upstash-delay" not in log[0].headers


def test_publish_with_delay_sets_header():
    log = []

    def handler(request: httpx.Request) -> httpx.Response:
        log.append(request)
        return httpx.Response(200, json={"messageId": "msg_456"})

    queue = QStashQueue("tok", http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    queue.publish("https://app.example.com/api/worker", {}, delay_seconds=180)

    assert log[0].headers["upstash-delay"] == "180s"
