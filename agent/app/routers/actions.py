# The Confirm and Cancel buttons for big changes (a new salary, a termination...).
#
# The agent can only PROPOSE a big change (see "Big changes" in app/tools.py). The chat page shows
# the CRUD API's preview with two buttons, and they call these two endpoints.
#
# Why the AI can't press the buttons:
#   - These are normal endpoints for the page, NOT tools: the model has no way to call them.
#   - Tools send "X-Request-Source: agent", and the CRUD API refuses to confirm those.
#     Only these endpoints send "ui": a person clicked.
#   - Typing "yes" in the chat is just a chat message. It never confirms anything.
# The CRUD API also checks that the action is yours, not expired (5 minutes), used only once, and that
# the record did not change since the preview (409).

from fastapi import APIRouter, Depends
from llama_index.core.base.llms.types import ChatMessage, MessageRole

from app import crud_api, memory
from app.sessions import ChatSession, current_session

router = APIRouter(tags=["Confirm"])


def tell_the_agent(session: ChatSession, text: str) -> None:
    """Put a note in the chat memory, so the agent knows what happened if the user asks."""
    memory.remember(session, [ChatMessage(role=MessageRole.ASSISTANT, content=text)])


@router.post("/actions/{action_id}/confirm")
def confirm(action_id: int, session: ChatSession = Depends(current_session)):
    """The user clicked Confirm: the CRUD API runs the change now."""
    try:
        done = crud_api.call(session, "POST", f"/actions/{action_id}/confirm", source="ui")
    except crud_api.CrudApiError as error:
        # Expired, changed since the preview (409)...: tell the agent too, then pass the error on.
        tell_the_agent(session, f"(The user clicked Confirm, but it was NOT done: {error.detail})")
        raise
    summary = done["action"]["preview"]["summary"]
    tell_the_agent(session, f"(The user clicked Confirm. This is now done: {summary})")
    return {"message": "Done", "summary": summary}


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: int, session: ChatSession = Depends(current_session)):
    """The user clicked Cancel: the change will never run."""
    cancelled = crud_api.call(session, "POST", f"/actions/{action_id}/cancel", source="ui")
    summary = cancelled["preview"]["summary"]
    tell_the_agent(session, f"(The user clicked Cancel. This was NOT done: {summary})")
    return {"message": "Cancelled", "summary": summary}
