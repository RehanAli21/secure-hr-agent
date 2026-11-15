# Gatekeeper HR — Agent service

The AI assistant for Gatekeeper HR. It sits between the chat UI and the CRUD API:

```
Chat UI (React)  -->  agent service (this folder)  -->  CRUD API (../backend)  -->  database
```

**The AI proposes, the API decides.** This service has no database password. It reaches HR data only
through the CRUD API, using the logged-in user's token, so it can never do more than that user.

It has login, logout, "who am I?" and a chat with eight read-only tools. Four are about the logged-in user:

| Tool | Answers questions like | What reaches the AI |
| --- | --- | --- |
| `get_my_profile` | "What is my job title?" | profile, **without** home address |
| `get_my_leave` | "How many sick days did I take?" | type, dates, days, status, **without** the reason |
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

**About the small local model:** the context window (how much text the model reads at once) is
`OLLAMA_CONTEXT` in `.env`, 4096 tokens by default (about 3,000 words). It wins over the "context
length" slider in the Ollama app. With a 4 GB GPU, anything bigger makes part of the model run on
the CPU, about 3 times slower; with a bigger GPU you can raise it (`.env.example` has a table, and
`ollama ps` shows where the model runs). Because the window is small, the tools send as little as
possible: compact JSON, only the fields needed, and no old leave for managers.

## How a chat message is answered

```
POST /chat "What is my job title?"
  0. The agent asks the CRUD API what this user may do (GET /me/permissions), fresh every time
  1. The model gets: the rules + the user's permissions + the message + only the tools they may use
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

The chat answer also lists the tools it used: `{"reply": "...", "tool_calls": ["get_my_profile"]}`.
Each message is answered on its own for now: the agent doesn't remember earlier messages yet.

## Run the tests

```
python -m pytest
```

The tests need neither the backend nor Ollama:
- `tests/fake_crud_api.py` pretends to be the CRUD API (using `httpx.MockTransport`) and remembers
  every request, so a test can check exactly what was sent.
- `tests/fake_llm.py` is a fake AI model that follows a script and remembers everything it was
  sent, so a test can check that the token and the home address never reach the AI.
