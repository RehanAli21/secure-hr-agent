# Tests for big changes that the user must confirm (a new salary...).
#
# The agent only PROPOSES the change (POST /actions on the CRUD API). The chat reply carries the
# preview, and only the page's Confirm button (POST /actions/{id}/confirm here) runs it.

import json

import pytest
from fastapi.testclient import TestClient

from app.tools import TOOL_PERMISSIONS
from tests.fake_crud_api import EMAIL, EMPLOYEE_PERMISSIONS, PASSWORD, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply

# Zara (id 12) in the fake CRUD API. Her salary goes from 50000.00 to 52000.
NEW_SALARY: dict[str, object] = {"employee": "Zara Finance", "amount": "52000", "effective_date": "2027-01-01"}


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
        {**NEW_SALARY, "employee": ""},
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


# ---------- The other big changes: terminate, role, manager ----------
#
# They all work like the salary: the tool only proposes, with the CRUD API's action name and args.

OTHER_BIG_CHANGES = [
    # (tool, the model's arguments, what must be sent to POST /actions)
    ("terminate_employee", {"employee": "Zara Finance", "termination_date": "2026-12-31"},
     {"action": "terminate_employee", "target_id": 12, "args": {"termination_date": "2026-12-31"}}),
    ("change_employee_role", {"employee": "zara", "role": "manager"},
     {"action": "change_role", "target_id": 12, "args": {"role": "manager"}}),
    ("change_employee_manager", {"employee": "Zara Finance", "new_manager": "Tara Team"},
     {"action": "change_manager", "target_id": 12, "args": {"manager_id": 8}}),
    ("change_employee_manager", {"employee": "12", "new_manager": "8"},  # ids work too
     {"action": "change_manager", "target_id": 12, "args": {"manager_id": 8}}),
    ("change_employee_manager", {"employee": "Zara Finance", "new_manager": "nobody"},
     {"action": "change_manager", "target_id": 12, "args": {"manager_id": None}}),
]


def log_in_with(client: TestClient, fake_api: FakeCrudApi, permission: str) -> None:
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    fake_api.permissions = {**EMPLOYEE_PERMISSIONS, "role": "hr", "permissions": ["view_all_employees", permission]}


@pytest.mark.parametrize("tool, args, expected", OTHER_BIG_CHANGES)
def test_big_changes_are_only_proposed(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM,
    tool: str, args: dict[str, object], expected: dict[str, object],
) -> None:
    log_in_with(client, fake_api, TOOL_PERMISSIONS[tool])
    fake_llm.script = [tool_call_reply(tool, args), text_reply("Please click Confirm.")]

    reply = client.post("/chat", json={"message": "Do it"}).json()

    assert crud_posts(fake_api) == ["/actions"]  # nothing else: no direct change
    body = json.loads([r for r in fake_api.requests if r.url.path == "/actions"][0].content)
    assert body == expected
    assert reply["pending_action"]["id"] == 501
    assert "Zara Finance" in reply["pending_action"]["summary"]  # the CRUD API's words


@pytest.mark.parametrize("tool, bad_args", [
    ("terminate_employee", {"employee": "Zara Finance"}),                            # no last day
    ("terminate_employee", {"employee": "Zara Finance", "termination_date": "soon"}),
    ("change_employee_role", {"employee": "Zara Finance", "role": "boss"}),         # not a real role
    ("change_employee_role", {"employee": "Zara Finance", "role": "admin", "user_id": 1}),  # unknown
    ("change_employee_manager", {"employee": "Zara Finance"}),  # new_manager forgotten: NOT "no manager"
    ("change_employee_manager", {"employee_id": 12, "manager_id": 8}),  # the old ids-only arguments
])
def test_bad_big_change_arguments_are_refused(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM, tool: str, bad_args: dict[str, object]
) -> None:
    log_in_with(client, fake_api, TOOL_PERMISSIONS[tool])
    fake_llm.script = [tool_call_reply(tool, bad_args), text_reply("Sorry.")]

    reply = client.post("/chat", json={"message": "Do it"}).json()

    assert "wrong arguments" in fake_llm.last_tool_result()
    assert reply["pending_action"] is None
    assert crud_posts(fake_api) == []


# ---------- Finding the person: never a guess ----------

def test_several_people_with_the_name_proposes_nothing(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    # There are 11 "Yusuf Number..." people: the tool lists them with their ids and asks.
    log_in_with(client, fake_api, "terminate_employee")
    fake_llm.script = [tool_call_reply("terminate_employee", {"employee": "Yusuf", "termination_date": "2026-12-31"}),
                       text_reply("Which Yusuf?")]

    reply = client.post("/chat", json={"message": "Terminate Yusuf"}).json()

    assert reply["pending_action"] is None
    assert crud_posts(fake_api) == []
    result = fake_llm.last_tool_result()
    assert "Nothing was proposed: several people are called" in result
    assert '"id":100' in result and '"job_title":"Accountant"' in result  # enough to ask which one


def test_nobody_with_the_name_proposes_nothing(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_with(client, fake_api, "change_role")
    fake_llm.script = [tool_call_reply("change_employee_role", {"employee": "Nobody Known", "role": "hr"}),
                       text_reply("I can't find them.")]

    reply = client.post("/chat", json={"message": "Make Nobody Known hr"}).json()

    assert reply["pending_action"] is None
    assert crud_posts(fake_api) == []
    assert 'nobody is called "Nobody Known"' in fake_llm.last_tool_result()


def test_an_unknown_id_proposes_nothing(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_with(client, fake_api, "change_salary")
    fake_llm.script = [tool_call_reply("update_employee_salary", {**NEW_SALARY, "employee": "999"}),
                       text_reply("No such id.")]

    reply = client.post("/chat", json={"message": "Give 999 more money"}).json()

    assert reply["pending_action"] is None
    assert crud_posts(fake_api) == []
    assert "nobody has the employee id 999" in fake_llm.last_tool_result()


def test_an_unclear_new_manager_proposes_nothing(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in_with(client, fake_api, "update_employee")
    fake_llm.script = [tool_call_reply("change_employee_manager", {"employee": "Zara Finance", "new_manager": "Yusuf"}),
                       text_reply("Which Yusuf?")]

    reply = client.post("/chat", json={"message": "Zara now reports to Yusuf"}).json()

    assert reply["pending_action"] is None
    assert crud_posts(fake_api) == []
