# Tests for the audit log (app/audit.py and app/routers/audit_log.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).

from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AuditLog
from tests.helpers import Org, as_agent, log_in


def read_log(client: TestClient, org: Org, query: str = "") -> list[dict[str, Any]]:
    """Read the audit log as Hana (hr)."""
    response = client.get(f"/audit-log{query}", headers=log_in(client, org.hr.email))
    assert response.status_code == 200
    return response.json()


def test_a_change_is_written_down_with_before_and_after(client: TestClient, db: Session, org: Org) -> None:
    hana = db.query(AuditLog).count()  # 0: the test database starts empty
    assert hana == 0

    client.patch(f"/employees/{org.report.id}", json={"job_title": "Team Lead"}, headers=log_in(client, org.hr.email))

    [row] = read_log(client, org)  # exactly one row
    assert row["action"] == "update_employee"
    assert row["table_name"] == "employees"
    assert row["record_id"] == org.report.id
    assert row["before"]["job_title"] == "Software Engineer"
    assert row["after"]["job_title"] == "Team Lead"
    assert row["source"] == "ui"


def test_the_log_says_who_made_the_change(client: TestClient, org: Org) -> None:
    client.patch(f"/employees/{org.report.id}/contact", json={"phone": "+1 555 000 0000"}, headers=log_in(client, org.report.email))

    [row] = read_log(client, org)
    assert row["account_kind"] == "user"
    assert row["account_id"] == org.report.id  # in the org fixture, each login id = their employee id


def test_changes_from_the_agent_are_marked_as_agent(client: TestClient, org: Org) -> None:
    headers = as_agent(log_in(client, org.report.email))

    client.patch(f"/employees/{org.report.id}/contact", json={"phone": "+1 555 000 0000"}, headers=headers)

    assert read_log(client, org)[0]["source"] == "agent"


def test_a_refused_request_writes_nothing(client: TestClient, org: Org) -> None:
    # Mona may not change employees. The request fails, so nothing changed, so nothing is logged.
    client.patch(f"/employees/{org.report.id}", json={"job_title": "CEO"}, headers=log_in(client, org.manager.email))

    assert read_log(client, org) == []


def test_password_hashes_never_go_into_the_log(client: TestClient, org: Org) -> None:
    # A role change saves a copy of the users row, which has a password_hash column.
    client.patch(f"/employees/{org.report.id}/role", json={"role": "manager"}, headers=log_in(client, org.hr.email))

    [row] = read_log(client, org)
    assert row["after"]["role"] == "manager"
    assert "password_hash" not in row["after"]
    assert "password_hash" not in row["before"]


def test_money_stays_exact_in_the_log(client: TestClient, org: Org) -> None:
    data = {"amount": "71234.50", "effective_date": "2026-01-15"}
    client.post(f"/employees/{org.report.id}/salaries", json=data, headers=log_in(client, org.hr.email))

    [row] = read_log(client, org)
    assert row["after"]["amount"] == "71234.50"  # text, not the float 71234.5


def test_the_log_can_be_filtered_by_record(client: TestClient, org: Org) -> None:
    hr = log_in(client, org.hr.email)
    client.patch(f"/employees/{org.report.id}", json={"job_title": "A"}, headers=hr)
    client.patch(f"/employees/{org.stranger.id}", json={"job_title": "B"}, headers=hr)

    rows = read_log(client, org, f"?table_name=employees&record_id={org.stranger.id}")

    assert [row["after"]["job_title"] for row in rows] == ["B"]


def test_only_hr_and_admin_can_read_the_log(client: TestClient, org: Org) -> None:
    for person in (org.report, org.manager):
        assert client.get("/audit-log", headers=log_in(client, person.email)).status_code == 403
