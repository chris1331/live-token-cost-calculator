from pathlib import Path

from src.security import csv_safe


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_secret_files_are_ignored():
    ignore = (PROJECT_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in ignore
    assert ".streamlit/secrets.toml" in ignore
    assert "*.pem" in ignore


def test_runtime_requirements_do_not_install_test_runner():
    runtime = (PROJECT_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
    development = (PROJECT_ROOT / "requirements-dev.txt").read_text(encoding="utf-8").lower()
    assert "pytest" not in runtime
    assert "pytest>=9.0.3" in development


def test_streamlit_upload_limit_is_configured():
    config = (PROJECT_ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert "maxUploadSize = 25" in config


def test_csv_formula_values_are_neutralized():
    assert csv_safe("=HYPERLINK(\"https://example.test\")") == "'=HYPERLINK(\"https://example.test\")"
    assert csv_safe("@SUM(1,2)") == "'@SUM(1,2)"
    assert csv_safe("ordinary.pdf") == "ordinary.pdf"
