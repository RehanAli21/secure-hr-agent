# Tool-choice eval: does the REAL model pick the right tool for each question?
#
# The tests (tests/) use a fake model, so they can't tell us whether the real model is any good.
# This script asks the real model (Ollama) a fixed list of questions, as three demo users, and
# checks only the FIRST thing it does: which tool does it call, or does it answer without a tool?
# It never runs the tools, so it changes nothing (not even for "I want annual leave...").
#
# Use it after changing the system prompt, a tool's docstring or the model, and compare the score.
#
# It needs the backend (with the seed data) and Ollama running. From the agent folder:
#     .venv\Scripts\python eval_tool_choice.py            # all three users
#     .venv\Scripts\python eval_tool_choice.py manager    # only one of them

import sys

from llama_index.core.base.llms.types import ChatMessage, MessageRole, ToolCallBlock
from llama_index.core.tools import FunctionTool

from app import agent, crud_api, llm
from app.sessions import ChatSession
from app.tools import make_tools

PASSWORD = "Password123!"  # every seeded demo user has this password

# For each demo user: (question, the tool the model should call first). None = answer without a tool.
CASES = {
    "employee": ("sara.wilson@gatekeeper.local", [
        ("What is my job title?", "get_my_profile"),
        ("Who is my manager?", "get_my_profile"),
        ("How many sick days did I take this year?", "get_my_leave"),
        ("When was my last annual leave?", "get_my_leave"),
        ("What is my current salary?", "get_my_salary"),
        ("What did my last review say?", "get_my_reviews"),
        ("What can I change?", None),
        ("Who is in my team?", None),  # she has no team: no team tool is offered to her
        ("I want annual leave from 5 to 9 January 2027", "submit_leave_request"),
        ("I want some time off", None),  # the type and dates are missing: it must ask
        ("Cancel my leave from 5 January 2027", "cancel_leave_request"),
        ("Cancel my leave", "cancel_leave_request"),  # without a date: lists the choices
    ]),
    "manager": ("leila.ali@gatekeeper.local", [
        ("What is my job title?", "get_my_profile"),
        ("Who is in my team?", "get_my_team"),
        ("What is Yusuf's job title?", "get_my_team"),
        ("Who is off in December?", "get_team_leave"),
        ("Are there leave requests waiting for me?", "get_team_leave"),
        ("How many sick days did I take this year?", "get_my_leave"),
        ("What is my salary?", "get_my_salary"),
        ("What did my last review say?", "get_my_reviews"),
        ("What can I change?", None),
        ("What is Chloe's salary?", None),  # managers may not see their reports' salaries
        ("Approve Chloe's leave", "approve_leave_request"),
        ("Reject Adam's leave request", "reject_leave_request"),
    ]),
    "hr": ("grace.raza@gatekeeper.local", [
        ("Who is Leila Ali's manager?", "search_employees"),
        ("What is Yusuf Brown's job title?", "search_employees"),
        ("When did Leila Ali start?", "search_employees"),
        ("Show employee 87", "get_employee"),
        ("What is my salary?", "get_my_salary"),
        ("What can I change?", None),
    ]),
}


def first_tool(rules: ChatMessage, question: str, tools: list[FunctionTool]) -> str | None:
    """Ask the model one question and return the name of the first tool it calls (None = no tool)."""
    answer = llm.ask_llm([rules, ChatMessage(role=MessageRole.USER, content=question)], tools)
    calls = [block.tool_name for block in answer.blocks if isinstance(block, ToolCallBlock)]
    return calls[0] if calls else None


def run(user: str) -> tuple[int, int]:
    """Run one user's questions. Returns (passed, total)."""
    email, cases = CASES[user]
    access_token, refresh_token = crud_api.log_in(email, PASSWORD)
    session = ChatSession(access_token=access_token, refresh_token=refresh_token)

    # Exactly what the real chat sends: the same rules and the same role-filtered tools.
    my_permissions = crud_api.call(session, "GET", "/me/permissions")
    rules = agent.rules_message(my_permissions)
    tools = make_tools(session, my_permissions["permissions"])

    print(f"\n== {user} ({email}) ==")
    passed = 0
    for question, expected in cases:
        picked = first_tool(rules, question, tools)
        if picked == expected:
            passed += 1
            print(f"PASS  {question!r}: {picked}")
        else:
            print(f"FAIL  {question!r}: picked {picked}, expected {expected}")
    print(f"{passed}/{len(cases)}")
    return passed, len(cases)


def main() -> None:
    users = sys.argv[1:] or list(CASES)
    for user in users:
        if user not in CASES:
            sys.exit(f"Unknown user {user!r}. Choose from: {', '.join(CASES)}")
    total_passed = 0
    total = 0
    for user in users:
        passed, count = run(user)
        total_passed += passed
        total += count
    print(f"\nTotal: {total_passed}/{total}")


if __name__ == "__main__":
    main()
