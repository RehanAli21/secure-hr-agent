# Tests for roles and permissions (app/routers/roles.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.helpers import Org, as_agent, create_employee, create_login, create_superuser, log_in


def change_role(client: TestClient, headers: dict[str, str], employee_id: int, role: str) -> int:
    """Try to change a role. Returns the status code."""
    return client.patch(f"/employees/{employee_id}/role", json={"role": role}, headers=headers).status_code


# ---------- What may I do? ----------

def test_everyone_can_list_the_roles(client: TestClient, org: Org) -> None:
    response = client.get("/roles", headers=log_in(client, org.report.email))

    assert [role["name"] for role in response.json()] == ["employee", "manager", "hr", "admin"]


def test_my_permissions_match_my_role(client: TestClient, org: Org) -> None:
    ravi = client.get("/me/permissions", headers=log_in(client, org.report.email)).json()
    hana = client.get("/me/permissions", headers=log_in(client, org.hr.email)).json()

    assert ravi["role"] == "employee"
    assert "submit_leave" in ravi["permissions"]
    assert "change_salary" not in ravi["permissions"]
    assert "change_salary" in hana["permissions"]


def test_being_a_manager_is_worked_out_live(client: TestClient, db: Session, org: Org) -> None:
    # Anyone who has people reporting to them gets the manager permissions, whatever their role.
    mona = log_in(client, org.manager.email)
    hana = log_in(client, org.hr.email)
    assert client.get("/me/permissions", headers=mona).json()["is_manager"] is True
    assert "decide_reports_leave" not in client.get("/me/permissions", headers=hana).json()["permissions"]

    # Ravi moves from Mona's team to Hana's team.
    org.report.manager_id = org.hr.id
    db.commit()

    # No new login needed: it is worked out again on every request.
    assert client.get("/me/permissions", headers=mona).json()["is_manager"] is False
    hana_now = client.get("/me/permissions", headers=hana).json()
    assert hana_now["is_manager"] is True
    assert "decide_reports_leave" in hana_now["permissions"]


def test_a_superuser_may_do_everything(client: TestClient, db: Session) -> None:
    superuser = create_superuser(db)

    response = client.get("/me/permissions", headers=log_in(client, superuser.email, superuser=True))

    assert response.json()["permissions"] == ["everything"]


# ---------- Changing roles ----------

def test_hr_changes_a_role_and_it_counts_right_away(client: TestClient, org: Org) -> None:
    ravi = log_in(client, org.report.email)

    assert change_role(client, log_in(client, org.hr.email), org.report.id, "manager") == 200
    # Ravi does not have to log in again: the role is read from the database on every request.
    assert client.get("/me", headers=ravi).json()["role"] == "manager"


def test_only_hr_and_admin_change_roles(client: TestClient, org: Org) -> None:
    assert change_role(client, log_in(client, org.manager.email), org.report.id, "hr") == 403


def test_nobody_changes_their_own_role(client: TestClient, org: Org) -> None:
    assert change_role(client, log_in(client, org.hr.email), org.hr.id, "admin") == 403


def test_hr_cannot_give_or_take_away_the_admin_role(client: TestClient, db: Session, org: Org) -> None:
    admin = create_employee(db, "Adam Admin")
    create_login(db, admin, role="admin")
    hr = log_in(client, org.hr.email)

    assert change_role(client, hr, org.report.id, "admin") == 403   # give
    assert change_role(client, hr, admin.id, "employee") == 403      # take away


def test_an_admin_can_give_the_admin_role(client: TestClient, db: Session, org: Org) -> None:
    admin = create_employee(db, "Adam Admin")
    create_login(db, admin, role="admin")

    assert change_role(client, log_in(client, admin.email), org.report.id, "admin") == 200


def test_only_the_four_roles_exist(client: TestClient, org: Org) -> None:
    assert change_role(client, log_in(client, org.hr.email), org.report.id, "boss") == 422


def test_an_employee_without_a_login_has_no_role(client: TestClient, db: Session, org: Org) -> None:
    no_login = create_employee(db, "Nora Nologin")

    assert change_role(client, log_in(client, org.hr.email), no_login.id, "manager") == 404


def test_the_agent_cannot_change_a_role_without_confirmation(client: TestClient, org: Org) -> None:
    headers = as_agent(log_in(client, org.hr.email))

    assert change_role(client, headers, org.report.id, "manager") == 403
