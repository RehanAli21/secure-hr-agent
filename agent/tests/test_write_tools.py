# Tests for the tools that CHANGE something (WRITE_TOOLS in app/tools.py).
# For now: asking for and cancelling your own leave, and approving or rejecting your team's leave.

import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.agent import MAX_CHANGES_PER_MESSAGE
from app.tools import TOOL_PERMISSIONS, WRITE_TOOLS, can_be_cancelled
from tests.fake_crud_api import EMAIL, EMPLOYEE_PERMISSIONS, PASSWORD, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply

GOOD_LEAVE: dict[str, object] = {"leave_type": "annual", "start_date": "2099-01-05", "end_date": "2099-01-09"}


def log_in(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200


def leave_posts(fake_api: FakeCrudApi) -> list[dict[str, object]]:
    """The bodies of every POST /leave the agent sent to the CRUD API."""
    return [json.loads(r.content) for r in fake_api.requests if r.method == "POST" and r.url.path == "/leave"]


def decision_posts(fake_api: FakeCrudApi) -> list[str]:
    """The paths of every approve or reject the agent sent, e.g. ["/leave/42/approve"]."""
    return [r.url.path for r in fake_api.requests
            if r.method == "POST" and r.url.path.endswith(("/approve", "/reject"))]


def make_me_a_manager(fake_api: FakeCrudApi) -> None:
    permissions = EMPLOYEE_PERMISSIONS["permissions"] + ["view_reports_leave", "decide_reports_leave"]
    fake_api.permissions = {**EMPLOYEE_PERMISSIONS, "is_manager": True, "permissions": permissions}


def cancel_posts(fake_api: FakeCrudApi) -> list[str]:
    """The paths of every cancel the agent sent to the CRUD API, e.g. ["/leave/12/cancel"]."""
    return [r.url.path for r in fake_api.requests if r.method == "POST" and r.url.path.endswith("/cancel")]


def ask(client: TestClient, fake_llm: FakeLLM, args: dict[str, object], tool: str = "submit_leave_request") -> str:
    """The (fake) model calls a tool once. Returns the tool result it got back."""
    fake_llm.script = [tool_call_reply(tool, args), text_reply("Done.")]
    assert client.post("/chat", json={"message": "I want leave"}).status_code == 200
    return fake_llm.last_tool_result()


# ---------- Asking for leave ----------

def test_asking_for_leave_sends_only_the_type_and_dates(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)

    result = ask(client, fake_llm, GOOD_LEAVE)

    # Exactly these three fields: no employee id (the CRUD API takes it from the token), no reason.
    assert leave_posts(fake_api) == [GOOD_LEAVE]
    assert '"status":"pending"' in result
    assert "until the manager approves it" in result


def test_a_made_up_leave_type_is_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = ask(client, fake_llm, {**GOOD_LEAVE, "leave_type": "holiday"})

    assert "wrong arguments" in result
    assert leave_posts(fake_api) == []  # nothing was sent


def test_a_date_that_is_not_a_date_is_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)

    result = ask(client, fake_llm, {**GOOD_LEAVE, "start_date": "next Monday"})

    assert "wrong arguments" in result
    assert leave_posts(fake_api) == []


def test_leave_can_not_be_asked_for_someone_else(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # An employee_id (or a reason) is an unknown argument: refused before anything is sent.
    log_in(client)

    assert "wrong arguments" in ask(client, fake_llm, {**GOOD_LEAVE, "employee_id": 99})
    assert "wrong arguments" in ask(client, fake_llm, {**GOOD_LEAVE, "reason": "Flu"})
    assert leave_posts(fake_api) == []


def test_when_the_crud_api_says_no_the_model_is_told_why(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    # The fake CRUD API says 409 for leave starting on 2099-02-01, like the real one for overlapping leave.
    log_in(client)

    result = ask(client, fake_llm, {**GOOD_LEAVE, "start_date": "2099-02-01", "end_date": "2099-02-03"})

    assert "Error 409: You already have leave on some of these days" in result


def test_leave_is_not_offered_without_the_permission(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    permissions = [p for p in EMPLOYEE_PERMISSIONS["permissions"] if p != "submit_leave"]
    fake_api.permissions = {**EMPLOYEE_PERMISSIONS, "permissions": permissions}

    result = ask(client, fake_llm, GOOD_LEAVE)

    assert "no tool with that name" in result
    assert leave_posts(fake_api) == []


# ---------- Cancelling leave ----------

def test_leave_is_cancelled_by_its_start_date(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # The fake user has pending leave from 2099-01-05 (id 12). The model only gives the date:
    # the tool finds the request itself, so the model never has to pass on (or guess) an id.
    log_in(client)

    result = ask(client, fake_llm, {"start_date": "2099-01-05"}, tool="cancel_leave_request")

    assert cancel_posts(fake_api) == ["/leave/12/cancel"]
    assert '"status":"cancelled"' in result


def test_a_wrong_date_cancels_nothing_and_lists_the_choices(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)

    result = ask(client, fake_llm, {"start_date": "2099-01-06"}, tool="cancel_leave_request")

    assert cancel_posts(fake_api) == []
    assert "Ask the user which of these they mean" in result
    assert '"start_date":"2099-01-05"' in result  # the leave that CAN be cancelled
    assert "2026-05-20" not in result             # past leave is not offered as a choice


def test_without_a_date_it_only_lists_the_choices(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # "Cancel my leave": the model calls the tool without a date and gets only the leave that CAN
    # be cancelled (not the whole history), so it can ask the user which one.
    log_in(client)

    result = ask(client, fake_llm, {}, tool="cancel_leave_request")

    assert cancel_posts(fake_api) == []
    assert "Nothing was cancelled yet" in result
    assert '"start_date":"2099-01-05"' in result
    assert "2026-05-20" not in result


def test_past_leave_is_never_cancelled(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # 2026-05-20 is approved and over: not a choice, even when the model asks for exactly that date.
    log_in(client)

    ask(client, fake_llm, {"start_date": "2026-05-20"}, tool="cancel_leave_request")

    assert cancel_posts(fake_api) == []


def test_cancel_takes_only_a_date(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # No leave id and no employee id: refused before anything is sent.
    log_in(client)

    assert "wrong arguments" in ask(client, fake_llm, {"leave_id": 12}, tool="cancel_leave_request")
    assert "wrong arguments" in ask(client, fake_llm, {"start_date": "2099-01-05", "employee_id": 99},
                                    tool="cancel_leave_request")
    assert cancel_posts(fake_api) == []


def test_which_leave_can_be_cancelled() -> None:
    # The same rule as the CRUD API: pending, or approved but not started yet.
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    today = date.today().isoformat()

    assert can_be_cancelled({"status": "pending", "start_date": "2020-01-01"})
    assert can_be_cancelled({"status": "approved", "start_date": tomorrow})
    assert not can_be_cancelled({"status": "approved", "start_date": today})  # already started
    assert not can_be_cancelled({"status": "rejected", "start_date": tomorrow})
    assert not can_be_cancelled({"status": "cancelled", "start_date": tomorrow})


# ---------- Approving and rejecting the team's leave ----------
#
# Waiting in the fake CRUD API: Tara (id 42, from 2099-08-03), and Tom twice (id 41 from 2099-07-01,
# id 43 from 2099-09-01).

def test_a_manager_approves_by_name(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    result = ask(client, fake_llm, {"employee_name": "tara"}, tool="approve_leave_request")

    assert decision_posts(fake_api) == ["/leave/42/approve"]
    assert '"status":"approved"' in result
    assert '"employee_name":"Tara Team"' in result


def test_only_pending_leave_is_asked_for(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    ask(client, fake_llm, {"employee_name": "tara"}, tool="approve_leave_request")

    leave_request = [r for r in fake_api.requests if r.url.path == "/leave"][-1]
    assert leave_request.url.params["manager_id"] == "7"  # my team, from GET /me
    assert leave_request.url.params["status"] == "pending"


def test_a_manager_rejects_by_name_and_date(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    result = ask(client, fake_llm, {"employee_name": "Tom", "start_date": "2099-09-01"}, tool="reject_leave_request")

    assert decision_posts(fake_api) == ["/leave/43/reject"]
    assert '"status":"rejected"' in result


def test_two_requests_from_the_same_person_are_not_guessed(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    # Tom has two pending requests: the tool decides nothing and lists both.
    log_in(client)
    make_me_a_manager(fake_api)

    result = ask(client, fake_llm, {"employee_name": "Tom"}, tool="approve_leave_request")

    assert decision_posts(fake_api) == []
    assert "Nothing was done yet" in result
    assert "2099-07-01" in result and "2099-09-01" in result
    assert "Tara" not in result  # only the requests that fit what the model gave


def test_without_details_it_only_lists_what_is_waiting(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    result = ask(client, fake_llm, {}, tool="approve_leave_request")

    assert decision_posts(fake_api) == []
    assert result.count('"status":"pending"') == 3
    assert "2020-03-02" not in result  # approved long ago: not waiting


def test_an_unknown_name_decides_nothing(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    result = ask(client, fake_llm, {"employee_name": "Zara"}, tool="reject_leave_request")

    assert decision_posts(fake_api) == []
    assert "Tara Team" in result and "Tom Team" in result  # all the choices instead


def test_a_decision_takes_no_ids(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)

    assert "wrong arguments" in ask(client, fake_llm, {"leave_id": 42}, tool="approve_leave_request")
    assert "wrong arguments" in ask(client, fake_llm, {"employee_id": 8}, tool="approve_leave_request")
    assert decision_posts(fake_api) == []


def test_a_plain_employee_is_not_offered_the_decisions(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)  # the fake user is a plain employee: no decide_reports_leave

    result = ask(client, fake_llm, {"employee_name": "tara"}, tool="approve_leave_request")

    assert "no tool with that name" in result
    assert decision_posts(fake_api) == []


# ---------- At most one change per message ----------

def test_only_one_change_per_message(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # A confused (or tricked) model tries to ask for leave three times in one message.
    log_in(client)
    fake_llm.script = [
        tool_call_reply("submit_leave_request", GOOD_LEAVE),
        tool_call_reply("submit_leave_request", {**GOOD_LEAVE, "start_date": "2099-03-01", "end_date": "2099-03-02"}),
        tool_call_reply("submit_leave_request", {**GOOD_LEAVE, "start_date": "2099-04-01", "end_date": "2099-04-02"}),
        text_reply("Done."),
    ]

    assert client.post("/chat", json={"message": "I want leave"}).status_code == 200

    assert len(leave_posts(fake_api)) == MAX_CHANGES_PER_MESSAGE == 1
    assert "only one change per message" in fake_llm.last_tool_result()


def test_cancelling_counts_as_a_change_too(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [
        tool_call_reply("submit_leave_request", {**GOOD_LEAVE, "start_date": "2099-03-01", "end_date": "2099-03-02"}),
        tool_call_reply("cancel_leave_request", {"start_date": "2099-01-05"}),
        text_reply("Done."),
    ]

    assert client.post("/chat", json={"message": "Move my leave to March"}).status_code == 200

    assert len(leave_posts(fake_api)) == 1
    assert cancel_posts(fake_api) == []  # the second change was not run
    assert "only one change per message" in fake_llm.last_tool_result()


def test_one_decision_per_message(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    make_me_a_manager(fake_api)
    fake_llm.script = [
        tool_call_reply("approve_leave_request", {"employee_name": "tara"}),
        tool_call_reply("reject_leave_request", {"employee_name": "tom", "start_date": "2099-09-01"}),
        text_reply("Done."),
    ]

    assert client.post("/chat", json={"message": "Approve Tara, reject Tom"}).status_code == 200

    assert decision_posts(fake_api) == ["/leave/42/approve"]
    assert "only one change per message" in fake_llm.last_tool_result()


def test_the_next_message_may_make_a_change_again(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)

    ask(client, fake_llm, GOOD_LEAVE)
    ask(client, fake_llm, {**GOOD_LEAVE, "start_date": "2099-03-01", "end_date": "2099-03-02"})

    assert len(leave_posts(fake_api)) == 2


def test_every_write_tool_is_a_real_tool() -> None:
    # A typo in WRITE_TOOLS would silently leave a changing tool without the one-change limit.
    assert WRITE_TOOLS <= set(TOOL_PERMISSIONS)
