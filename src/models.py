from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal


PriceStatus = Literal["live", "fallback"]


@dataclass(frozen=True)
class PricingRate:
    provider: str
    model_id: str
    display_name: str
    input_per_million: float
    output_per_million: float
    cached_input_per_million: float | None = None
    long_input_per_million: float | None = None
    long_output_per_million: float | None = None
    long_context_threshold: int | None = None
    source_url: str = ""
    retrieved_at: str = ""
    status: PriceStatus = "fallback"
    tier: str = "standard"
    unit: str = "USD per 1M tokens"
    warning: str = ""

    def rates_for(self, input_tokens: int) -> tuple[float, float, str]:
        if (
            self.long_context_threshold is not None
            and input_tokens > self.long_context_threshold
            and self.long_input_per_million is not None
            and self.long_output_per_million is not None
        ):
            return self.long_input_per_million, self.long_output_per_million, "long context"
        return self.input_per_million, self.output_per_million, self.tier


@dataclass(frozen=True)
class CountResult:
    provider: str
    model_id: str
    total_input_tokens: int
    prompt_baseline_tokens: int
    incremental_file_tokens: int
    modality_breakdown: dict[str, int] = field(default_factory=dict)
    warning: str = ""


@dataclass(frozen=True)
class FileDiagnostic:
    filename: str
    extension: str
    bytes: int
    characters: int = 0
    words: int = 0
    pages: int | None = None
    slides: int | None = None
    sheets: int | None = None
    rows: int | None = None
    warning: str = ""


@dataclass(frozen=True)
class CostResult:
    pair: str
    side: str
    filename: str
    provider: str
    model_id: str
    count: CountResult
    price: PricingRate
    expected_output_tokens: int
    repetitions: int
    input_rate: float
    output_rate: float
    applied_tier: str
    file_input_cost: float
    complete_input_cost: float
    output_cost: float
    scenario_total: float
    warning: str = ""

    def to_row(self) -> dict[str, Any]:
        row = {
            "Pair": self.pair,
            "Side": self.side,
            "File": self.filename,
            "Provider": self.provider,
            "Model": self.model_id,
            "Input tokens": self.count.total_input_tokens,
            "Prompt baseline": self.count.prompt_baseline_tokens,
            "File tokens": self.count.incremental_file_tokens,
            "Input $/1M": self.input_rate,
            "Output $/1M": self.output_rate,
            "File input cost USD": self.file_input_cost,
            "Complete input cost USD": self.complete_input_cost,
            "Estimated output cost USD": self.output_cost,
            "Repetitions": self.repetitions,
            "Scenario total USD": self.scenario_total,
            "Pricing tier": self.applied_tier,
            "Pricing status": self.price.status,
            "Pricing source": self.price.source_url,
            "Pricing retrieved/verified": self.price.retrieved_at,
            "Pricing unit": self.price.unit,
            "Modality breakdown": str(self.count.modality_breakdown or {}),
            "Warning": self.warning,
        }
        return row


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def calculate_cost(
    *,
    pair: str,
    side: str,
    filename: str,
    count: CountResult,
    price: PricingRate,
    expected_output_tokens: int,
    repetitions: int,
) -> CostResult:
    input_rate, output_rate, applied_tier = price.rates_for(count.total_input_tokens)
    file_input_cost = count.incremental_file_tokens * input_rate / 1_000_000
    complete_input_cost = count.total_input_tokens * input_rate / 1_000_000
    output_cost = expected_output_tokens * output_rate / 1_000_000
    warnings = [value for value in (count.warning, price.warning) if value]
    return CostResult(
        pair=pair,
        side=side,
        filename=filename,
        provider=count.provider,
        model_id=count.model_id,
        count=count,
        price=price,
        expected_output_tokens=expected_output_tokens,
        repetitions=repetitions,
        input_rate=input_rate,
        output_rate=output_rate,
        applied_tier=applied_tier,
        file_input_cost=file_input_cost,
        complete_input_cost=complete_input_cost,
        output_cost=output_cost,
        scenario_total=(complete_input_cost + output_cost) * repetitions,
        warning="; ".join(warnings),
    )
