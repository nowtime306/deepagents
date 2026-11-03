"""Never load an API environment or default vendor configuration in evaluation tests."""

import os

import pytest

os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] = "1"
os.environ["DEEPEVAL_DISABLE_DOTENV"] = "1"


@pytest.fixture(autouse=True)
def private_storage(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    for key in ("DEEPEVAL_HOME", "ENV_DIR_PATH", "DEEPEVAL_RESULTS_FOLDER", "DEEPEVAL_CACHE_FOLDER"):
        location = tmp_path / key.lower()
        location.mkdir()
        monkeypatch.setenv(key, str(location))
    for key in list(os.environ):
        if key.startswith(("OPENAI_", "ANTHROPIC_", "CONFIDENT_", "GEMINI_", "AZURE_")):
            monkeypatch.delenv(key)
