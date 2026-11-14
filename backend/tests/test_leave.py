# Tests for leave requests and their permission rules (app/routers/leave.py).
# The `org` fixture gives us: Hana (hr), Mona (manager), Ravi (Mona's report), Omar (stranger).
#
# We use dates in 2099, so the tests keep working in the future (some rules look at "today").

from typing import Any

from fastapi.testclient import TestClient

from tests.helpers import Org, log_in


def submit_leave(
    client: TestClient,
    headers: dict[str, str],
    start: str = "2099-03-01",
    end: str = "2099-03-05",
    leave_type: str = "annual",
) -> Any:
    """Ask for leave as the logged-in person and return the response."""
    data = {"leave_type": leave_type, "start_date": start, "end_date": end, "reason": "Family wedding"}
    return client.post("/leave", json=data, headers=headers)


# ---------- Asking for leave ----------

def test_an_employee_asks_for_leave_for_themselves(client: TestClient, org: Org) -> None:
    response = submit_leave(client, log_in(client, org.report.email))

    assert response.status_code == 201
    assert response.json()["employee_id"] == org.report.id  # taken from the token
    assert response.json()["status"] == "pending"


def test_nobody_can_ask_leave_for_someone_else(client: TestClient, org: Org) -> None:
    data = {"employee_id": org.stranger.id, "leave_type": "annual", "start_date": "2099-03-01", "end_date": "2099-03-02"}

    response = client.post("/leave", json=data, headers=log_in(client, org.report.email))

    assert response.status_code == 422  # employee_id is not even an allowed field


def test_leave_on_days_you_already_have_off_is_refused(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.report.email)
    submit_leave(client, headers, "2099-03-01", "2099-03-05")

    response = submit_leave(client, headers, "2099-03-04", "2099-03-08")

    assert response.status_code == 409


def test_leave_cannot_end_before_it_starts(client: TestClient, org: Org) -> None:
    response = submit_leave(client, log_in(client, org.report.email), "2099-03-05", "2099-03-01")

    assert response.status_code == 400


def test_only_known_leave_types_are_accepted(client: TestClient, org: Org) -> None:
    response = submit_leave(client, log_in(client, org.report.email), leave_type="holiday")

    assert response.status_code == 422


# ---------- Who can see leave, and the private reason ----------

def test_the_manager_sees_the_leave_but_not_the_reason(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    response = client.get(f"/leave/{leave_id}", headers=log_in(client, org.manager.email))

    assert response.status_code == 200
    assert response.json()["reason"] is None


def test_the_employee_and_hr_see_the_reason(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    for person in (org.report, org.hr):
        response = client.get(f"/leave/{leave_id}", headers=log_in(client, person.email))
        assert response.json()["reason"] == "Family wedding"


def test_a_stranger_cannot_see_someone_elses_leave(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    response = client.get(f"/leave/{leave_id}", headers=log_in(client, org.stranger.email))

    assert response.status_code == 404


def test_the_leave_list_only_shows_leave_you_may_see(client: TestClient, org: Org) -> None:
    submit_leave(client, log_in(client, org.report.email))
    submit_leave(client, log_in(client, org.stranger.email))

    manager_list = client.get("/leave", headers=log_in(client, org.manager.email)).json()
    stranger_list = client.get("/leave", headers=log_in(client, org.stranger.email)).json()
    hr_list = client.get("/leave", headers=log_in(client, org.hr.email)).json()

    assert [row["employee_id"] for row in manager_list] == [org.report.id]
    assert [row["employee_id"] for row in stranger_list] == [org.stranger.id]
    assert len(hr_list) == 2


# ---------- Approving and rejecting ----------

def test_the_manager_approves_their_reports_leave(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    response = client.post(f"/leave/{leave_id}/approve", headers=log_in(client, org.manager.email))

    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["approver_id"] == org.manager.id


def test_nobody_approves_their_own_leave(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.report.email)
    leave_id = submit_leave(client, headers).json()["id"]

    response = client.post(f"/leave/{leave_id}/approve", headers=headers)

    assert response.status_code == 403


def test_hr_cannot_approve_leave_because_hr_is_not_the_manager(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    response = client.post(f"/leave/{leave_id}/approve", headers=log_in(client, org.hr.email))

    assert response.status_code == 403


def test_leave_can_only_be_decided_once(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]
    manager = log_in(client, org.manager.email)
    client.post(f"/leave/{leave_id}/reject", headers=manager)

    response = client.post(f"/leave/{leave_id}/approve", headers=manager)

    assert response.status_code == 400


# ---------- Cancelling ----------

def test_an_employee_cancels_their_own_leave(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.report.email)
    leave_id = submit_leave(client, headers).json()["id"]

    response = client.post(f"/leave/{leave_id}/cancel", headers=headers)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_the_manager_cannot_cancel_a_reports_leave(client: TestClient, org: Org) -> None:
    leave_id = submit_leave(client, log_in(client, org.report.email)).json()["id"]

    response = client.post(f"/leave/{leave_id}/cancel", headers=log_in(client, org.manager.email))

    assert response.status_code == 403


def test_rejected_leave_cannot_be_cancelled(client: TestClient, org: Org) -> None:
    headers = log_in(client, org.report.email)
    leave_id = submit_leave(client, headers).json()["id"]
    client.post(f"/leave/{leave_id}/reject", headers=log_in(client, org.manager.email))

    response = client.post(f"/leave/{leave_id}/cancel", headers=headers)

    assert response.status_code == 400
