# Tests for big changes that the user must confirm (a new salary...).
#
# The agent only PROPOSES the change (POST /actions on the CRUD API). The chat reply carries the
# preview, and only the page's Confirm button (POST /actions/{id}/confirm here) runs it.

import json

from fastapi.testclient import TestClient

from app.tools import TOOL_PERMISSIONS
from tests.fake_crud_api import EMAIL, EMPLOYEE_PERMISSIONS, PASSWORD, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply

# Zara (id 12) in the fake CRUD API. Her salary goes from 50000.00 to 52000.
NEW_SALARY: dict[str, object] = {"employee_id": 12, "amount": "52000", "effective_date": "2027-01-01"}


def log_in_as_hr(client: TestClient, fake_api: FakeCrudApi) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    permissions = ["view_all_employees", "change_salary"]
    fake_api.permissions = {**EMPLOYEE_PERMISSIONS, "role": "hr", "permissions": permissions}


def propose_salary(client: TestClient, fake_llm: FakeLLM, args: dict[str, object] = NEW_SALARY) -> dict[str, object]:
    """The (fake) model proposes a new salary. Returns the chat reply."""
    fake_llm.script = [tool_call_reply("update_employee_salary", args), text_reply("Please click Confirm.")]
    response = client.post("/chat", json={"message": "Give Zara 52000 from 1 January"})
    assert response.status_code == 200
    return response.json()


def crud_posts(fake_api: FakeCrudApi) -> list[str]:
    return [r.url.path for r in fake_api.requests if r.method == "POST" and r.url.path != "/auth/login"]


# ---------- Proposing ----------

def test_the_agent_only_proposes_the_change(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)

    propose_salary(client, fake_llm)

    # The only change asked of the CRUD API is a pending action: nothing about salaries directly.
    assert crud_posts(fake_api) == ["/actions"]
    body = json.loads([r for r in fake_api.requests if r.url.path == "/actions"][0].content)
    assert body == {"action": "change_salary", "target_id": 12,
                    "args": {"amount": "52000", "effective_date": "2027-01-01"}}
    assert fake_api.actions[501]["status"] == "pending"


def test_the_reply_carries_the_preview_for_the_confirm_button(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in_as_hr(client, fake_api)

    reply = propose_salary(client, fake_llm)

    assert reply["pending_action"] == {
        "id": 501,
        # Written by the CRUD API from the real data, not by the model:
        "summary": "Change the salary of Zara Finance from 50000.00 to 52000, starting 2027-01-01.",
        "expires_at": "2026-11-16T09:05:00Z",
    }
    # The model is told that nothing has changed yet.
    assert "Nothing has changed yet" in fake_llm.last_tool_result()


def test_a_proposal_belongs_to_one_message(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)

    fake_llm.script = [text_reply("Anything else?")]
    reply = client.post("/chat", json={"message": "Thanks"}).json()

    assert reply["pending_action"] is None


def test_bad_salary_arguments_are_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)

    bad_arguments: list[dict[str, object]] = [
        {**NEW_SALARY, "amount": "0"},
        {**NEW_SALARY, "amount": "lots"},
        {**NEW_SALARY, "employee_id": 0},
        {**NEW_SALARY, "effective_date": "soon"},
        {"amount": "52000", "effective_date": "2027-01-01"},  # whose salary?
        {**NEW_SALARY, "confirmed": True},                    # an unknown argument
    ]
    for bad in bad_arguments:
        reply = propose_salary(client, fake_llm, bad)
        assert "wrong arguments" in fake_llm.last_tool_result()
        assert reply["pending_action"] is None

    assert crud_posts(fake_api) == []


def test_salary_changes_are_only_offered_with_the_permission(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})  # a plain employee

    reply = propose_salary(client, fake_llm)

    assert "no tool with that name" in fake_llm.last_tool_result()
    assert reply["pending_action"] is None
    assert TOOL_PERMISSIONS["update_employee_salary"] == "change_salary"


# ---------- Only the user can confirm ----------

def test_the_confirm_button_runs_the_change(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)

    response = client.post("/actions/501/confirm")

    assert response.status_code == 200
    assert response.json()["message"] == "Done"
    assert fake_api.actions[501]["status"] == "done"
    # Sent as "ui": a person clicked. (The CRUD API refuses a confirm that says "agent".)
    confirm = [r for r in fake_api.requests if r.url.path == "/actions/501/confirm"][0]
    assert confirm.headers["X-Request-Source"] == "ui"


def test_the_tools_always_say_agent(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # So the CRUD API knows the PROPOSAL came from the agent, and would refuse to run it directly.
    log_in_as_hr(client, fake_api)

    propose_salary(client, fake_llm)

    proposal = [r for r in fake_api.requests if r.url.path == "/actions"][0]
    assert proposal.headers["X-Request-Source"] == "agent"


def test_the_model_has_no_way_to_confirm(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)

    # No tool can confirm, and typing "yes" is just a chat message.
    offered = [tool.metadata.get_name() for tool in fake_llm.seen_tools]
    assert not any("confirm" in name for name in offered)
    fake_llm.script = [tool_call_reply("confirm_action", {"action_id": 501}), text_reply("Done!")]
    client.post("/chat", json={"message": "yes"})

    assert "no tool with that name" in fake_llm.last_tool_result()
    assert fake_api.actions[501]["status"] == "pending"


def test_confirm_needs_login(client: TestClient) -> None:
    assert client.post("/actions/501/confirm").status_code == 401
    assert client.post("/actions/501/cancel").status_code == 401


def test_an_action_can_be_confirmed_only_once(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)
    assert client.post("/actions/501/confirm").status_code == 200

    again = client.post("/actions/501/confirm")

    # The CRUD API's own answer, passed on unchanged.
    assert again.status_code == 400
    assert again.json() == {"detail": "This action is already done"}


def test_the_cancel_button(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)

    response = client.post("/actions/501/cancel")

    assert response.status_code == 200
    assert fake_api.actions[501]["status"] == "cancelled"
    assert client.post("/actions/501/confirm").status_code == 400  # too late to confirm


def test_the_agent_is_told_what_the_user_clicked(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # So "did it work?" gets a true answer in the next message.
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)
    client.post("/actions/501/confirm")

    fake_llm.script = [text_reply("Yes, it is done.")]
    client.post("/chat", json={"message": "Did it work?"})

    seen = "\n".join(message.content or "" for message in fake_llm.seen_messages[-1])
    assert "The user clicked Confirm. This is now done: Change the salary of Zara Finance" in seen


def test_the_agent_is_told_when_confirm_fails(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # The action ran out (5 minutes) or was already used: the agent must not keep saying "still pending".
    log_in_as_hr(client, fake_api)
    propose_salary(client, fake_llm)
    fake_api.actions[501]["status"] = "expired"

    response = client.post("/actions/501/confirm")
    fake_llm.script = [text_reply("It expired. Shall I propose it again?")]
    client.post("/chat", json={"message": "Did it work?"})

    assert response.status_code == 400
    seen = "\n".join(message.content or "" for message in fake_llm.seen_messages[-1])
    assert "The user clicked Confirm, but it was NOT done: This action is already expired" in seen
