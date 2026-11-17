# Tracing: a record of what happened inside each chat message, step by step.
#
# The final answer doesn't tell you WHY the agent did something. A trace does: which tools the
# model called, with which arguments, what happened, and how long each step took. Use it to debug
# a strange answer, to see where the time goes, or to check a suspicious chat afterwards.
#
# One chat message = one line of JSON in traces/<date>.jsonl (in the agent folder), for example:
#   {"chat": "3f9c1a2b", "role": "hr", "seconds": 41.2, "outcome": "answer", "steps": [
#     {"step": "model", "seconds": 18.3, "calls": ["update_employee_salary"]},
#     {"step": "tool", "tool": "update_employee_salary", "seconds": 0.3, "result": "ok",
#      "result_size": 211, "args": {"employee": "<hidden>", "amount": "<hidden>", "effective_date": "2027-01-01"}},
#     ...]}
#
# REDACTED: a trace touches everything, so it keeps the SHAPE of what happened and never the
# private parts:
#   - never the tokens or the session cookie (the chat id is a separate random id)
#   - never the user's message, the model's answer or a tool result: only their sizes
#   - argument values only when they are in SAFE_ARGUMENTS (dates, leave types, roles);
#     everything else (names, amounts, questions) becomes "<hidden>"
# The audit log in the CRUD API is a different thing: it records every CHANGE to the data, with who
# made it. A trace records the agent's steps, including the reads and the refused calls.

import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

TRACE_FOLDER = Path(__file__).parent.parent / "traces"

# Argument values that are safe to keep: they say nothing private about anyone.
SAFE_ARGUMENTS = {"leave_type", "role", "start_date", "end_date", "effective_date", "termination_date"}

# Requests run in several threads at once: only one may write to the file at a time.
lock = threading.Lock()


def result_kind(result: str) -> str:
    """What a tool result means, without its content: "ok", "error 403", "wrong arguments"..."""
    if result.startswith("Error: there is no tool"):
        return "no such tool"
    if result.startswith("Error: wrong arguments"):
        return "wrong arguments"
    if result.startswith("Error: only one change"):
        return "refused: one change per message"
    if result.startswith("Error "):
        return "error " + result.removeprefix("Error ").split(":")[0]  # "Error 403: ..." -> "error 403"
    return "ok"


def hide_values(args: dict[str, Any]) -> dict[str, Any]:
    """Keep the argument names, but only the safe values: {"employee": "<hidden>", "role": "manager"}."""
    return {name: (value if name in SAFE_ARGUMENTS else "<hidden>") for name, value in args.items()}


def empty_steps() -> list[dict[str, Any]]:
    return []


@dataclass
class Trace:
    chat: str                    # the chat's random id (ChatSession.chat_id), to see turns of one chat
    message_size: int            # the length of the user's message (never the message itself)
    role: Optional[str] = None   # filled in once GET /me/permissions has answered
    steps: list[dict[str, Any]] = field(default_factory=empty_steps)
    outcome: str = "answer"      # changed when the message ends another way (see app/agent.py)
    started: float = field(default_factory=time.monotonic)

    def model_step(self, seconds: float, calls: list[str]) -> None:
        """The model answered: with tool calls, or (no calls) with the final answer."""
        self.steps.append({"step": "model", "seconds": round(seconds, 2), "calls": calls})

    def tool_step(self, tool: str, args: dict[str, Any] | str, seconds: float, result: str) -> None:
        # args is normally a dict, but a confused model can send plain text instead: hide it all.
        self.steps.append({"step": "tool", "tool": tool, "seconds": round(seconds, 2),
                           "result": result_kind(result), "result_size": len(result),
                           "args": hide_values(args) if isinstance(args, dict) else "<hidden>"})

    def finish(self, reply_size: int = 0, pending_action: Optional[int] = None) -> None:
        """Write the whole trace as one line of JSON."""
        line = {
            "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chat": self.chat,
            "role": self.role,
            "seconds": round(time.monotonic() - self.started, 2),
            "outcome": self.outcome,
            "message_size": self.message_size,
            "reply_size": reply_size,
            "pending_action": pending_action,  # the id of a proposal waiting for Confirm, if any
            "steps": self.steps,
        }
        TRACE_FOLDER.mkdir(exist_ok=True)
        file = TRACE_FOLDER / f"{datetime.now(timezone.utc).date().isoformat()}.jsonl"
        with lock:
            with file.open("a", encoding="utf-8") as out:
                out.write(json.dumps(line) + "\n")
