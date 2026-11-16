# Tests for the chat memory (app/memory.py): the agent remembers the last few turns of a chat.

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from llama_index.core.base.llms.types import ChatMessage, MessageRole

from app import llm, memory
from app.main import app
from app.sessions import ChatSession
from tests.fake_crud_api import EMAIL, PASSWORD, REFRESH_TOKEN, FakeCrudApi
from tests.fake_llm import FakeLLM, text_reply, tool_call_reply


def log_in(client: TestClient) -> None:
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200


def chat(client: TestClient, message: str) -> None:
    assert client.post("/chat", json={"message": message}).status_code == 200


def texts_sent_last_time(fake_llm: FakeLLM) -> list[str]:
    """The content of every message in the model's last call."""
    return [message.content or "" for message in fake_llm.seen_messages[-1]]


# ---------- Remembering ----------

def test_the_second_message_sees_the_first_question_and_answer(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    fake_llm.script = [text_reply("Which Yusuf: Engineering or HR?")]
    chat(client, "What is Yusuf's job title?")

    fake_llm.script = [text_reply("Data Engineer.")]
    chat(client, "The one in Engineering.")

    texts = texts_sent_last_time(fake_llm)
    assert texts[1:] == ["What is Yusuf's job title?", "Which Yusuf: Engineering or HR?", "The one in Engineering."]


def test_tool_results_are_remembered_too(client: TestClient, fake_llm: FakeLLM) -> None:
    # The ids and facts in tool results are what the model needs for a follow-up question.
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("You are a Developer.")]
    chat(client, "What is my job title?")

    fake_llm.script = [text_reply("Yes.")]
    chat(client, "Are you sure?")

    assert '"job_title":"Developer"' in "\n".join(texts_sent_last_time(fake_llm))


def test_there_is_only_one_system_message_built_fresh(client: TestClient, fake_llm: FakeLLM) -> None:
    # The rules and permissions are not stored in the memory: they are rebuilt for every message.
    log_in(client)
    chat(client, "Hi")
    chat(client, "Hi again")

    roles = [message.role for message in fake_llm.seen_messages[-1]]
    assert roles.count(MessageRole.SYSTEM) == 1
    assert roles[0] == MessageRole.SYSTEM


def test_only_the_last_few_turns_are_kept(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    for number in range(memory.MAX_TURNS + 2):  # questions 0, 1, 2, 3 and 4
        chat(client, f"Question {number}")

    # The last call had the 3 remembered turns (questions 1, 2, 3) plus the new question 4.
    texts = texts_sent_last_time(fake_llm)
    assert "Question 0" not in texts  # forgotten
    assert "Question 1" in texts      # the oldest one still remembered
    assert "Question 4" in texts


def test_a_long_chat_forgets_the_oldest_turns_to_fit() -> None:
    # Two turns that are each more than half the budget: only the newest one fits.
    session = ChatSession(access_token="a", refresh_token="r")
    big_text = "x" * (memory.MAX_HISTORY_CHARACTERS // 2 + 1)
    first = [ChatMessage(role=MessageRole.USER, content="first " + big_text)]
    second = [ChatMessage(role=MessageRole.USER, content="second " + big_text)]

    memory.remember(session, first)
    memory.remember(session, second)

    assert session.history == [second]


def test_a_failed_message_is_not_remembered(
    client: TestClient, fake_llm: FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_in(client)

    def ollama_is_down(messages: list[ChatMessage], tools: object) -> ChatMessage:
        raise HTTPException(status_code=503, detail="The AI model is not reachable.")

    monkeypatch.setattr(llm, "ask_llm", ollama_is_down)
    assert client.post("/chat", json={"message": "Lost question"}).status_code == 503

    monkeypatch.setattr(llm, "ask_llm", fake_llm.ask)
    chat(client, "Next question")
    assert "Lost question" not in texts_sent_last_time(fake_llm)


# ---------- Keeping chats apart, and forgetting ----------

def test_each_session_has_its_own_memory(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    chat(client, "My secret question")

    other_browser = TestClient(app)  # its own cookies, so its own session
    log_in(other_browser)
    chat(other_browser, "Hello")

    assert "My secret question" not in texts_sent_last_time(fake_llm)


def test_new_chat_forgets_the_earlier_messages(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    chat(client, "Old question")

    response = client.post("/chat/new")
    assert response.status_code == 200
    chat(client, "New question")

    assert "Old question" not in texts_sent_last_time(fake_llm)


def test_new_chat_needs_login(client: TestClient) -> None:
    assert client.post("/chat/new").status_code == 401


def test_logging_out_forgets_the_chat(client: TestClient, fake_llm: FakeLLM) -> None:
    log_in(client)
    chat(client, "Old question")
    client.post("/auth/logout")

    log_in(client)
    chat(client, "New question")

    assert "Old question" not in texts_sent_last_time(fake_llm)


def test_the_token_never_reaches_the_ai_across_turns(
    client: TestClient, fake_api: FakeCrudApi, fake_llm: FakeLLM
) -> None:
    log_in(client)
    fake_llm.script = [tool_call_reply("get_my_profile"), text_reply("Done.")]
    chat(client, "Show my profile")
    fake_llm.script = [text_reply("Done again.")]
    chat(client, "And again")

    seen = fake_llm.everything_seen()
    assert fake_api.access_token not in seen
    assert REFRESH_TOKEN not in seen
