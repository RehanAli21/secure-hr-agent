# Tests for the rate limits (app/rate_limits.py).
# The real limits are high (300 reads a minute), so the tests lower them with monkeypatch.

import pytest
from fastapi.testclient import TestClient

from app import rate_limits
from tests.helpers import PASSWORD, Org, log_in


def test_too_many_reads_give_429(client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limits, "READS_PER_MINUTE", 3)
    ravi = log_in(client, org.report.email)

    for _ in range(3):
        assert client.get("/me", headers=ravi).status_code == 200
    response = client.get("/me", headers=ravi)

    assert response.status_code == 429
    assert response.json()["detail"].startswith("Too many requests. Try again in")
    assert 1 <= int(response.headers["Retry-After"]) <= 60


def test_each_user_has_their_own_count(client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limits, "READS_PER_MINUTE", 2)
    ravi = log_in(client, org.report.email)
    for _ in range(3):
        client.get("/me", headers=ravi)

    # Ravi used up his reads; Omar didn't.
    assert client.get("/me", headers=log_in(client, org.stranger.email)).status_code == 200


def test_writes_have_their_own_stricter_limit(client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limits, "WRITES_PER_MINUTE", 2)
    ravi = log_in(client, org.report.email)
    leave = [{"leave_type": "annual", "start_date": f"2099-0{month}-01", "end_date": f"2099-0{month}-02"}
             for month in (1, 2, 3)]

    assert client.post("/leave", json=leave[0], headers=ravi).status_code == 201
    assert client.post("/leave", json=leave[1], headers=ravi).status_code == 201
    assert client.post("/leave", json=leave[2], headers=ravi).status_code == 429
    assert client.get("/leave", headers=ravi).status_code == 200  # reading still works


def test_too_many_logins_give_429_even_with_the_right_password(
    client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rate_limits, "LOGINS_PER_MINUTE", 3)
    wrong = {"email": org.report.email, "password": "guess"}
    for _ in range(3):
        assert client.post("/auth/login", json=wrong).status_code == 401

    # Uppercase counts as the same email, so it can't be used to get around the limit.
    right = {"email": org.report.email.upper(), "password": PASSWORD}
    assert client.post("/auth/login", json=right).status_code == 429


def test_after_a_minute_requests_count_again(client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rate_limits, "READS_PER_MINUTE", 1)
    ravi = log_in(client, org.report.email)
    now = [1000.0]
    monkeypatch.setattr(rate_limits.time, "monotonic", lambda: now[0])  # a clock we control

    assert client.get("/me", headers=ravi).status_code == 200
    assert client.get("/me", headers=ravi).status_code == 429
    now[0] += 61  # a minute later
    assert client.get("/me", headers=ravi).status_code == 200


def test_the_login_needed_endpoints_are_all_limited(client: TestClient, org: Org, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every router in app/main.py counts, not just the ones we happened to test above.
    monkeypatch.setattr(rate_limits, "READS_PER_MINUTE", 1)
    paths = ["/me/permissions", "/roles", "/departments", "/employees", "/leave",
             f"/employees/{org.report.id}/salaries", f"/employees/{org.report.id}/reviews", "/audit-log", "/actions/1"]
    for path in paths:
        rate_limits.forget_all()
        hana = log_in(client, org.hr.email)
        client.get(path, headers=hana)
        assert client.get(path, headers=hana).status_code == 429, path
