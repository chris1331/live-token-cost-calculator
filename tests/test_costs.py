import pytest

from src.models import CountResult, PricingRate, calculate_cost


def test_cost_formula_and_long_context_tier():
    price = PricingRate(
        provider="Gemini",
        model_id="model",
        display_name="Model",
        input_per_million=1.0,
        output_per_million=4.0,
        long_input_per_million=2.0,
        long_output_per_million=6.0,
        long_context_threshold=200_000,
    )
    count = CountResult("Gemini", "model", 250_000, 10, 249_990)
    result = calculate_cost(
        pair="PDF vs TXT",
        side="PDF",
        filename="x.pdf",
        count=count,
        price=price,
        expected_output_tokens=1_000,
        repetitions=3,
    )
    assert result.applied_tier == "long context"
    assert result.file_input_cost == pytest.approx(0.49998)
    assert result.complete_input_cost == pytest.approx(0.5)
    assert result.output_cost == pytest.approx(0.006)
    assert result.scenario_total == pytest.approx(1.518)


def test_export_row_has_provenance_and_no_secret_fields():
    price = PricingRate(
        provider="OpenAI",
        model_id="gpt-test",
        display_name="Test",
        input_per_million=1,
        output_per_million=2,
        source_url="https://developers.openai.com/api/docs/pricing",
        retrieved_at="2026-08-17",
        status="live",
    )
    count = CountResult("OpenAI", "gpt-test", 100, 10, 90)
    row = calculate_cost(
        pair="A",
        side="B",
        filename="safe.txt",
        count=count,
        price=price,
        expected_output_tokens=10,
        repetitions=1,
    ).to_row()
    assert row["Pricing status"] == "live"
    assert row["Pricing source"].startswith("https://developers.openai.com")
    assert all("key" not in name.lower() and "content" not in name.lower() for name in row)

