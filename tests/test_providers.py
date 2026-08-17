import base64

import pytest

from src.providers import (
    ClaudeTokenAdapter,
    GeminiTokenAdapter,
    OpenAITokenAdapter,
    ProviderError,
    UnsupportedFormat,
)


class FakeResponse:
    def __init__(self, payload, ok=True, status_code=200):
        self.payload = payload
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self.payload


def test_openai_baseline_is_subtracted(monkeypatch):
    responses = iter([FakeResponse({"input_tokens": 11}), FakeResponse({"input_tokens": 111})])
    captured = []

    def post(*args, **kwargs):
        captured.append(kwargs["json"])
        return next(responses)

    monkeypatch.setattr("src.providers.requests.post", post)
    result = OpenAITokenAdapter("secret").count_input("gpt", "Read", "a.txt", b"hello")
    assert result.prompt_baseline_tokens == 11
    assert result.incremental_file_tokens == 100
    file_data = captured[1]["input"][0]["content"][0]["file_data"]
    assert file_data.endswith(base64.b64encode(b"hello").decode())
    assert "secret" not in str(captured)


def test_claude_rejects_binary_office_before_upload(monkeypatch):
    monkeypatch.setattr(
        "src.providers.requests.post", lambda *a, **k: FakeResponse({"input_tokens": 5})
    )
    with pytest.raises(UnsupportedFormat, match="binary Office"):
        ClaudeTokenAdapter("secret").count_input("claude", "Read", "a.xlsx", b"not-a-workbook")


def test_gemini_key_is_sent_in_header_not_url(monkeypatch):
    captured = []

    def post(*args, **kwargs):
        captured.append((args, kwargs))
        return FakeResponse({"totalTokens": 10})

    monkeypatch.setattr("src.providers.requests.post", post)
    GeminiTokenAdapter("gemini-secret").count_input("gemini", "Read", "a.txt", b"hello")
    for args, kwargs in captured:
        assert "gemini-secret" not in args[0]
        assert "params" not in kwargs
        assert kwargs["headers"]["x-goog-api-key"] == "gemini-secret"


def test_network_error_is_redacted(monkeypatch):
    def fail(*args, **kwargs):
        import requests

        raise requests.Timeout("https://example.test/?key=gemini-secret")

    monkeypatch.setattr("src.providers.requests.post", fail)
    with pytest.raises(ProviderError) as error:
        GeminiTokenAdapter("gemini-secret").count_input("gemini", "Read", "a.txt", b"hello")
    assert "gemini-secret" not in str(error.value)
    assert "network request failed" in str(error.value)
