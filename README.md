# Live-Priced File Token Cost Calculator

A local Streamlit app for comparing provider-reported token counts and API costs for PDF/TXT, PowerPoint/TXT, and Excel/CSV pairs across OpenAI, Claude, and Gemini.

## Run locally

Python 3.11 or newer is recommended.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run app.py
```

The app retrieves official pricing once at the start of each session. If a provider page cannot be validated, it uses a visibly labeled fallback snapshot from `data/fallback_pricing.json`.

For development and tests, install `requirements-dev.txt` instead of `requirements.txt`.

## What leaves your computer

- Pricing-page requests contain no uploaded data.
- Files are sent only after **Calculate provider costs** is clicked.
- A file goes only to providers with selected models and a supplied API key.
- API keys remain in Streamlit session memory and are not written to files or CSV reports.
- Gemini keys are transmitted in the `x-goog-api-key` request header, never in the URL.
- Individual uploads are limited to 25 MB, and each calculation is capped at 30 provider requests.

## Provider behavior

- OpenAI receives native supported file inputs through its input-token count endpoint.
- Claude receives PDFs and decoded plain-text files. Binary PowerPoint and Excel files are reported as unsupported; use their TXT/CSV comparison versions.
- Gemini receives PDFs natively. TXT/CSV are sent as text. PPTX/XLSX are locally text-extracted and clearly labeled in results because Gemini document vision is PDF-specific.

Output cost is based on the expected output-token value entered in the app. No generation request is made.
