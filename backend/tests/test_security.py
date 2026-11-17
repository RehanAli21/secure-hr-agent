# The spec's security test cases, for the CRUD API: an attacker who controls the AI agent (or
# calls the API directly) can never do more than the logged-in user may.
#
# Some cases are already tested elsewhere; SECURITY.md (in the project root) lists every case
# with all of its tests. This file has the ones that were missing.
# The `org` fixture: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (a stranger).

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import Employee
from tests.helpers import Org, as_agent, log_in

LEAVE = {"leave_type": "annual", "start_date": "2099-03-01", "end_date": "2099-03-05"}


# ---------- "Employee asks the agent to raise their own salary" ----------

def test_an_employee_cannot_raise_their_own_salary(client: TestClient, org: Org) -> None:
    ravi = log_in(client, org.report.email)
    raise_ravi = {"amount": "999999", "effective_date": "2099-01-01"}

    # Not directly, not as the agent, and not by asking for a pending action either.
    assert client.post(f"/employees/{org.report.id}/salaries", json=raise_ravi, headers=ravi).status_code == 403
    assert client.post(f"/employees/{org.report.id}/salaries", json=raise_ravi,
                       headers=as_agent(ravi)).status_code == 403
    proposal = {"action": "change_salary", "target_id": org.report.id, "args": raise_ravi}
    assert client.post("/actions", json=proposal, headers=as_agent(ravi)).status_code == 403


# ---------- "Manager asks for a review or record of someone outside their team" ----------

def test_a_manager_cannot_read_records_outside_their_team(client: TestClient, org: Org) -> None:
    mona = log_in(client, org.manager.email)
    omar = f"/employees/{org.stranger.id}"

    # 404, not 403: she can't even find out that Omar exists.
    assert client.get(omar, headers=mona).status_code == 404
    assert client.get(f"{omar}/reviews", headers=mona).status_code == 404
    assert client.get(f"{omar}/salaries", headers=mona).status_code == 404
    # Asking the list for Omar's leave gives nothing.
    client.post("/leave", json=LEAVE, headers=log_in(client, org.stranger.email))
    assert client.get(f"/leave?employee_id={org.stranger.id}", headers=mona).json() == []


def test_a_manager_cannot_decide_leave_outside_their_team(client: TestClient, org: Org) -> None:
    omars_leave = client.post("/leave", json=LEAVE, headers=log_in(client, org.stranger.email)).json()

    response = client.post(f"/leave/{omars_leave['id']}/approve", headers=log_in(client, org.manager.email))

    assert response.status_code in (403, 404)


def test_a_manager_cannot_review_someone_outside_their_team(client: TestClient, org: Org) -> None:
    review = {"period": "2025-H2", "rating": 1, "comments": "Bad"}

    response = client.post(f"/employees/{org.stranger.id}/reviews", json=review,
                           headers=log_in(client, org.manager.email))

    assert response.status_code in (403, 404)


# ---------- "Model passes user_id or role in tool args" ----------

def test_who_is_acting_always_comes_from_the_token(client: TestClient, org: Org) -> None:
    # Ravi tries to ask leave in Omar's name, or to say he is HR. Unknown fields are refused
    # (422), so a made-up identity never even reaches the code.
    ravi = as_agent(log_in(client, org.report.email))

    for extra in ({"employee_id": org.stranger.id}, {"user_id": 1}, {"role": "hr"}):
        response = client.post("/leave", json={**LEAVE, **extra}, headers=ravi)
        assert response.status_code == 422, extra


# ---------- "Agent changes arguments after the preview" ----------

def test_confirm_runs_exactly_the_stored_arguments(client: TestClient, db: Session, org: Org) -> None:
    hana = log_in(client, org.hr.email)
    proposal = {"action": "terminate_employee", "target_id": org.report.id,
                "args": {"termination_date": "2026-12-31"}}
    action_id = client.post("/actions", json=proposal, headers=as_agent(hana)).json()["id"]

    # Whatever comes with the confirm is ignored: the stored arguments run.
    other = {"target_id": org.stranger.id, "args": {"termination_date": "2099-01-01"}}
    response = client.post(f"/actions/{action_id}/confirm", json=other, headers=hana)

    assert response.status_code == 200
    db.expire_all()
    ravi = db.get(Employee, org.report.id)
    omar = db.get(Employee, org.stranger.id)
    assert ravi is not None and omar is not None
    assert str(ravi.termination_date) == "2026-12-31"
    assert omar.employment_status == "active"


def test_a_pending_action_cannot_be_changed(client: TestClient, org: Org) -> None:
    hana = log_in(client, org.hr.email)
    proposal = {"action": "terminate_employee", "target_id": org.report.id,
                "args": {"termination_date": "2026-12-31"}}
    action_id = client.post("/actions", json=proposal, headers=as_agent(hana)).json()["id"]

    # There is no endpoint to edit one (405 = this method is not allowed here).
    new_args = {"args": {"termination_date": "2099-01-01"}}
    assert client.patch(f"/actions/{action_id}", json=new_args, headers=as_agent(hana)).status_code == 405
    assert client.put(f"/actions/{action_id}", json=new_args, headers=as_agent(hana)).status_code == 405


# ---------- "Bulk update over the row cap" ----------

def test_there_is_no_way_to_change_many_records_at_once(client: TestClient, org: Org) -> None:
    # Bulk updates are not built, so the cap is simple: every change is one record at a time.
    hana = log_in(client, org.hr.email)
    paths: list[str] = list(app.openapi()["paths"])  # every endpoint the API has

    assert not any("bulk" in path for path in paths)
    assert client.patch("/employees", json={"job_title": "X"}, headers=hana).status_code == 405
    # And reading is capped too: at most 100 rows per page.
    assert client.get("/employees?limit=101", headers=hana).status_code == 422
    assert client.get("/leave?limit=101", headers=hana).status_code == 422
