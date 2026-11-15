# The chat endpoint: the chat UI sends one message, and gets the assistant's answer back.

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.agent import run_turn
from app.sessions import ChatSession, current_session

router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1, max_length=2000)  # a cap, so nobody sends a whole book


class ChatReply(BaseModel):
    reply: str
    tool_calls: list[str]  # which tools the agent used, e.g. ["get_my_profile"]


@router.post("/chat", response_model=ChatReply)
def chat(data: ChatRequest, session: ChatSession = Depends(current_session)):
    """Send one message. You must be logged in: the agent works as YOU, with your permissions."""
    result = run_turn(session, data.message)
    return ChatReply(reply=result.reply, tool_calls=result.tool_calls)
