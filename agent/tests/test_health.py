# Tests for the health check.
# We don't start the real CRUD API here: we replace is_crud_api_up with a fake answer.

import httpx
import pytest
from fastapi.testclient import TestClient

from app import crud_api
from app.main import app

client = TestClient(app)


def test_health_when_the_crud_api_is_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crud_api, "is_crud_api_up", lambda: True)

    response = client.get("/health")

    assert response.json() == {"status": "ok", "crud_api": "ok"}


def test_health_when_the_crud_api_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crud_api, "is_crud_api_up", lambda: False)

    response = client.get("/health")

    assert response.json() == {"status": "ok", "crud_api": "unreachable"}


def test_a_wrong_address_counts_as_down(monkeypatch: pytest.MonkeyPatch) -> None:
    # Port 1 has nothing running on it, so the real check must say "down", not crash.
    monkeypatch.setattr(crud_api, "http", httpx.Client(base_url="http://127.0.0.1:1"))

    assert crud_api.is_crud_api_up() is False
