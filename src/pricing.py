from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup, Tag

from src.models import PricingRate, utc_now_iso


OPENAI_URL = "https://developers.openai.com/api/docs/pricing"
CLAUDE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"
GEMINI_URL = "https://ai.google.dev/gemini-api/docs/pricing"
ALLOWED_HOSTS = {
    "developers.openai.com",
    "platform.claude.com",
    "ai.google.dev",
}
FALLBACK_PATH = Path(__file__).resolve().parents[1] / "data" / "fallback_pricing.json"


class PricingError(RuntimeError):
    pass


def _money(value: str) -> float | None:
    match = re.search(r"\$\s*([0-9]+(?:\.[0-9]+)?)", value.replace(",", ""))
    return float(match.group(1)) if match else None


def _effective_money_values(value: str) -> list[float]:
    """Return current effective values, preserving multi-value context tiers."""
    values = [float(item) for item in re.findall(r"\$\s*([0-9]+(?:\.[0-9]+)?)", value)]
    start_match = re.search(
        r"starting\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", value, re.IGNORECASE
    )
    if start_match and len(values) >= 2:
        starts = datetime.strptime(start_match.group(1), "%B %d, %Y").date()
        return [values[1] if date.today() >= starts else values[0]]
    return values


def _table_rows(body: str) -> list[list[str]]:
    soup = BeautifulSoup(body, "html.parser")
    rows: list[list[str]] = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(cells)
    if rows:
        return rows
    for line in body.splitlines():
        if "|" in line and not re.fullmatch(r"[\s|:-]+", line):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) >= 2:
                rows.append(cells)
    return rows


def load_fallback_rates() -> dict[str, PricingRate]:
    payload = json.loads(FALLBACK_PATH.read_text(encoding="utf-8"))
    verified = payload["verified_at"]
    rates: dict[str, PricingRate] = {}
    for item in payload["rates"]:
        rates[item["model_id"]] = PricingRate(
            provider=item["provider"],
            model_id=item["model_id"],
            display_name=item["display_name"],
            input_per_million=float(item["input"]),
            output_per_million=float(item["output"]),
            cached_input_per_million=item.get("cached"),
            long_input_per_million=item.get("long_input"),
            long_output_per_million=item.get("long_output"),
            long_context_threshold=item.get("long_threshold"),
            source_url=item["source"],
            retrieved_at=verified,
            status="fallback",
            warning=f"Fallback price—not live (verified {verified}).",
        )
    return rates


class ProviderPricingAdapter(ABC):
    provider: str
    url: str

    def __init__(self, fallback_rates: dict[str, PricingRate], timeout: float = 15.0):
        self.fallback_rates = {
            key: value for key, value in fallback_rates.items() if value.provider == self.provider
        }
        self.timeout = timeout

    def fetch_prices(self) -> tuple[dict[str, PricingRate], str | None]:
        parsed = urlparse(self.url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
            raise PricingError(f"Refusing non-official pricing URL: {self.url}")
        try:
            response = requests.get(
                self.url,
                timeout=self.timeout,
                headers={"User-Agent": "LiveTokenCostCalculator/1.0"},
            )
            response.raise_for_status()
            rates = self.parse(response.text, utc_now_iso())
            missing = set(self.fallback_rates) - set(rates)
            if missing:
                raise PricingError(f"Official page did not contain validated rows for: {', '.join(sorted(missing))}")
            return rates, None
        except Exception as exc:
            warning = f"{self.provider} live pricing unavailable: {exc}"
            return self.fallback_rates, warning

    @abstractmethod
    def parse(self, body: str, retrieved_at: str) -> dict[str, PricingRate]:
        raise NotImplementedError

    def _live(self, base: PricingRate, **updates: object) -> PricingRate:
        return replace(
            base,
            retrieved_at=str(updates.pop("retrieved_at")),
            status="live",
            warning="",
            **updates,
        )


class OpenAIPricingAdapter(ProviderPricingAdapter):
    provider = "OpenAI"
    url = OPENAI_URL

    def parse(self, body: str, retrieved_at: str) -> dict[str, PricingRate]:
        result: dict[str, PricingRate] = {}
        # The first matching flagship table on the official page is the Standard tier.
        for cells in _table_rows(body):
            model = cells[0].strip().lower()
            if model not in self.fallback_rates or model in result:
                continue
            values = [_money(cell) for cell in cells[1:]]
            if len(values) < 4 or any(value is None for value in values[:4]):
                continue
            input_rate, cached_rate, _, output_rate = values[:4]
            long_input = long_output = None
            if len(values) >= 8 and values[4] is not None and values[7] is not None:
                long_input, long_output = values[4], values[7]
            if not (0 < input_rate < 1000 and 0 < output_rate < 1000):
                continue
            result[model] = self._live(
                self.fallback_rates[model],
                input_per_million=input_rate,
                output_per_million=output_rate,
                cached_input_per_million=cached_rate,
                long_input_per_million=long_input,
                long_output_per_million=long_output,
                retrieved_at=retrieved_at,
            )
        return result


class ClaudePricingAdapter(ProviderPricingAdapter):
    provider = "Claude"
    url = CLAUDE_URL

    def parse(self, body: str, retrieved_at: str) -> dict[str, PricingRate]:
        by_name = {rate.display_name.lower(): rate for rate in self.fallback_rates.values()}
        result: dict[str, PricingRate] = {}
        for cells in _table_rows(body):
            name = re.sub(r"\s*\([^)]*\)\s*", "", cells[0]).strip().lower()
            base = by_name.get(name)
            if base is None or base.model_id in result or len(cells) < 6:
                continue
            input_rate = _money(cells[1])
            cached_rate = _money(cells[4])
            output_rate = _money(cells[5])
            if input_rate is None or output_rate is None:
                continue
            result[base.model_id] = self._live(
                base,
                input_per_million=input_rate,
                output_per_million=output_rate,
                cached_input_per_million=cached_rate,
                retrieved_at=retrieved_at,
            )
        return result


class GeminiPricingAdapter(ProviderPricingAdapter):
    provider = "Gemini"
    url = GEMINI_URL

    @staticmethod
    def _standard_table_for_model(soup: BeautifulSoup, model_id: str) -> Tag | None:
        code = next((tag for tag in soup.find_all("code") if tag.get_text(strip=True) == model_id), None)
        if code is None:
            return None
        heading = code.find_previous("h2")
        if heading is None:
            return None
        cursor = heading.find_next()
        while cursor is not None:
            if cursor.name == "h2":
                break
            if cursor.name == "h3" and cursor.get_text(" ", strip=True).lower() == "standard":
                return cursor.find_next("table")
            cursor = cursor.find_next()
        return None

    def parse(self, body: str, retrieved_at: str) -> dict[str, PricingRate]:
        soup = BeautifulSoup(body, "html.parser")
        result: dict[str, PricingRate] = {}
        for model_id, base in self.fallback_rates.items():
            table = self._standard_table_for_model(soup, model_id)
            if table is None:
                # Useful for compact saved fixtures and Markdown responses.
                section_match = re.search(
                    rf"`?{re.escape(model_id)}`?(.*?)(?=\n##\s|\Z)", body, re.S | re.I
                )
                section = section_match.group(1) if section_match else ""
                input_match = re.search(r"Input price.*?\$([0-9.]+)", section, re.S | re.I)
                output_match = re.search(r"Output price.*?\$([0-9.]+)", section, re.S | re.I)
                if not input_match or not output_match:
                    continue
                input_values = [float(value) for value in re.findall(r"\$([0-9.]+)", input_match.group(0))]
                output_values = [float(value) for value in re.findall(r"\$([0-9.]+)", output_match.group(0))]
                input_rate, output_rate = input_values[0], output_values[0]
                long_input = input_values[1] if len(input_values) > 1 else base.long_input_per_million
                long_output = output_values[1] if len(output_values) > 1 else base.long_output_per_million
            else:
                row_map: dict[str, str] = {}
                for tr in table.find_all("tr"):
                    cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
                    if len(cells) >= 2:
                        row_map[cells[0].lower()] = cells[-1]
                input_text = next((v for k, v in row_map.items() if k.startswith("input price")), "")
                output_text = next((v for k, v in row_map.items() if k.startswith("output price")), "")
                input_values = _effective_money_values(input_text)
                output_values = _effective_money_values(output_text)
                if not input_values or not output_values:
                    continue
                input_rate, output_rate = input_values[0], output_values[0]
                long_input = input_values[1] if "> 200k" in input_text and len(input_values) > 1 else None
                long_output = output_values[1] if "> 200k" in output_text and len(output_values) > 1 else None
            result[model_id] = self._live(
                base,
                input_per_million=input_rate,
                output_per_million=output_rate,
                long_input_per_million=long_input,
                long_output_per_million=long_output,
                long_context_threshold=200_000 if long_input is not None else None,
                retrieved_at=retrieved_at,
            )
        return result


def fetch_all_prices() -> tuple[dict[str, PricingRate], list[str]]:
    fallback = load_fallback_rates()
    rates: dict[str, PricingRate] = {}
    warnings: list[str] = []
    for adapter_cls in (OpenAIPricingAdapter, ClaudePricingAdapter, GeminiPricingAdapter):
        fetched, warning = adapter_cls(fallback).fetch_prices()
        rates.update(fetched)
        if warning:
            warnings.append(warning)
    return rates, warnings
