# Tests for the chat: the agent loop (app/agent.py), the tools (app/tools.py) and POST /chat.
# The AI model is fake (tests/fake_llm.py): each test writes the script of what the model "says".

import pytest
from fastapi.testclient import TestClient
from llama_index.llms.ollama import Ollama  # pyright: ignore[reportMissingTypeStubs] (see app/llm.py)

from app import agent, llm
from app.agent import MAX_TOOL_CALLS
from tests.fake_crud_api import EMAIL, PASSWORD, REFRESH_TOKEN, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply

# The real ask_llm, saved here before the fake_llm fixture replaces it.
ORIGINAL_ASK_LLM = llm.ask_llm


def log_in(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200


def chat(client: TestClient, message: str) -> dict[str, object]:
    response = client.post("/chat", json={"message": message})
    assert response.status_code == 200
    return response.json()


def crud_paths(fake_api: FakeCrudApi) -> list[str]:
    return [request.url.path for request in fake_api.requests]


# ---------- The basics ----------

def test_chat_needs_login(client: TestClient) -> None:
    assert client.post("/chat", json={"message": "Hi"}).status_code == 401


def test_a_message_can_not_be_empty_or_huge(client: TestClient) -> None:
    log_in(client)

    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x" * 2001}).status_code == 422


def test_a_plain_answer_uses_no_tools(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [text_reply("Hi! I can help with your HR records.")]

    answer = chat(client, "Hello")

    assert answer == {"reply": "Hi! I can help with your HR records.", "tool_calls": [], "pending_action": None}


# ---------- Using a tool ----------

def test_the_agent_looks_up_my_profile_through_the_crud_api(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("You are a Developer.")]

    answer = chat(client, "What is my job title?")

    assert answer == {"reply": "You are a Developer.", "tool_calls": ["get_my_profile"], "pending_action": None}
    # The tool asked the CRUD API, with the user's own token...
    assert crud_paths(fake_api)[-2:] == ["/me", "/employees/7"]
    assert fake_api.requests[-1].headers["Authorization"] == f"Bearer {fake_api.access_token}"
    # ...and the model got the result back, marked as data.
    result = fake_llm.last_tool_result()
    assert result.startswith("<tool_result>") and result.endswith("</tool_result>")
    assert '"job_title":"Developer"' in result


def test_the_home_address_never_reaches_the_ai(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("Done.")]

    chat(client, "Show my profile")

    assert "Secret Street" not in fake_llm.everything_seen()


def test_the_token_never_reaches_the_ai(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("Done.")]

    chat(client, "Show my profile")

    seen = fake_llm.everything_seen()
    assert fake_api.access_token not in seen
    assert REFRESH_TOKEN not in seen


# ---------- The model gets something wrong ----------

def test_an_unknown_tool_is_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("run_sql", {"sql": "SELECT * FROM salaries"}), text_reply("Sorry.")]
    calls_before = len(fake_api.requests)

    chat(client, "Show all salaries")

    assert "no tool with that name" in fake_llm.last_tool_result()
    # The only call to the CRUD API was the permissions check at the start of the message.
    assert crud_paths(fake_api)[calls_before:] == ["/me/permissions"]


def test_made_up_arguments_are_refused(client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM) -> None:
    # get_my_profile takes no arguments. A model that tries to pass someone else's id is stopped.
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile", {"employee_id": 99}), text_reply("Sorry.")]
    calls_before = len(fake_api.requests)

    chat(client, "Show the profile of employee 99")

    assert "wrong arguments" in fake_llm.last_tool_result()
    assert crud_paths(fake_api)[calls_before:] == ["/me/permissions"]


def test_when_the_crud_api_says_no_the_model_is_told_why(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    fake_api.employee_status = 403
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("You may not see that.")]

    answer = chat(client, "Show my profile")

    assert "Error 403" in fake_llm.last_tool_result()
    assert answer["reply"] == "You may not see that."


def test_the_agent_stops_after_too_many_tool_calls(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile")]  # repeats forever: a confused model

    answer = chat(client, "Show my profile")

    assert answer["tool_calls"] == ["get_my_profile"] * MAX_TOOL_CALLS
    assert "too many steps" in str(answer["reply"])
    assert "already used a tool that changes something" not in str(answer["reply"])  # it only read


def test_a_message_that_takes_too_long_is_stopped(
    client: TestClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_in(client)
    monkeypatch.setattr(agent, "TURN_TIMEOUT_SECONDS", -1)  # every message is "too slow" after one step
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("You are a developer.")]

    answer = chat(client, "Show my profile")

    assert answer["tool_calls"] == ["get_my_profile"]  # the tool ran, but the model was not asked again
    assert str(answer["reply"]).startswith("Sorry, that took too long, so I stopped.")
    assert len(fake_llm.seen_messages) == 1


def test_a_stopped_message_says_that_something_changed(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Stopping must never hide a change: the user would think nothing happened, and ask again.
    log_in(client)
    monkeypatch.setattr(agent, "TURN_TIMEOUT_SECONDS", -1)
    leave: dict[str, object] = {"leave_type": "annual", "start_date": "2099-03-02", "end_date": "2099-03-06"}
    fake_llm.script = [tool_call_reply("submit_leave_request", leave), text_reply("Done.")]

    answer = chat(client, "Leave from 2 to 6 March 2099")

    assert "POST" in [r.method for r in fake_api.requests if r.url.path == "/leave"]
    assert "Before I stopped, I had already used a tool that changes something" in str(answer["reply"])


# ---------- The real model wrapper ----------

def test_when_ollama_is_not_running_chat_gives_503(
    client: TestClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Use the REAL ask_llm, pointed at a port where nothing runs.
    monkeypatch.setattr(llm, "ask_llm", ORIGINAL_ASK_LLM)
    monkeypatch.setattr(llm, "llm", Ollama(model="qwen3:4b-instruct", base_url="http://127.0.0.1:1"))
    log_in(client)

    response = client.post("/chat", json={"message": "Hi"})

    assert response.status_code == 503
