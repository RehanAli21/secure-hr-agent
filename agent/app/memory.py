# Chat memory: the agent remembers the last few questions and answers of a chat.
#
# Without it, every message stands alone:
#   You:   What is Yusuf Brown's job title?
#   Agent: There are two. The Data Engineer in Engineering, or the Recruiter in HR?
#   You:   The one in Engineering.
#   Agent: ...which one? (it forgot the question)
#
# What we remember: a list of TURNS. One turn = your message, the tool calls and their results,
# and the agent's answer. The tool results matter: they hold the ids the agent needs to go on.
# What we DON'T remember: the rules and your permissions. Those are rebuilt fresh every message.
#
# The model can only read a little text at once (OLLAMA_CONTEXT), so we keep only the last few
# turns, and we forget the oldest ones when the chat gets too long.
#
# The memory lives in your session, on the server: logging out, or "new chat", forgets it.

from llama_index.core.base.llms.types import ChatMessage

from app.config import OLLAMA_CONTEXT
from app.sessions import ChatSession

# Remember at most this many turns.
MAX_TURNS = 3

# And at most this many characters in total. One token is about 3 to 4 characters, so this is
# roughly a quarter of the context window. The rest is for the rules, the tools and the new message.
MAX_HISTORY_CHARACTERS = OLLAMA_CONTEXT


def size_of(turn: list[ChatMessage]) -> int:
    """About how many characters a turn takes (+100 per message for tool calls and labels)."""
    return sum(len(message.content or "") + 100 for message in turn)


def remember(session: ChatSession, turn: list[ChatMessage]) -> None:
    """Add a finished turn, then forget the oldest turns until it fits."""
    session.history.append(turn)
    session.history = session.history[-MAX_TURNS:]
    while session.history and sum(size_of(t) for t in session.history) > MAX_HISTORY_CHARACTERS:
        session.history.pop(0)


def past_messages(session: ChatSession) -> list[ChatMessage]:
    """All remembered messages, oldest first, to put before the new message."""
    return [message for turn in session.history for message in turn]


def forget(session: ChatSession) -> None:
    """Start a new chat."""
    session.history.clear()
