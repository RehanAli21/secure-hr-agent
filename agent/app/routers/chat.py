# The chat endpoints: the chat UI sends one message, and gets the assistant's answer back.
# The agent remembers the last few messages of the chat (app/memory.py), until "new chat" or logout.

from typing import Literal, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app import memory
from app.agent import run_turn
from app.sessions import ChatSession, current_session

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)  # a cap, so nobody sends a whole book


class PendingAction(BaseModel):
    """A big change waiting for the user: the page shows the summary with a Confirm button."""
    id: int
    summary: str     # written by the CRUD API from the real data, never by the AI
    expires_at: str  # confirm before this time (5 minutes)


class ChartPoint(BaseModel):
    label: str    # e.g. "Engineering" or "2026-03"
    value: float


class Chart(BaseModel):
    """Numbers for the page to draw, straight from the CRUD API (never written by the AI)."""
    title: str                    # e.g. "Average current salary of active employees (no currency)"
    details: str                  # e.g. "by department"
    kind: Literal["bar", "line"]  # line for months and years, bars for everything else
    points: list[ChartPoint]


class ChatReply(BaseModel):
    reply: str
    tool_calls: list[str]  # which tools the agent used, e.g. ["get_my_profile"]
    pending_action: Optional[PendingAction] = None
    chart: Optional[Chart] = None


@router.post("/chat", response_model=ChatReply)
def chat(data: ChatRequest, session: ChatSession = Depends(current_session)):
    """Send one message. You must be logged in: the agent works as YOU, with your permissions."""
    result = run_turn(session, data.message)
    pending = PendingAction(**result.pending_action) if result.pending_action else None
    chart = Chart(**result.chart) if result.chart else None
    return ChatReply(reply=result.reply, tool_calls=result.tool_calls, pending_action=pending, chart=chart)


@router.post("/chat/new")
def new_chat(session: ChatSession = Depends(current_session)):
    """Start a new chat: the agent forgets the earlier messages (you stay logged in)."""
    memory.forget(session)
    return {"message": "Started a new chat"}
