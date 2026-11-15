# Tests for live permissions: the agent asks the CRUD API what the user may do on every message,
# tells the model, and only offers the tools the user may use.

from fastapi.testclient import TestClient

from tests.fake_crud_api import EMAIL, NO_PERMISSIONS, PASSWORD, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply


def log_in(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200


def chat(client: TestClient, message: str) -> None:
    assert client.post("/chat", json={"message": message}).status_code == 200


def test_permissions_are_fetched_on_every_message(client: TestClient, fake_api: FakeCrudApi) -> None:
    log_in(client)

    chat(client, "Hi")
    chat(client, "Hi again")

    paths = [request.url.path for request in fake_api.requests]
    assert paths.count("/me/permissions") == 2  # once per message: never cached


def test_the_model_is_told_what_the_user_may_do(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)

    chat(client, "What can I change?")

    prompt = fake_llm.system_prompt()
    assert "<permissions>" in prompt
    assert "Role: employee" in prompt
    assert "Ask for leave (annual, sick, unpaid or parental)" in prompt


def test_a_role_change_counts_on_the_next_message(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    chat(client, "What can I do?")
    assert "Role: employee" in fake_llm.system_prompt()

    # HR makes this user a manager. No new login needed.
    fake_api.permissions = {**fake_api.permissions, "role": "manager"}
    chat(client, "What can I do now?")

    assert "Role: manager" in fake_llm.system_prompt()


def test_tools_the_user_may_not_use_are_not_offered(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_api.permissions = NO_PERMISSIONS  # no "view_own_profile"

    chat(client, "Show my profile")

    assert fake_llm.seen_tools == []


def test_a_hidden_tool_called_anyway_is_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # The model may still try to call a tool it was not offered (it may have seen it in an
    # earlier answer, or just guess the name). It must not run.
    log_in(client)
    fake_api.permissions = NO_PERMISSIONS
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("Sorry.")]

    chat(client, "Show my profile")

    assert "no tool with that name" in fake_llm.last_tool_result()
    assert "/employees/7" not in [request.url.path for request in fake_api.requests]
