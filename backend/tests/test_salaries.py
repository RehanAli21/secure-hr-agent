# Tests for salaries and their permission rules (app/routers/salaries.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Salary
from tests.helpers import Org, log_in


@pytest.fixture
def ravi_has_salaries(db: Session, org: Org) -> None:
    """Give Ravi a salary history: a starting salary and one raise."""
    db.add(Salary(employee_id=org.report.id, amount=Decimal("60000.00"), effective_date=date(2024, 1, 15)))
    db.add(Salary(employee_id=org.report.id, amount=Decimal("65000.00"), effective_date=date(2025, 1, 15)))
    db.commit()


# ---------- Who can see a salary ----------

def test_an_employee_sees_their_own_salary_history_newest_first(
    client: TestClient, org: Org, ravi_has_salaries: None
) -> None:
    response = client.get(f"/employees/{org.report.id}/salaries", headers=log_in(client, org.report.email))

    assert response.status_code == 200
    # Money comes back as exact text, never as a float.
    assert [row["amount"] for row in response.json()] == ["65000.00", "60000.00"]


def test_a_manager_cannot_see_a_reports_salary(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    # 403, not 404: the manager may know Ravi exists, but not what he earns.
    response = client.get(f"/employees/{org.report.id}/salaries", headers=log_in(client, org.manager.email))

    assert response.status_code == 403


def test_a_stranger_gets_404_for_a_salary(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    response = client.get(f"/employees/{org.report.id}/salaries", headers=log_in(client, org.stranger.email))

    assert response.status_code == 404


def test_hr_sees_every_salary(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    response = client.get(f"/employees/{org.report.id}/salaries", headers=log_in(client, org.hr.email))

    assert len(response.json()) == 2


# ---------- Giving a new salary ----------

RAISE = {"amount": "70000.50", "effective_date": "2026-01-15"}


def test_only_hr_can_give_a_raise(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    for person in (org.report, org.manager):
        response = client.post(f"/employees/{org.report.id}/salaries", json=RAISE, headers=log_in(client, person.email))
        assert response.status_code == 403

    response = client.post(f"/employees/{org.report.id}/salaries", json=RAISE, headers=log_in(client, org.hr.email))
    assert response.status_code == 201
    assert response.json()["amount"] == "70000.50"


def test_a_raise_adds_a_row_and_keeps_the_history(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    hr = log_in(client, org.hr.email)

    client.post(f"/employees/{org.report.id}/salaries", json=RAISE, headers=hr)

    history = client.get(f"/employees/{org.report.id}/salaries", headers=hr).json()
    assert [row["amount"] for row in history] == ["70000.50", "65000.00", "60000.00"]


def test_hr_cannot_give_themselves_a_raise(client: TestClient, org: Org) -> None:
    response = client.post(f"/employees/{org.hr.id}/salaries", json=RAISE, headers=log_in(client, org.hr.email))

    assert response.status_code == 403


def test_a_new_salary_must_start_after_the_latest_one(client: TestClient, org: Org, ravi_has_salaries: None) -> None:
    # The latest one starts on 2025-01-15. Changing the past would rewrite history.
    data = {"amount": "70000", "effective_date": "2025-01-15"}

    response = client.post(f"/employees/{org.report.id}/salaries", json=data, headers=log_in(client, org.hr.email))

    assert response.status_code == 400


@pytest.mark.parametrize("bad_amount", ["0", "-100", "50000.123"])
def test_bad_amounts_are_refused(client: TestClient, org: Org, bad_amount: str) -> None:
    # parametrize = run this same test 3 times, once with each bad amount.
    data = {"amount": bad_amount, "effective_date": "2026-01-15"}

    response = client.post(f"/employees/{org.report.id}/salaries", json=data, headers=log_in(client, org.hr.email))

    assert response.status_code == 422


def test_salary_history_can_never_be_edited_or_deleted(client: TestClient, org: Org) -> None:
    hr = log_in(client, org.hr.email)

    assert client.patch(f"/employees/{org.report.id}/salaries", json={}, headers=hr).status_code == 405
    assert client.delete(f"/employees/{org.report.id}/salaries", headers=hr).status_code == 405
