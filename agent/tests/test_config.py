# Tests for the settings (app/config.py).
# Settings are read when the file is imported, so each test runs a fresh Python that imports it.

import os
import subprocess
import sys


def import_config(**settings: str) -> subprocess.CompletedProcess[str]:
    """Import app.config in a new Python process, with these settings in the environment."""
    env = {**os.environ, **settings}
    return subprocess.run([sys.executable, "-c", "import app.config as c; print(c.OLLAMA_CONTEXT)"],
                          capture_output=True, text=True, env=env)


def test_the_context_size_comes_from_the_settings() -> None:
    result = import_config(OLLAMA_CONTEXT="8192")

    assert result.stdout.strip() == "8192"


def test_a_context_size_that_is_not_a_number_stops_with_a_clear_message() -> None:
    result = import_config(OLLAMA_CONTEXT="lots")

    assert result.returncode != 0
    assert "OLLAMA_CONTEXT must be a whole number" in result.stderr
