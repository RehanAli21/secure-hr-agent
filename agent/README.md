# Gatekeeper HR — Agent service

The AI assistant for Gatekeeper HR. It sits between the chat UI and the CRUD API:

```
Chat UI (React)  -->  agent service (this folder)  -->  CRUD API (../backend)  -->  database
```

**The AI proposes, the API decides.** This service has no database password. It reaches HR data only
through the CRUD API, using the logged-in user's token, so it can never do more than that user.

It has login, logout, "who am I?" and a chat with eight read-only tools and one that asks for leave. Four are about the logged-in user:

| Tool | Answers questions like | What reaches the AI |
| --- | --- | --- |
| `get_my_profile` | "What is my job title?" | profile, **without** home address |
| `get_my_leave` | "How many sick days did I take?" | days of approved leave per year and type (added up by the code, not the model), and the 20 newest requests: type, dates, days, status, **without** the reason |
| `get_my_salary` | "What is my current salary?" | amount and effective date |
| `get_my_reviews` | "What did my last review say?" | period, rating, comments, reviewer id |

Two are for managers (anyone with people reporting to them):

| Tool | Answers questions like | What reaches the AI |
| --- | --- | --- |
| `get_my_team` | "Who is in my team?", "What is Yusuf's job title?" | current team members, no home addresses |
| `get_team_leave` | "Who is off in December?", "What is waiting for me?" | current and upcoming leave, with names, no reasons |

None of these six takes an employee id: whose data it is always comes from the login.

Two are for HR and admins (permission `view_all_employees`):

| Tool | Answers questions like | What reaches the AI |
| --- | --- | --- |
| `search_employees` | "Who is Leila Ali's manager?", "What is Yusuf's job title?" | one match: the full profile; several: a short list to choose from (at most 10) |
| `get_employee` | "Show employee 87" | the full profile, with department and manager **names**, no home address |

**Ask, never guess:** when a name matches several people (there are two Yusuf Browns!), the agent
lists them and asks which one. It never picks one itself.

Four change something. For everyone (permissions `submit_leave` and `cancel_own_leave`):

| Tool | Used for | What it sends to the CRUD API |
| --- | --- | --- |
| `submit_leave_request` | "I want annual leave from 5 to 9 January", "Book me a sick day tomorrow" | leave type and dates only: no reason (it can be private), no employee id |
| `cancel_leave_request` | "Cancel my leave from 5 January", "Cancel my leave" | the cancel of the one request that starts on that day, found by the tool itself |

New leave is pending until the manager approves it. If the type or a date is missing, the agent asks.
Leave is cancelled **by its start date, never by an id**: the user says "my leave from 5 January",
and pending or approved leave never overlaps, so only one request can start on that day. Without a
date (or with a wrong one) the tool cancels nothing and returns only the leave that *can* still be
cancelled, so the agent can ask "which one?".

And for managers (permission `decide_reports_leave`):

| Tool | Used for | What it sends to the CRUD API |
| --- | --- | --- |
| `approve_leave_request` | "Approve Chloe's leave" | the approval of the one pending request that fits |
| `reject_leave_request` | "Reject Adam's leave request" | the rejection of the one pending request that fits |

The same idea: the model says **whose** leave (part of the name is fine) and/or the first day, never
an id. The tool looks only at the pending requests of the manager's direct reports, and acts only
when exactly one fits. Two requests from the same person, a name that matches nobody, or no details
at all: nothing is decided, and the agent gets the list of requests waiting, to ask which one.
The CRUD API still checks that the user is the direct manager and that the request is still pending.
**At most one change per message:** the tools that change something are listed in `WRITE_TOOLS`
(`app/tools.py`), and the agent refuses a second one in the same message, so a confused or tricked
model can't make many changes at once. Every change lands in the CRUD API's audit log, marked
`source = agent`.

**About the small local model:** the context window (how much text the model reads at once) is
`OLLAMA_CONTEXT` in `.env`, 4096 tokens by default (about 3,000 words). It wins over the "context
length" slider in the Ollama app. With a 4 GB GPU, anything bigger makes part of the model run on
the CPU, about 3 times slower; with a bigger GPU you can raise it (`.env.example` has a table, and
`ollama ps` shows where the model runs). Because the window is small, the tools send as little as
possible: compact JSON, only the fields needed, and no old leave for managers.

### Big changes: the user must click Confirm

HR can make four big changes in the chat, but the agent only **proposes** them: each tool sends a
pending action (`POST /actions`), and **nothing changes yet**.

| Tool | Used for | Permission |
| --- | --- | --- |
| `update_employee_salary` | "Give Leila Ali 170000 from 1 January" | `change_salary` |
| `terminate_employee` | "Terminate Chloe Miller, her last day is 31 December" | `terminate_employee` |
| `change_employee_role` | "Make Chloe Miller a manager" (a role) | `change_role` |
| `change_employee_manager` | "Chloe Miller now reports to Saad Chaudhry", "...no longer has a manager" | `update_employee` |

**Who:** these tools take a **name or an employee id**, and find the person themselves (the small
model is bad at "search first, then pass on the id"). Exactly one match: the change is proposed.
Nobody, or several people with that name (there are two Saad Chaudhrys!): nothing is proposed, and
the agent gets the choices with their ids to ask "which one?"; the answer leads to a second call with
the id.

```
You:    Give Leila Ali 170000 from 1 January 2027
Agent:  (update_employee_salary, which finds Leila Ali itself)
Reply:  {"reply": "...", "pending_action": {"id": 2, "summary": "Change the salary of Leila Ali from
         163500.00 to 170000, starting 2027-01-01.", "expires_at": "..."}}
Page:   shows the summary with Confirm and Cancel buttons
Click:  POST /actions/2/confirm  ->  the CRUD API runs the change
```

- The **summary is written by the CRUD API** from the real records, never by the AI.
- **Only a click confirms.** Typing "yes" is just a chat message. The model has no tool that confirms,
  and the CRUD API refuses to confirm anything sent by the agent (`X-Request-Source: agent`); only the
  Confirm and Cancel endpoints (`app/routers/actions.py`) send `ui`.
- The CRUD API also checks: it is your action, not older than 5 minutes, used only once, and the record
  did not change since the preview (409). After a click, the agent is told what happened.

## How a chat message is answered

```
POST /chat "What is my job title?"
  0. The agent asks the CRUD API what this user may do (GET /me/permissions), fresh every time
  1. The model gets: the rules + the user's permissions + the last few turns of the chat
     + the message + only the tools they may use
  2. The model asks: "call get_my_profile"
  3. The agent checks the tool name and its arguments, then runs it:
       get_my_profile -> CRUD API /me and /employees/{id}, with the user's own token
  4. Only allowed fields go back to the model (no home address), marked as <tool_result> data
  5. The model answers: "You are an Executive Assistant."
```

- The model **can't see the token**: tools are built inside `make_tools(session)`, so they use the
  session without it ever being a parameter the model sees.
- A made-up tool name or argument is refused before anything runs.
- At most 8 tool calls per message, so a confused model can't loop forever.
- **Live permissions:** because they are fetched on every message, a role change counts from the next
  message. The model uses them to answer "what can I change?", and each tool is only offered to users
  with the permission it needs (`TOOL_PERMISSIONS` in `app/tools.py`). That filter is only for a better
  experience: the CRUD API still checks every call.

## Chat memory

The agent remembers the last few turns of the chat (`app/memory.py`), so follow-up questions work:

```
You:   What is Yusuf Brown's job title?
Agent: There are two: a Data Engineer in Engineering and a Recruiter in HR. Which one do you mean?
You:   The one in Engineering.
Agent: He is a Data Engineer.
You:   Who is his manager?
Agent: Leila Ali.
```

- One turn = your message, the tool calls with their results, and the answer. The tool results are
  kept on purpose: they hold the ids the agent needs for "the one in Engineering".
- At most 3 turns, and at most `OLLAMA_CONTEXT` characters (about a quarter of the context window).
  The oldest turns are forgotten first.
- The rules and your permissions are **not** remembered: they are rebuilt fresh for every message.
- The memory lives in your session on the server: `POST /chat/new` or logging out forgets it.
  A message that fails (for example, Ollama is down) is not remembered.

## How login works

```
1. Login form  --email + password-->  agent service  --->  CRUD API /auth/login
2.                                    agent service  <---  access + refresh token
3. Browser  <--cookie: session_id (random)--  agent service keeps the tokens in its memory
4. Browser  --cookie-->  agent service  --Authorization: Bearer <token>-->  CRUD API
```

- The **tokens never leave the agent service**: the browser only gets a random session id, in an
  `httponly` cookie that JavaScript can't read. Later, the AI won't see the tokens either.
- The access token lasts 15 minutes. When it runs out, the agent service gets a new one with the
  refresh token by itself, so the user stays logged in (for up to 7 days).
- Sessions live in memory: **restarting the agent service logs everyone out.**
- Only normal users can log in here. Superusers use the CRUD API directly.

## What you need

- Python 3.10 or newer
- The CRUD API from `../backend`, running (see its README)
- [Ollama](https://ollama.com), with the model downloaded:

  ```
  ollama pull qwen3:4b-instruct
  ```

  Use an "instruct" model: it answers right away. A "thinking" model (like plain `qwen3:4b`) writes a
  long explanation to itself first, so every answer takes minutes. It runs well on a 4 GB GPU.

## First-time setup

Run every command from the `agent` folder.

**1. Create a virtual environment and install the packages:**

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS/Linux, activate it with `source .venv/bin/activate` instead.

**2. Create your `.env` file:**

```
copy .env.example .env
```

`CRUD_API_URL` is where the backend runs. The default, `http://localhost:8000`, is right if you started
the backend with its normal command.

## Run the agent service

Start the backend first (in its own terminal), then:

```
python -m uvicorn app.main:app --reload --port 8001
```

It runs on port **8001**, because the backend already uses 8000.

Check it at http://localhost:8001/health:

- `{"status": "ok", "crud_api": "ok"}`: everything is running.
- `{"status": "ok", "crud_api": "unreachable"}`: this service is fine, but the backend is not running
  (or `CRUD_API_URL` is wrong).

## Try logging in

Use the docs page at http://localhost:8001/docs (it keeps the cookie for you), or curl:

```
curl -c cookies.txt -H "Content-Type: application/json" -d "{\"email\":\"sara.wilson@gatekeeper.local\",\"password\":\"Password123!\"}" http://localhost:8001/auth/login
curl -b cookies.txt http://localhost:8001/me
curl -b cookies.txt -H "Content-Type: application/json" -d "{\"message\":\"What is my job title?\"}" http://localhost:8001/chat
curl -b cookies.txt -X POST http://localhost:8001/auth/logout
```

The chat answer also lists the tools it used: `{"reply": "...", "tool_calls": ["get_my_profile"], "pending_action": null}`.
To start a new chat (the agent forgets the earlier messages, you stay logged in):

```
curl -b cookies.txt -X POST http://localhost:8001/chat/new
```

## Check the real model: the tool-choice eval

The tests use a fake model, so they can't tell whether the real one picks the right tools. After
changing the system prompt, a tool's docstring or the model, run (backend and Ollama running):

```
python eval_tool_choice.py            # Sara (employee), Leila (manager) and Grace (hr)
python eval_tool_choice.py manager    # only one of them
```

It asks a fixed list of questions and checks the first tool the model picks (or that it answers
without a tool, like for "What can I change?"). Today's score with `qwen3:4b-instruct`: 37/37.
When you add a tool, add a few questions for it to `CASES`.

It also checks the **prompt budget**: the rules and every tool description are sent with every
message, before the user has typed anything. The model reads at most 4096 tokens at once, so for
each user they must stay under `PROMPT_BUDGET` (3000 tokens), leaving room for chat memory and
tool results. Today: Sara 1469, Leila 2042, Grace (hr, 16 tools) 2948. That is why the tool
docstrings are short, and why `make_tool` sends only the docstring, without the Python signature.
One catch: Ollama doesn't show the model which arguments are optional, so a docstring must say
it ("start_date is optional"), or the model asks the user for a date instead of calling the tool.

## Run the tests

```
python -m pytest
```

The tests need neither the backend nor Ollama:
- `tests/fake_crud_api.py` pretends to be the CRUD API (using `httpx.MockTransport`) and remembers
  every request, so a test can check exactly what was sent.
- `tests/fake_llm.py` is a fake AI model that follows a script and remembers everything it was
  sent, so a test can check that the token and the home address never reach the AI.
