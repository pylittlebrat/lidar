"""LLMClient 测试（monkeypatch requests.post，不打真实网络）。"""

from __future__ import annotations

import json

import pytest

import techradar.llm.client as client_mod
from techradar.llm.client import LLMClient, LLMError, parse_json_object


class FakeResp:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload
        self.text = json.dumps(payload) if payload else ""

    def json(self):
        return self._payload


def ok_response(content: str) -> FakeResp:
    return FakeResp(200, {"choices": [{"message": {"content": content}}],
                          "usage": {"total_tokens": 10}})


def make_client(**overrides) -> LLMClient:
    defaults = {"api_key": "test-key", "base_url": "https://llm.test/v1", "model": "m"}
    defaults.update(overrides)
    return LLMClient(**defaults)


def test_chat_json_parses_fenced_json(monkeypatch) -> None:
    def fake_post(url, headers=None, json=None, timeout=None):
        return ok_response('```json\n{"summary": "x", "overall_relevance": 90}\n```')

    monkeypatch.setattr(client_mod.requests, "post", fake_post)
    data = make_client().chat_json("s", "u")
    assert data["summary"] == "x"
    assert data["overall_relevance"] == 90
    assert data["_usage"]["total_tokens"] == 10


def test_retry_429_then_success(monkeypatch) -> None:
    seq = [
        FakeResp(429, {"error": "overloaded"}),
        ok_response('{"ok": true}'),
    ]
    monkeypatch.setattr(client_mod.requests, "post", lambda *a, **k: seq.pop(0))
    data = make_client().chat_json("s", "u")
    assert data["ok"] is True


def test_400_no_retry(monkeypatch) -> None:
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(1)
        return FakeResp(400, {"error": "bad key"})

    monkeypatch.setattr(client_mod.requests, "post", fake_post)
    with pytest.raises(LLMError):
        make_client().chat_json("s", "u")
    assert len(calls) == 1  # 4xx 不重试


def test_missing_api_key_raises(monkeypatch) -> None:
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(LLMError):
        LLMClient(api_key="", base_url="https://x/v1")


def test_missing_base_url_raises(monkeypatch) -> None:
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    with pytest.raises(LLMError):
        LLMClient(api_key="k", base_url="")


def test_parse_json_object_variants() -> None:
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('{"b": 2}') == {"b": 2}
    # 从大段文本中提取 {…}
    assert parse_json_object('note: {"c": 3} done') == {"c": 3}
    with pytest.raises(LLMError):
        parse_json_object("not json at all")


@pytest.mark.parametrize("content", ["[]", "null", '"hello"', "42"])
def test_json_array_and_scalar_are_not_objects(content) -> None:
    with pytest.raises(LLMError):
        parse_json_object(content)


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"message": {"content": None}}]}])
def test_malformed_api_response_raises_domain_error(payload, monkeypatch) -> None:
    monkeypatch.setattr(client_mod.requests, "post", lambda *a, **k: FakeResp(200, payload))
    with pytest.raises(LLMError):
        make_client().chat_json("s", "u")


def test_transport_error_is_normalized_after_retry(monkeypatch) -> None:
    import requests
    import importlib
    retry_module = importlib.import_module("techradar.utils.retry")
    monkeypatch.setattr(retry_module.time, "sleep", lambda seconds: None)
    calls = []
    def timeout(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout("private service details")
    monkeypatch.setattr(client_mod.requests, "post", timeout)
    with pytest.raises(LLMError, match="Timeout") as error:
        make_client().chat_json("s", "u")
    assert len(calls) == 3
    assert "private service details" not in str(error.value)
