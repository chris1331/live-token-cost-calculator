import base64

import pytest

from src.providers import ClaudeTokenAdapter, OpenAITokenAdapter, UnsupportedFormat


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

