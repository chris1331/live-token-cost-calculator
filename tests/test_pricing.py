from datetime import date
from pathlib import Path

import pytest

from src.pricing import (
    ClaudePricingAdapter,
    GeminiPricingAdapter,
    OpenAIPricingAdapter,
    load_fallback_rates,
    _effective_money_values,
)


FIXTURES = Path(__file__).parent / "fixtures"


def test_openai_parser_exact_models_and_long_rates():
    adapter = OpenAIPricingAdapter(load_fallback_rates())
    rates = adapter.parse((FIXTURES / "openai_pricing.md").read_text(), "now")
    assert rates["gpt-5.6-sol"].input_per_million == 5
    assert rates["gpt-5.6-sol"].output_per_million == 30
    assert rates["gpt-5.6-sol"].long_input_per_million == 10
    assert rates["gpt-5.6-sol"].status == "live"


def test_claude_parser_uses_base_and_output_columns():
    adapter = ClaudePricingAdapter(load_fallback_rates())
    rates = adapter.parse((FIXTURES / "claude_pricing.md").read_text(), "now")
    assert rates["claude-sonnet-5"].input_per_million == 2
    assert rates["claude-sonnet-5"].cached_input_per_million == pytest.approx(0.2)
    assert rates["claude-sonnet-5"].output_per_million == 10


def test_gemini_parser_selects_standard_table_and_threshold():
    adapter = GeminiPricingAdapter(load_fallback_rates())
    rates = adapter.parse((FIXTURES / "gemini_pricing.html").read_text(), "now")
    pro = rates["gemini-2.5-pro"]
    assert pro.input_per_million == 1.25
    assert pro.output_per_million == 10
    assert pro.long_input_per_million == 2.5
    assert pro.long_output_per_million == 15
    assert pro.long_context_threshold == 200_000


def test_fetch_failure_returns_labeled_fallback(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr("src.pricing.requests.get", fail)
    rates, warning = OpenAIPricingAdapter(load_fallback_rates()).fetch_prices()
    assert warning and "live pricing unavailable" in warning
    assert all(rate.status == "fallback" for rate in rates.values())
    assert all("Fallback price—not live" in rate.warning for rate in rates.values())


def test_dated_price_selects_rate_effective_today():
    values = _effective_money_values(
        "$0.75 through December 31, 2026. $1.50 starting January 1, 2027."
    )
    assert values == [0.75 if date.today() < date(2027, 1, 1) else 1.50]
