import json

import httpx

from app.storage.redis import UpstashRedis
from app.storage.redis_lock import RedisLock


def _mock_redis(store: dict) -> UpstashRedis:
    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json.loads(request.content)
        op = cmd[0]
        if op == "SET" and "NX" in cmd:
            key, value = cmd[1], cmd[2]
            if key in store:
                return httpx.Response(200, json={"result": None})
            store[key] = value
            return httpx.Response(200, json={"result": "OK"})
        if op == "DEL":
            store.pop(cmd[1], None)
            return httpx.Response(200, json={"result": 1})
        return httpx.Response(400, json={"error": "unhandled"})

    return UpstashRedis(url="https://fake.upstash.io", token="tok", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_lock_acquires_and_releases():
    store: dict = {}
    redis = _mock_redis(store)
    with RedisLock(redis, "lock:test", ttl_seconds=5):
        assert "lock:test" in store
    assert "lock:test" not in store


def test_lock_second_acquirer_waits_then_proceeds_after_release():
    store: dict = {"lock:test": "1"}  # already held
    redis = _mock_redis(store)

    lock = RedisLock(redis, "lock:test", ttl_seconds=5, max_attempts=3, retry_delay_s=0.01)
    # simulate release happening after 2 attempts by having a background "release"
    import threading

    def release_soon():
        import time

        time.sleep(0.015)
        store.pop("lock:test", None)

    threading.Thread(target=release_soon).start()
    with lock:
        assert "lock:test" in store
