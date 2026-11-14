# Tests for the employee endpoints and their permission rules (app/routers/employees.py).
#
# Every test gets the same small company from the `org` fixture (tests/conftest.py):
#
#     Hana (hr)        Mona (manager)        Omar (employee, the "stranger")
#                            |
#                      Ravi (employee, reports to Mona)

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import Org, create_superuser, log_in


def ids_in(response_json: list[dict[str, object]]) -> set[object]:
    """The ids in a list answer, e.g. {1, 3}. A set, because the order does not matter here."""
    return {row["id"] for row in response_json}


# ---------- Who can see whom ----------

def test_an_employee_sees_only_themselves_in_the_list(client: TestClient, org: Org) -> None:
    response = client.get("/employees", headers=log_in(client, org.report.email))

    assert response.status_code == 200
    assert ids_in(response.json()) == {org.report.id}


def test_an_employee_cannot_open_someone_else(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.report.email)

    assert client.get(f"/employees/{org.report.id}", headers=headers).status_code == 200
    assert client.get(f"/employees/{org.stranger.id}", headers=headers).status_code == 404
    assert client.get(f"/employees/{org.manager.id}", headers=headers).status_code == 404


def test_a_hidden_employee_looks_exactly_like_a_missing_one(client: TestClient, org: Org) -> None:
    # Same status AND same message, so nobody can find out which ids exist.
    headers = log_in(client, org.report.email)

    hidden = client.get(f"/employees/{org.stranger.id}", headers=headers)
    missing = client.get("/employees/99999", headers=headers)

    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json() == {"detail": "Employee not found"}


def test_a_manager_sees_themselves_and_their_direct_reports(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.manager.email)

    response = client.get("/employees", headers=headers)

    assert ids_in(response.json()) == {org.manager.id, org.report.id}
    assert client.get(f"/employees/{org.report.id}", headers=headers).status_code == 200
    assert client.get(f"/employees/{org.stranger.id}", headers=headers).status_code == 404


def test_a_manager_loses_access_as_soon_as_the_report_moves(client: TestClient, db: Session, org: Org) -> None:
    # The manager rule is checked against the live org chart on every request, never remembered.
    headers = log_in(client, org.manager.email)
    assert client.get(f"/employees/{org.report.id}", headers=headers).status_code == 200

    org.report.manager_id = None
    db.commit()

    assert client.get(f"/employees/{org.report.id}", headers=headers).status_code == 404


def test_hr_sees_everyone(client: TestClient, org: Org) -> None:
    response = client.get("/employees", headers=log_in(client, org.hr.email))

    assert ids_in(response.json()) == {org.hr.id, org.manager.id, org.report.id, org.stranger.id}


def test_a_superuser_sees_everyone(client: TestClient, db: Session, org: Org) -> None:
    superuser = create_superuser(db)

    response = client.get("/employees", headers=log_in(client, superuser.email, superuser=True))

    assert len(response.json()) == 4


def test_the_list_never_returns_more_than_100_rows(client: TestClient, org: Org) -> None:
    response = client.get("/employees?limit=101", headers=log_in(client, org.hr.email))

    assert response.status_code == 422


# ---------- The private home address ----------

def test_an_employee_sees_their_own_home_address(client: TestClient, org: Org) -> None:
    response = client.get(f"/employees/{org.report.id}", headers=log_in(client, org.report.email))

    assert response.json()["home_address"] == "1 Secret Street, Lakeside"


def test_a_manager_sees_the_phone_but_not_the_home_address(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.manager.email)

    one = client.get(f"/employees/{org.report.id}", headers=headers).json()
    in_list = [row for row in client.get("/employees", headers=headers).json() if row["id"] == org.report.id][0]

    assert one["phone"] == "+1 555 111 2222"
    assert one["home_address"] is None
    assert in_list["home_address"] is None


def test_hr_sees_the_home_address(client: TestClient, org: Org) -> None:
    response = client.get(f"/employees/{org.report.id}", headers=log_in(client, org.hr.email))

    assert response.json()["home_address"] == "1 Secret Street, Lakeside"


# ---------- Creating and changing employees ----------

NEW_EMPLOYEE = {
    "name": "Nina New",
    "email": "nina.new@test.local",
    "job_title": "Designer",
    "department_id": 1,
    "start_date": "2026-02-01",
}


def test_only_hr_can_create_an_employee(client: TestClient, org: Org) -> None:
    for person in (org.report, org.manager):
        response = client.post("/employees", json=NEW_EMPLOYEE, headers=log_in(client, person.email))
        assert response.status_code == 403

    response = client.post("/employees", json=NEW_EMPLOYEE, headers=log_in(client, org.hr.email))
    assert response.status_code == 201
    assert response.json()["employment_status"] == "active"


def test_a_new_employee_cannot_be_created_as_terminated(client: TestClient, org: Org) -> None:
    data = {**NEW_EMPLOYEE, "employment_status": "terminated"}

    response = client.post("/employees", json=data, headers=log_in(client, org.hr.email))

    assert response.status_code == 422


def test_two_employees_cannot_share_an_email(client: TestClient, org: Org) -> None:
    data = {**NEW_EMPLOYEE, "email": org.stranger.email}

    response = client.post("/employees", json=data, headers=log_in(client, org.hr.email))

    assert response.status_code == 409


def test_only_hr_can_change_an_employee(client: TestClient, org: Org) -> None:
    change = {"job_title": "Senior Software Engineer"}

    by_manager = client.patch(f"/employees/{org.report.id}", json=change, headers=log_in(client, org.manager.email))
    by_hr = client.patch(f"/employees/{org.report.id}", json=change, headers=log_in(client, org.hr.email))

    assert by_manager.status_code == 403
    assert by_hr.status_code == 200
    assert by_hr.json()["job_title"] == "Senior Software Engineer"


def test_an_employee_cannot_be_their_own_manager(client: TestClient, org: Org) -> None:
    response = client.patch(
        f"/employees/{org.report.id}",
        json={"manager_id": org.report.id},
        headers=log_in(client, org.hr.email),
    )

    assert response.status_code == 400


# ---------- Contact details ----------

def test_an_employee_can_change_their_own_contact_details(client: TestClient, org: Org) -> None:
    response = client.patch(
        f"/employees/{org.report.id}/contact",
        json={"phone": "+1 555 999 0000"},
        headers=log_in(client, org.report.email),
    )

    assert response.status_code == 200
    assert response.json()["phone"] == "+1 555 999 0000"


def test_the_contact_endpoint_cannot_change_anything_else(client: TestClient, org: Org) -> None:
    response = client.patch(
        f"/employees/{org.report.id}/contact",
        json={"job_title": "CEO"},
        headers=log_in(client, org.report.email),
    )

    assert response.status_code == 422


def test_a_manager_cannot_change_a_reports_contact_details(client: TestClient, org: Org) -> None:
    response = client.patch(
        f"/employees/{org.report.id}/contact",
        json={"phone": "+1 555 999 0000"},
        headers=log_in(client, org.manager.email),
    )

    assert response.status_code == 403


# ---------- Terminating ----------

def test_only_hr_can_terminate(client: TestClient, org: Org) -> None:
    response = client.post(
        f"/employees/{org.report.id}/terminate",
        json={"termination_date": "2026-12-31"},
        headers=log_in(client, org.manager.email),
    )

    assert response.status_code == 403


def test_terminating_keeps_the_record_and_disables_the_login(client: TestClient, org: Org) -> None:
    report_headers = log_in(client, org.report.email)

    response = client.post(
        f"/employees/{org.report.id}/terminate",
        json={"termination_date": "2026-12-31"},
        headers=log_in(client, org.hr.email),
    )

    assert response.status_code == 200
    assert response.json()["employment_status"] == "terminated"
    assert response.json()["termination_date"] == "2026-12-31"
    # Their token was valid a moment ago. Now it stops working.
    assert client.get("/me", headers=report_headers).status_code == 401
    # The record is still there for HR.
    assert client.get(f"/employees/{org.report.id}", headers=log_in(client, org.hr.email)).status_code == 200


def test_a_terminated_employee_cannot_be_changed(client: TestClient, org: Org) -> None:
    hr_headers = log_in(client, org.hr.email)
    client.post(f"/employees/{org.report.id}/terminate", json={"termination_date": "2026-12-31"}, headers=hr_headers)

    response = client.patch(f"/employees/{org.report.id}", json={"job_title": "x"}, headers=hr_headers)

    assert response.status_code == 400


def test_nobody_can_leave_before_they_started(client: TestClient, org: Org) -> None:
    # create_employee gives everyone the start date 2024-01-15.
    response = client.post(
        f"/employees/{org.report.id}/terminate",
        json={"termination_date": "2020-01-01"},
        headers=log_in(client, org.hr.email),
    )

    assert response.status_code == 400


def test_there_is_no_delete_endpoint_for_employees(client: TestClient, org: Org) -> None:
    response = client.delete(f"/employees/{org.report.id}", headers=log_in(client, org.hr.email))

    assert response.status_code == 405  # 405 = "this method is not allowed here"
