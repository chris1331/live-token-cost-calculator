from __future__ import annotations

import base64
from abc import ABC, abstractmethod

import requests

from src.files import TEXT_EXTENSIONS, decode_text, extension, extract_text, mime_type
from src.models import CountResult


class ProviderError(RuntimeError):
    pass


class UnsupportedFormat(ProviderError):
    pass


class ProviderTokenAdapter(ABC):
    provider: str

    def __init__(self, api_key: str, timeout: float = 60.0):
        if not api_key.strip():
            raise ProviderError(f"A {self.provider} API key is required.")
        self.api_key = api_key.strip()
        self.timeout = timeout

    @abstractmethod
    def count_input(self, model: str, prompt: str, filename: str, data: bytes) -> CountResult:
        raise NotImplementedError

    @staticmethod
    def _json(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError(f"Provider returned non-JSON response ({response.status_code}).") from exc
        if not response.ok:
            error = payload.get("error", payload)
            if isinstance(error, dict):
                error = error.get("message", str(error))
            raise ProviderError(f"Provider API error ({response.status_code}): {error}")
        return payload

    @staticmethod
    def _post(*args, provider: str, **kwargs) -> requests.Response:
        try:
            return requests.post(*args, **kwargs)
        except requests.RequestException as exc:
            raise ProviderError(f"{provider} network request failed. Check connectivity and try again.") from exc


class OpenAITokenAdapter(ProviderTokenAdapter):
    provider = "OpenAI"
    endpoint = "https://api.openai.com/v1/responses/input_tokens"

    def _count(self, payload: dict) -> int:
        response = self._post(
            self.endpoint,
            provider=self.provider,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=self.timeout,
        )
        result = self._json(response)
        if not isinstance(result.get("input_tokens"), int):
            raise ProviderError("OpenAI response did not include an integer input_tokens value.")
        return result["input_tokens"]

    def count_input(self, model: str, prompt: str, filename: str, data: bytes) -> CountResult:
        baseline = self._count(
            {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [{"type": "input_text", "text": prompt}],
                    }
                ],
            }
        )
        encoded = base64.b64encode(data).decode("ascii")
        total = self._count(
            {
                "model": model,
                "input": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "input_file",
                                "filename": filename,
                                "file_data": f"data:{mime_type(filename)};base64,{encoded}",
                            },
                            {"type": "input_text", "text": prompt},
                        ],
                    }
                ],
            }
        )
        return CountResult(self.provider, model, total, baseline, max(total - baseline, 0))


class ClaudeTokenAdapter(ProviderTokenAdapter):
    provider = "Claude"
    endpoint = "https://api.anthropic.com/v1/messages/count_tokens"

    def _count(self, model: str, content: list[dict]) -> int:
        response = self._post(
            self.endpoint,
            provider=self.provider,
            headers={
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={"model": model, "messages": [{"role": "user", "content": content}]},
            timeout=self.timeout,
        )
        result = self._json(response)
        if not isinstance(result.get("input_tokens"), int):
            raise ProviderError("Claude response did not include an integer input_tokens value.")
        return result["input_tokens"]

    def count_input(self, model: str, prompt: str, filename: str, data: bytes) -> CountResult:
        ext = extension(filename)
        baseline = self._count(model, [{"type": "text", "text": prompt}])
        if ext == ".pdf":
            file_part = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(data).decode("ascii"),
                },
            }
        elif ext in TEXT_EXTENSIONS:
            file_part = {"type": "text", "text": decode_text(data)}
        else:
            raise UnsupportedFormat(
                "Claude document blocks do not support this binary Office format; compare its TXT/CSV conversion instead."
            )
        total = self._count(model, [file_part, {"type": "text", "text": prompt}])
        return CountResult(self.provider, model, total, baseline, max(total - baseline, 0))


class GeminiTokenAdapter(ProviderTokenAdapter):
    provider = "Gemini"
    endpoint = "https://generativelanguage.googleapis.com/v1beta/models/{model}:countTokens"

    def _count(self, model: str, parts: list[dict]) -> tuple[int, dict[str, int]]:
        response = self._post(
            self.endpoint.format(model=model),
            provider=self.provider,
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            json={"contents": [{"role": "user", "parts": parts}]},
            timeout=self.timeout,
        )
        result = self._json(response)
        total = result.get("totalTokens")
        if not isinstance(total, int):
            raise ProviderError("Gemini response did not include an integer totalTokens value.")
        breakdown: dict[str, int] = {}
        for item in result.get("promptTokensDetails", []) or []:
            modality = str(item.get("modality", "unspecified")).lower()
            count = item.get("tokenCount")
            if isinstance(count, int):
                breakdown[modality] = breakdown.get(modality, 0) + count
        return total, breakdown

    def count_input(self, model: str, prompt: str, filename: str, data: bytes) -> CountResult:
        baseline, _ = self._count(model, [{"text": prompt}])
        ext = extension(filename)
        warning = ""
        if ext == ".pdf":
            file_part = {
                "inlineData": {
                    "mimeType": "application/pdf",
                    "data": base64.b64encode(data).decode("ascii"),
                }
            }
        elif ext in TEXT_EXTENSIONS:
            file_part = {"text": decode_text(data)}
        elif ext in {".pptx", ".xlsx"}:
            text, _ = extract_text(filename, data)
            file_part = {"text": text}
            warning = "Gemini counted locally extracted text because native visual document understanding is PDF-only."
        else:
            raise UnsupportedFormat("Gemini cannot count this file format in the configured workflow.")
        total, breakdown = self._count(model, [file_part, {"text": prompt}])
        return CountResult(
            self.provider,
            model,
            total,
            baseline,
            max(total - baseline, 0),
            modality_breakdown=breakdown,
            warning=warning,
        )


ADAPTERS = {
    "OpenAI": OpenAITokenAdapter,
    "Claude": ClaudeTokenAdapter,
    "Gemini": GeminiTokenAdapter,
}
