# Chat sessions: where the agent service keeps each user's login tokens.
#
# When you log in, the CRUD API gives us two tokens (access + refresh). We keep them HERE, on the
# server. The browser only gets a random "session id" in a cookie, never the tokens themselves.
# And the AI never sees them either: they are only used inside crud_api.py, to call the CRUD API.
#
# The sessions live in memory (a plain dict). That keeps the code simple, but it means:
#   - restarting the agent service logs everybody out
#   - it only works with one server process
# A real deployment would keep them in a database or Redis instead.

import secrets
from dataclasses import dataclass, field
from typing import Optional

from fastapi import Cookie, HTTPException
from llama_index.core.base.llms.types import ChatMessage

# The name of the cookie that holds the session id.
SESSION_COOKIE = "session_id"


def empty_history() -> list[list[ChatMessage]]:
    return []


@dataclass
class ChatSession:
    # repr=False: if a session is ever printed or logged, the tokens are left out.
    # print(session) shows "ChatSession()" instead of the secret tokens.
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    # The chat so far (see app/memory.py): a list of turns, each turn a list of messages.
    # Also repr=False: it holds HR data from the tool results.
    history: list[list[ChatMessage]] = field(default_factory=empty_history, repr=False)


# session id -> that user's tokens and chat
SESSIONS: dict[str, ChatSession] = {}


def create_session(access_token: str, refresh_token: str) -> str:
    """Store the tokens and return a new random session id (impossible to guess)."""
    session_id = secrets.token_urlsafe(32)
    SESSIONS[session_id] = ChatSession(access_token=access_token, refresh_token=refresh_token)
    return session_id


def delete_session(session_id: Optional[str]) -> None:
    if session_id is not None:
        SESSIONS.pop(session_id, None)


def current_session(session_id: Optional[str] = Cookie(default=None)) -> ChatSession:
    """FastAPI dependency: find the session from the browser's cookie. No valid session gives 401."""
    if session_id is None or session_id not in SESSIONS:
        raise HTTPException(status_code=401, detail="Please log in")
    return SESSIONS[session_id]
