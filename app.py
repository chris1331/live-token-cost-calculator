from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import streamlit as st

from src.files import diagnose
from src.models import calculate_cost
from src.pricing import fetch_all_prices
from src.providers import ADAPTERS, ProviderError, UnsupportedFormat


st.set_page_config(page_title="File Token Cost Calculator", page_icon="🧮", layout="wide")


PAIR_DEFINITIONS = [
    ("PDF vs TXT", "PDF", ["pdf"], "TXT", ["txt"]),
    ("PowerPoint vs TXT", "PowerPoint", ["pptx", "ppt"], "TXT", ["txt"]),
    ("Excel vs CSV", "Excel", ["xlsx", "xls"], "CSV", ["csv"]),
]


def initialize_prices() -> None:
    if "prices" not in st.session_state:
        with st.spinner("Retrieving current prices from official provider sites…"):
            prices, warnings = fetch_all_prices()
        st.session_state.prices = prices
        st.session_state.price_warnings = warnings


def pricing_frame(prices: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Provider": rate.provider,
                "Model": rate.model_id,
                "Input $/1M": rate.input_per_million,
                "Output $/1M": rate.output_per_million,
                "Status": rate.status.upper(),
                "Retrieved / verified": rate.retrieved_at,
                "Source": rate.source_url,
            }
            for rate in prices.values()
        ]
    )


def add_pair_deltas(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return frame
    frame = frame.copy()
    frame["Token difference vs pair"] = pd.NA
    frame["Cost difference vs pair USD"] = pd.NA
    frame["Cost difference vs pair %"] = pd.NA
    for _, indices in frame.groupby(["Pair", "Provider", "Model"]).groups.items():
        group = frame.loc[list(indices)]
        if len(group) != 2:
            continue
        first, second = group.index[0], group.index[1]
        token_delta = int(frame.at[first, "File tokens"]) - int(frame.at[second, "File tokens"])
        cost_delta = float(frame.at[first, "Scenario total USD"]) - float(
            frame.at[second, "Scenario total USD"]
        )
        denominator = float(frame.at[second, "Scenario total USD"])
        percent = cost_delta / denominator * 100 if denominator else None
        frame.at[first, "Token difference vs pair"] = token_delta
        frame.at[second, "Token difference vs pair"] = -token_delta
        frame.at[first, "Cost difference vs pair USD"] = cost_delta
        frame.at[second, "Cost difference vs pair USD"] = -cost_delta
        frame.at[first, "Cost difference vs pair %"] = percent
        if percent is not None and float(frame.at[first, "Scenario total USD"]):
            reverse_denominator = float(frame.at[first, "Scenario total USD"])
            frame.at[second, "Cost difference vs pair %"] = -cost_delta / reverse_denominator * 100
    return frame


initialize_prices()
prices = st.session_state.prices

st.title("Live-Priced File Token Cost Calculator")
st.caption(
    "Provider token counts × official API rates. Prices refresh once when this app session starts."
)

for warning in st.session_state.price_warnings:
    st.warning(warning + " A dated fallback is being used and will remain labeled in results.")

with st.expander("Current provider pricing and provenance", expanded=True):
    st.dataframe(
        pricing_frame(prices),
        use_container_width=True,
        hide_index=True,
        column_config={"Source": st.column_config.LinkColumn("Official source")},
    )

st.subheader("1. Provider access")
key_columns = st.columns(3)
api_keys = {
    "OpenAI": key_columns[0].text_input("OpenAI API key", type="password", key="openai_key"),
    "Claude": key_columns[1].text_input("Claude API key", type="password", key="claude_key"),
    "Gemini": key_columns[2].text_input("Gemini API key", type="password", key="gemini_key"),
}
st.caption("Keys stay in this running browser session and are never written to disk or exported.")

st.subheader("2. Models and scenario")
selection_columns = st.columns(3)
selected_models: dict[str, list[str]] = {}
for column, provider in zip(selection_columns, ("OpenAI", "Claude", "Gemini")):
    options = [model_id for model_id, rate in prices.items() if rate.provider == provider]
    default = options[:1]
    selected_models[provider] = column.multiselect(
        provider,
        options=options,
        default=default,
        format_func=lambda model_id, p=prices: f"{p[model_id].display_name} ({model_id})",
    )

scenario_columns = st.columns([3, 1, 1])
prompt = scenario_columns[0].text_input("Fixed prompt", value="Read and analyze this file.")
expected_output_tokens = scenario_columns[1].number_input(
    "Expected output tokens", min_value=0, value=500, step=50
)
repetitions = scenario_columns[2].number_input("Requests", min_value=1, value=1, step=1)

st.subheader("3. Upload comparison pairs")
uploaded: list[tuple[str, str, object]] = []
diagnostic_rows: list[dict] = []
for pair_name, left_label, left_types, right_label, right_types in PAIR_DEFINITIONS:
    st.markdown(f"**{pair_name}**")
    left_column, right_column = st.columns(2)
    left_file = left_column.file_uploader(
        left_label, type=left_types, key=f"{pair_name}-left", label_visibility="collapsed"
    )
    right_file = right_column.file_uploader(
        right_label, type=right_types, key=f"{pair_name}-right", label_visibility="collapsed"
    )
    for side, file in ((left_label, left_file), (right_label, right_file)):
        if file is not None:
            data = file.getvalue()
            uploaded.append((pair_name, side, file))
            diagnostic = asdict(diagnose(file.name, data))
            diagnostic.update({"pair": pair_name, "side": side})
            diagnostic_rows.append(diagnostic)

if diagnostic_rows:
    with st.expander("Local file diagnostics", expanded=False):
        st.dataframe(pd.DataFrame(diagnostic_rows), use_container_width=True, hide_index=True)

st.info(
    "Clicking Calculate sends each file only to the selected providers whose API keys are supplied. "
    "Claude will explicitly skip unsupported PowerPoint and Excel originals."
)

if st.button("Calculate provider costs", type="primary", disabled=not uploaded):
    result_rows: list[dict] = []
    error_rows: list[dict] = []
    jobs = sum(len(models) for models in selected_models.values()) * len(uploaded)
    progress = st.progress(0, text="Starting provider token counts…")
    completed = 0
    for pair_name, side, file in uploaded:
        data = file.getvalue()
        for provider, models in selected_models.items():
            for model_id in models:
                completed += 1
                progress.progress(
                    completed / max(jobs, 1),
                    text=f"Counting {file.name} with {provider} / {model_id}",
                )
                if not api_keys[provider].strip():
                    error_rows.append(
                        {
                            "Pair": pair_name,
                            "Side": side,
                            "File": file.name,
                            "Provider": provider,
                            "Model": model_id,
                            "Reason": f"No {provider} API key supplied.",
                        }
                    )
                    continue
                try:
                    adapter = ADAPTERS[provider](api_keys[provider])
                    count = adapter.count_input(model_id, prompt, file.name, data)
                    cost = calculate_cost(
                        pair=pair_name,
                        side=side,
                        filename=file.name,
                        count=count,
                        price=prices[model_id],
                        expected_output_tokens=int(expected_output_tokens),
                        repetitions=int(repetitions),
                    )
                    result_rows.append(cost.to_row())
                except (UnsupportedFormat, ProviderError, ValueError) as exc:
                    error_rows.append(
                        {
                            "Pair": pair_name,
                            "Side": side,
                            "File": file.name,
                            "Provider": provider,
                            "Model": model_id,
                            "Reason": str(exc),
                        }
                    )
                except Exception as exc:
                    error_rows.append(
                        {
                            "Pair": pair_name,
                            "Side": side,
                            "File": file.name,
                            "Provider": provider,
                            "Model": model_id,
                            "Reason": f"Unexpected counting failure: {exc}",
                        }
                    )
    progress.empty()
    st.session_state.result_rows = result_rows
    st.session_state.error_rows = error_rows

if st.session_state.get("result_rows"):
    st.subheader("Results")
    result_frame = add_pair_deltas(pd.DataFrame(st.session_state.result_rows))
    display_frame = result_frame.copy()
    money_columns = [column for column in display_frame if "cost" in column.lower()]
    for column in money_columns:
        display_frame[column] = pd.to_numeric(display_frame[column], errors="coerce")
    st.dataframe(
        display_frame,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Pricing source": st.column_config.LinkColumn("Pricing source"),
            "File input cost USD": st.column_config.NumberColumn(format="$%.8f"),
            "Complete input cost USD": st.column_config.NumberColumn(format="$%.8f"),
            "Estimated output cost USD": st.column_config.NumberColumn(format="$%.8f"),
            "Scenario total USD": st.column_config.NumberColumn(format="$%.8f"),
            "Cost difference vs pair USD": st.column_config.NumberColumn(format="$%.8f"),
            "Cost difference vs pair %": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
    st.download_button(
        "Download CSV report",
        data=result_frame.to_csv(index=False).encode("utf-8"),
        file_name="file-token-cost-comparison.csv",
        mime="text/csv",
    )

if st.session_state.get("error_rows"):
    st.subheader("Unsupported or failed counts")
    st.dataframe(pd.DataFrame(st.session_state.error_rows), use_container_width=True, hide_index=True)

