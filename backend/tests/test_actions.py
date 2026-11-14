# Tests for pending actions: big changes the user must confirm (app/routers/actions.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).

from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import AuditLog, Employee, PendingAction, utc_now
from tests.helpers import Org, as_agent, create_employee, create_login, log_in

TERMINATE_RAVI_ARGS = {"termination_date": "2026-12-31"}


def ask(client: TestClient, headers: dict[str, str], action: str, target_id: int, args: dict[str, Any]) -> Any:
    """Create a pending action and return the response."""
    data = {"action": action, "target_id": target_id, "args": args}
    return client.post("/actions", json=data, headers=headers)


def ask_to_terminate_ravi(client: TestClient, org: Org) -> int:
    """The agent, working for Hana, asks to terminate Ravi. Returns the pending action id."""
    response = ask(client, as_agent(log_in(client, org.hr.email)), "terminate_employee", org.report.id, TERMINATE_RAVI_ARGS)
    assert response.status_code == 201
    return response.json()["id"]


def ravi_status(db: Session, org: Org) -> str:
    db.expire_all()  # read it fresh from the database
    employee = db.get(Employee, org.report.id)
    assert employee is not None
    return employee.employment_status


# ---------- The agent cannot skip the confirmation ----------

def test_the_agent_cannot_make_big_changes_directly(client: TestClient, org: Org) -> None:
    agent = as_agent(log_in(client, org.hr.email))
    ravi = f"/employees/{org.report.id}"

    assert client.post(f"{ravi}/terminate", json=TERMINATE_RAVI_ARGS, headers=agent).status_code == 403
    assert client.post(f"{ravi}/salaries", json={"amount": "1", "effective_date": "2027-01-01"}, headers=agent).status_code == 403
    assert client.patch(ravi, json={"manager_id": org.stranger.id}, headers=agent).status_code == 403
    assert client.patch(f"{ravi}/role", json={"role": "manager"}, headers=agent).status_code == 403


def test_the_agent_can_still_make_small_changes_directly(client: TestClient, org: Org) -> None:
    agent = as_agent(log_in(client, org.hr.email))

    response = client.patch(f"/employees/{org.report.id}", json={"job_title": "Team Lead"}, headers=agent)

    assert response.status_code == 200


# ---------- Asking for a big change ----------

def test_asking_changes_nothing_and_gives_a_preview(client: TestClient, db: Session, org: Org) -> None:
    response = ask(client, as_agent(log_in(client, org.hr.email)), "terminate_employee", org.report.id, TERMINATE_RAVI_ARGS)

    assert response.status_code == 201
    assert response.json()["status"] == "pending"
    # The preview is built by the API from the real record (the name comes from the database).
    assert "Ravi Report" in response.json()["preview"]["summary"]
    assert ravi_status(db, org) == "active"


def test_only_hr_and_admin_can_ask_for_big_changes(client: TestClient, org: Org) -> None:
    response = ask(client, log_in(client, org.manager.email), "terminate_employee", org.report.id, TERMINATE_RAVI_ARGS)

    assert response.status_code == 403


def test_the_arguments_are_checked_with_the_real_schema(client: TestClient, org: Org) -> None:
    hr = log_in(client, org.hr.email)

    assert ask(client, hr, "terminate_employee", org.report.id, {}).status_code == 422                 # missing date
    assert ask(client, hr, "change_role", org.report.id, {"role": "boss"}).status_code == 422           # bad role
    assert ask(client, hr, "delete_department", 1, {"force": True}).status_code == 422                  # unknown arg
    assert ask(client, hr, "run_sql", 1, {"sql": "DROP TABLE employees"}).status_code == 422            # unknown action


# ---------- Confirming ----------

def test_confirming_runs_the_change(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)

    # Hana clicks "Confirm" in the app (so: no agent header).
    response = client.post(f"/actions/{action_id}/confirm", headers=log_in(client, org.hr.email))

    assert response.status_code == 200
    assert response.json()["action"]["status"] == "done"
    assert response.json()["result"]["employment_status"] == "terminated"
    assert ravi_status(db, org) == "terminated"


def test_a_confirmed_change_is_logged_with_its_pending_action(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    client.post(f"/actions/{action_id}/confirm", headers=log_in(client, org.hr.email))

    row = db.query(AuditLog).one()
    assert row.action == "terminate_employee"
    assert row.source == "agent"
    assert row.pending_action_id == action_id


def test_the_agent_cannot_confirm(client: TestClient, db: Session, org: Org) -> None:
    # Typing "yes" in the chat would make the agent call confirm. That must not work.
    action_id = ask_to_terminate_ravi(client, org)

    response = client.post(f"/actions/{action_id}/confirm", headers=as_agent(log_in(client, org.hr.email)))

    assert response.status_code == 403
    assert ravi_status(db, org) == "active"


def test_only_the_person_who_asked_can_see_or_confirm(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    other_hr = create_employee(db, "Harry Hr")
    create_login(db, other_hr, role="hr")
    harry = log_in(client, other_hr.email)

    assert client.get(f"/actions/{action_id}", headers=harry).status_code == 404
    assert client.post(f"/actions/{action_id}/confirm", headers=harry).status_code == 404


def test_an_action_can_be_confirmed_only_once(client: TestClient, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    hr = log_in(client, org.hr.email)
    client.post(f"/actions/{action_id}/confirm", headers=hr)

    response = client.post(f"/actions/{action_id}/confirm", headers=hr)

    assert response.status_code == 400


def test_an_action_expires_after_5_minutes(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    # Pretend 5 minutes have passed: move the expiry time into the past.
    pending = db.get(PendingAction, action_id)
    assert pending is not None
    pending.expires_at = utc_now() - timedelta(seconds=1)
    db.commit()
    hr = log_in(client, org.hr.email)

    response = client.post(f"/actions/{action_id}/confirm", headers=hr)

    assert response.status_code == 400
    assert client.get(f"/actions/{action_id}", headers=hr).json()["status"] == "expired"
    assert ravi_status(db, org) == "active"


def test_if_the_record_changed_since_the_preview_confirm_gives_409(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    hr = log_in(client, org.hr.email)
    # Someone changes Ravi after the preview was made.
    client.patch(f"/employees/{org.report.id}", json={"job_title": "Team Lead"}, headers=hr)

    response = client.post(f"/actions/{action_id}/confirm", headers=hr)

    assert response.status_code == 409
    assert ravi_status(db, org) == "active"


def test_a_cancelled_action_never_runs(client: TestClient, db: Session, org: Org) -> None:
    action_id = ask_to_terminate_ravi(client, org)
    hr = log_in(client, org.hr.email)
    client.post(f"/actions/{action_id}/cancel", headers=hr)

    response = client.post(f"/actions/{action_id}/confirm", headers=hr)

    assert response.status_code == 400
    assert ravi_status(db, org) == "active"


def test_all_the_normal_checks_run_again_at_confirm(client: TestClient, org: Org) -> None:
    # Hana may ASK to make herself admin, but the real endpoint refuses when it runs.
    hr = log_in(client, org.hr.email)
    action_id = ask(client, hr, "change_role", org.hr.id, {"role": "admin"}).json()["id"]

    response = client.post(f"/actions/{action_id}/confirm", headers=hr)

    assert response.status_code == 403
    # Nothing happened, so the action is still pending (and will expire).
    assert client.get(f"/actions/{action_id}", headers=hr).json()["status"] == "pending"
