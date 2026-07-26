import json as json_module

import httpx
import pytest
from pydantic import BaseModel

from app.clients.gemini import DailyCallCounter, GeminiClient, GeminiDailyCapExceeded, GeminiStructuredOutputError
from app.storage.redis import UpstashRedis


class Choice(BaseModel):
    headline: str
    confidence: float


class FakeResponse:
    def __init__(self, text: str | None = None, parsed=None):
        self.text = text
        self.parsed = parsed


class FakeModels:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents})
        return self._responses.pop(0)


class FakeRunner:
    def __init__(self, responses: list) -> None:
        self.models = FakeModels(responses)


def _mock_redis(store: dict) -> UpstashRedis:
    def handler(request: httpx.Request) -> httpx.Response:
        cmd = json_module.loads(request.content)
        op = cmd[0]
        if op == "INCR":
            store[cmd[1]] = store.get(cmd[1], 0) + 1
            return httpx.Response(200, json={"result": store[cmd[1]]})
        if op == "EXPIRE":
            return httpx.Response(200, json={"result": 1})
        return httpx.Response(400, json={"error": f"unhandled {op}"})

    return UpstashRedis(url="https://fake.upstash.io", token="tok", client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_generate_structured_uses_parsed_when_available():
    runner = FakeRunner([FakeResponse(parsed=Choice(headline="Great session", confidence=0.9))])
    counter = DailyCallCounter(_mock_redis({}), cap=200)
    client = GeminiClient(runner, counter)

    result = client.generate_structured(model="gemini-3.5-flash", prompt="hi", response_schema=Choice)
    assert result.headline == "Great session"


def test_generate_structured_falls_back_to_text_json():
    payload = json_module.dumps({"headline": "Solid work", "confidence": 0.7})
    runner = FakeRunner([FakeResponse(text=payload)])
    counter = DailyCallCounter(_mock_redis({}), cap=200)
    client = GeminiClient(runner, counter)

    result = client.generate_structured(model="gemini-3.5-flash", prompt="hi", response_schema=Choice)
    assert result.confidence == 0.7


def test_generate_structured_retries_once_on_invalid_json_then_succeeds():
    runner = FakeRunner([
        FakeResponse(text="not valid json at all"),
        FakeResponse(text=json_module.dumps({"headline": "Recovered", "confidence": 0.5})),
    ])
    counter = DailyCallCounter(_mock_redis({}), cap=200)
    client = GeminiClient(runner, counter)

    result = client.generate_structured(model="gemini-3.5-flash", prompt="hi", response_schema=Choice, max_retries=1)
    assert result.headline == "Recovered"
    assert len(runner.models.calls) == 2


def test_generate_structured_raises_after_exhausting_retries():
    runner = FakeRunner([FakeResponse(text="garbage"), FakeResponse(text="still garbage")])
    counter = DailyCallCounter(_mock_redis({}), cap=200)
    client = GeminiClient(runner, counter)

    with pytest.raises(GeminiStructuredOutputError):
        client.generate_structured(model="gemini-3.5-flash", prompt="hi", response_schema=Choice, max_retries=1)


def test_daily_call_cap_blocks_further_calls():
    store: dict = {}
    counter = DailyCallCounter(_mock_redis(store), cap=2)
    runner = FakeRunner([
        FakeResponse(parsed=Choice(headline="a", confidence=0.1)),
        FakeResponse(parsed=Choice(headline="b", confidence=0.1)),
    ])
    client = GeminiClient(runner, counter)

    client.generate_structured(model="m", prompt="1", response_schema=Choice)
    client.generate_structured(model="m", prompt="2", response_schema=Choice)
    with pytest.raises(GeminiDailyCapExceeded):
        client.generate_structured(model="m", prompt="3", response_schema=Choice)
