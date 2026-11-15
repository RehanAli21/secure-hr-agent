# Shared test setup. pytest runs the fixtures below automatically for the tests that ask for them.

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app import crud_api, llm
from app.main import app
from app.sessions import SESSIONS
from tests.fake_crud_api import FakeCrudApi
from tests.fake_llm import FakeLLM


@pytest.fixture
def fake_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeCrudApi]:
    """Point the agent service at a fake CRUD API, and start with no sessions."""
    fake = FakeCrudApi()
    monkeypatch.setattr(crud_api, "http", fake.client())
    SESSIONS.clear()
    yield fake
    SESSIONS.clear()


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> FakeLLM:
    """Use a fake AI model that follows a script, instead of the real one in Ollama."""
    fake = FakeLLM()
    monkeypatch.setattr(llm, "ask_llm", fake.ask)
    return fake


@pytest.fixture
def client(fake_api: FakeCrudApi, fake_llm: FakeLLM) -> TestClient:
    """A test browser. Like a real one, it keeps cookies between requests.
    It always uses the fakes, so no test ever needs the real backend or Ollama."""
    return TestClient(app)
