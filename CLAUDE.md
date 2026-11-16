# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Project name: **Gatekeeper HR**. Being built step by step: the backend (CRUD API + database, `backend/`) is done and tested; the agent service (`agent/`) is being built (login/logout/`/me` with server-side sessions, and `POST /chat` with live permissions and eight read-only tools: `get_my_profile`, `get_my_leave`, `get_my_salary`, `get_my_reviews`, for managers `get_my_team`, `get_team_leave`, and for hr/admin `search_employees`, `get_employee`; and chat memory of the last 3 turns); the React frontend comes later. The authoritative design is the build spec doc "Secure AI Database Assistant — Build Spec": https://claude.ai/artifact/1ADs6yz96sNXaoCd39ZMPi (read it with the Claude Docs tools, not web fetch). When the spec and this file disagree, the spec wins; update this file.

**Code style:** the owner makes tutorial videos from this code. Keep it super simple and beginner-readable: plain explicit code, short files, comments explaining the why, no clever abstractions. Build one small step at a time and stop for review; don't build several features at once.

## Commands

Backend (run from `backend/`, on Windows):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\alembic upgrade head                        # create/update tables
.venv\Scripts\python create_user.py                       # create a normal login account
.venv\Scripts\python create_superuser.py                  # create a superuser (can access everything)
.venv\Scripts\python seed.py                              # demo data: 550 employees + logins, salaries, leave, reviews (fills empty tables only; password Password123!)
.venv\Scripts\python seed.py --reset                      # wipe all demo data and reseed (keeps superusers you created yourself)
.venv\Scripts\python -m uvicorn app.main:app --reload    # API at http://localhost:8000, docs at /docs
.venv\Scripts\python -m pytest                           # run all tests (-v for names; pytest tests/test_x.py::test_name for one test)
```

Tests (`backend/tests/`, pytest + FastAPI `TestClient`): `conftest.py` points `DATABASE_URL` at `TEST_DATABASE_URL` (a separate `gatekeeper_hr_test` DB; it refuses to run unless the name ends in `_test`) **before importing `app`**, runs `alembic upgrade head` once per session, and the `db` fixture TRUNCATEs every table before each test; `client` depends on `db`. Test data is created with plain functions in `tests/helpers.py` (`create_employee`, `create_login`, `create_superuser`, `log_in`; one shared bcrypt hash for speed). Tests must also pass strict Pyright (annotate fixtures and test parameters).

Agent service (run from `agent/`; its own `.venv` and `.env` with `CRUD_API_URL`, no database settings):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8001   # backend must run on 8000 first
.venv\Scripts\python -m pytest                                      # tests don't need the backend
```

`app/config.py` reads settings; `app/main.py` is the FastAPI app (a `CrudApiError` handler passes the CRUD API's status + detail through unchanged). `app/crud_api.py` is the only place that calls the CRUD API and the only code that touches tokens: one module-level `httpx.Client` (`http`, 10 s timeout); `call(session, method, path, json)` sends `Authorization: Bearer` + `X-Request-Source: agent`, and on a 401 refreshes once and retries. `app/sessions.py`: in-memory `SESSIONS` dict, session id → `ChatSession(access_token, refresh_token)` with `repr=False` on both; the browser gets only a random `session_id` httpOnly cookie; `current_session` dependency → 401 "Please log in". `routers/auth.py`: `POST /auth/login` (normal users only), `POST /auth/logout`, `GET /me`. Tests use `tests/fake_crud_api.py` (`FakeCrudApi` behind `httpx.MockTransport`, records `requests`; `expire_access_token()`, `refresh_works`, `employee_status`), swapped in by the `fake_api` fixture, which also clears `SESSIONS`.

The chat: `app/llm.py` holds the LlamaIndex `Ollama` LLM (default `qwen3:4b-instruct`, temperature 0, 8k context) and `ask_llm(messages, tools)`, the only function that calls it (Ollama down → 503). Use an instruct model: `qwen3:4b` is the always-thinking 2507 variant (minutes per answer), and llama-index-llms-ollama 0.11 drops `thinking=False` (`False or None`). `app/tools.py`: `make_tools(session)` builds `FunctionTool`s as closures over the session, so the token is never a parameter the model sees; each tool has an explicit Pydantic `fn_schema` with `extra="forbid"` (`NoArgs`) and a field allowlist (`PROFILE_FIELDS` without `home_address`, `LEAVE_FIELDS` without `reason`, `SALARY_FIELDS`, `REVIEW_FIELDS` with comments). "My" tools never take an employee id: `my_employee_id()` asks `GET /me`. Lessons from real runs with the 4B model: keep tool arguments to a minimum (an optional `status` filter on leave made it filter wrongly, so it was removed); pre-compute what needs arithmetic (`calendar_days` on leave); tool docstrings should say "use it for ANY question about ..." with varied examples; tell it amounts have no currency; the system prompt includes today's date (for current salary vs a future raise). `crud_api.call` takes optional `params` (query string). HR tools (permission `view_all_employees`): `search_employees(name)` (`NameSearch`: 2–100 chars) asks `GET /employees?name=&limit=11`; 0 matches → a sentence, 1 match → `{"one_match": full_profile}` (the 4B model does not chain search → get_employee, even when the docstring tells it to), 2+ → `{"several_matches": [SEARCH_FIELDS + department name]}` plus a plain-English `note` when there are more than 10 (it ignored a true/false flag). `get_employee(employee_id)` (`EmployeeId`: int > 0) → `full_profile` = `EMPLOYEE_FIELDS` (no `home_address`) with `department` and `manager_name` filled in by code. The prompt says: several matches → list and ask, never pick or guess an id; say a name when the result has one (e.g. `manager_name`), else the id. Manager tools: `team_of(manager_id)` = `GET /employees?manager_id=` keeping only active people; `get_team_leave` = `GET /leave?manager_id=&limit=30`, keeping only leave ending today or later, with `employee_name` added in code. **Context budget:** `OLLAMA_CONTEXT` in `.env` (default 4096, must be digits; `config.py`), passed as `context_window` in `llm.py` and sent as `num_ctx` on every request, so it overrides the Ollama app's context slider (the owner's is 64k: `OLLAMA_CONTEXT_LENGTH:65536`). KV cache for qwen3:4b-instruct is ~0.14 MB per token (36 layers × 8 KV heads × 128 × 2 × fp16) (8192 made qwen3:4b-instruct 4.2 GB, half on CPU, ~3x slower on the 4 GB GTX 1650; even 4096 is ~34% CPU); a prompt over the window gives Ollama `ResponseError` "exceeds the available context size", shown as 503 "The AI model could not answer: ...". So tool results use `as_json` (no spaces) and minimal fields (no `decided_at` on leave). Prompt rules added after measuring tool choice: HR facts are always answered with a tool, never from `<permissions>`; a named person is looked up with a tool first. Measure prompt/tool changes with `eval_tool_choice.py` (agent folder; needs backend + Ollama; `python eval_tool_choice.py [employee|manager|hr]`): it logs in as Sara/Leila/Grace, builds the real `agent.rules_message(...)` and role-filtered tools, and checks the first tool picked per question (`None` = no tool); add a case when adding a tool. Score: 24/24 (the manager set was 8/10 before the two rules above). `app/agent.py`: `run_turn` first calls `GET /me/permissions` (every message, never cached), puts `permissions_text(...)` (role + plain-English descriptions inside `<permissions>` tags) after `SYSTEM_PROMPT`, and passes the permission names to `make_tools(session, permissions)`, which only returns tools whose `TOOL_PERMISSIONS` entry is in the list (a tool missing from `TOOL_PERMISSIONS` is never offered; a hidden tool the model calls anyway gets "no tool with that name"). The prompt tells the model to repeat matching permission lines as written (it misstated HR's list when allowed to paraphrase). Then the loop (`MAX_TOOL_CALLS = 8`); `run_tool` refuses unknown tools and validates args before running, and turns `CrudApiError` into "Error <code>: <detail>" for the model; results are wrapped in `<tool_result>` tags; the system prompt says results are data, and that ids are numbers, never names (small models invent a manager's name otherwise). Tests use `tests/fake_llm.py` (`FakeLLM.script` of `text_reply`/`tool_call_reply`, records everything sent; `everything_seen()`, `last_tool_result()`) via the `fake_llm` fixture; `client` depends on both fakes. **Chat memory** (`app/memory.py`): `ChatSession.history` (`repr=False`) is a list of turns, one turn = the user message, the assistant tool-call messages, the `<tool_result>` messages and the final answer (tool results are kept: follow-ups need their ids). `run_turn` sends `[rules, *memory.past_messages(session), *turn]` and calls `memory.remember` only when a turn finishes (an exception, e.g. Ollama 503, remembers nothing); the system message is rebuilt every turn, never stored. `remember` keeps `MAX_TURNS = 3` and then drops the oldest turns while `size_of` (characters + 100 per message) is over `MAX_HISTORY_CHARACTERS = OLLAMA_CONTEXT`. `POST /chat/new` and logout forget it. Measured live: a 4-message HR follow-up chat stayed at ~2.1k prompt tokens. Tests: `tests/test_memory.py`. The `llama_index.llms.ollama` import needs `# pyright: ignore[reportMissingTypeStubs]` (the package has no `py.typed`).

Migrations (Alembic, run from `backend/`):

```
.venv\Scripts\alembic revision --autogenerate -m "describe the change"   # after editing app/models.py
.venv\Scripts\alembic upgrade head        # apply all migrations
.venv\Scripts\alembic downgrade -1        # undo the last one
```

The database is PostgreSQL: during development the locally installed PostgreSQL 18 (port 5432, database `gatekeeper_hr`). The connection string is `DATABASE_URL` in `backend/.env` (gitignored; template in `.env.example`), loaded by `app/database.py`. Alembic owns the schema: the app never calls `create_all`. `alembic/env.py` reuses the app's `engine` and `models.Base.metadata` (there is no URL in `alembic.ini`, and offline/`--sql` mode is not supported). Always read an autogenerated migration in `alembic/versions/` before applying it: autogenerate does not detect `CheckConstraint`s added to an existing table, so add those by hand (`op.create_check_constraint` / `op.drop_constraint`), as in the `add_termination_date` and `add_role_to_users` migrations. Also check autogenerated foreign keys have a name: `create_pending_actions` had `None`, which breaks `downgrade`.

Auth (`app/security.py`): bcrypt password hashes; HS256 JWTs signed with `JWT_SECRET_KEY` from `.env`, with a `type` claim (`access` 15 min / `refresh` 7 days) so one can't be used as the other, and a `kind` claim (`user` → `users` table, `superuser` → `super_users` table) because ids overlap between the two tables. Superusers (email + password only, no employee link) log in at `/auth/superuser/login` and must pass every permission check (use `is_superuser(account)` first in step-4 checks). `get_current_user` returns `Account = Union[User, SuperUser]`, reloads it from the DB on every request and rejects inactive users; routers are protected with `dependencies=[Depends(get_current_user)]`. Terminating an employee sets their user's `is_active = False`.

Backend rules (all in `app/`):
- **Permissions** live in `permissions.py`. `users.role` is employee/manager/hr/admin (DB check constraint); `require_role(account, HR_ROLES)` → 403. Row scope comes from `can_see_employee` / `only_visible_employees`: manager scope is direct reports via the live `manager_id`. An employee the caller may not see gives 404, not 403, so ids can't be probed. Leave and reviews reuse employee visibility. Approving leave and writing reviews need the live direct manager (any role). Sensitive fields are blanked in output helpers: leave `reason` and `home_address` for anyone but the owner and hr/admin. Salaries are owner + hr/admin only (a manager gets 403 for a report).
- **No self-service escalation**: nobody approves their own leave, reviews themselves, sets their own salary or changes their own role; only admins grant or remove `admin`. Ids of "who is acting" (employee_id, reviewer_id, approver) always come from the token, never the body.
- **Audit log** (`audit.py`): every write endpoint takes `origin: Origin = Depends(get_origin)`, snapshots `before`, flushes, calls `record_audit(...)`, then commits once, so the change and its audit row land together. `snapshot` drops `password_hash` and keeps money as strings. `X-Request-Source: agent` marks agent calls. Read via `GET /audit-log` (hr/admin).
- **Soft deletes**: departments and performance reviews have `deleted_at`, with `DELETE` + `/restore` endpoints (hr/admin) and `include_deleted=true` for hr/admin. The one-review-per-period rule is a partial unique index (`WHERE deleted_at IS NULL`). Salaries and reviews have no PATCH; employees are terminated, never deleted.
- **Confirmation** (`pending_actions.py`, `routers/actions.py`): terminate, salary, role, manager change, delete department and delete review call `require_confirmation(origin)`, which blocks agent-sourced calls unless they run from a confirmed pending action. `POST /actions` (hr/admin) validates args with the real endpoint's schema and stores a backend-built preview plus a `target_version` snapshot. `POST /actions/{id}/confirm` is owner-only, single-use, expires after 5 min, rejects the agent, returns 409 if the snapshot changed, and runs the real endpoint function (so every check runs again) with `pending_action_id` in the audit row. Multi-step actions and bulk updates are not built.
- **List filters**: `GET /employees` and `GET /leave` take `manager_id` (that manager's direct reports), applied after the visibility filter so it can only narrow the result. Seeing your direct reports follows the live `manager_id` for any role (`can_see_employee` / `only_visible_employees`), matching `/me/permissions`, which gives manager permissions to anyone with active reports; the `manager` role name grants nothing extra.
- **Permission descriptions**: every permission name has a plain-English entry in `PERMISSION_DESCRIPTIONS` (`permissions.py`, a test checks none is missing); `GET /roles` and `GET /me/permissions` return them as `descriptions: {name: text}`. Keep them true to the real rules: the agent repeats them to users.
- The seed gives HR staff `hr`, the Head of IT `admin` and anyone with reports `manager`, plus deterministic phone/address per employee.

Not built yet: rate limits, `Idempotency-Key`, bulk caps, least-privilege DB role; the automated tests (130) cover every router: auth, employees, leave, salaries, reviews, roles, audit log, soft deletes and pending actions (`tests/test_*.py`; most use the shared `org` fixture from `conftest.py` / `helpers.create_org`: Hana hr, Mona manager, Ravi her report, Omar a stranger; `helpers.as_agent(headers)` adds `X-Request-Source: agent`). Use 2099 dates where a rule compares with today. When adding a rule, add a test, and check it by breaking the rule on purpose: the test must fail. A Docker Compose setup for one-command install comes at the end of the project. The owner uses Pylance in **strict** mode: code must type-check cleanly (models use SQLAlchemy 2.0 `Mapped[...]`/`mapped_column` style; timestamps are timezone-aware UTC). To check from the terminal, put `{"include":["app"],"typeCheckingMode":"strict","venvPath":".","venv":".venv"}` in a temporary `backend/pyrightconfig.json` and run `npx pyright` from `backend/`.

## What this is

An HR assistant chatbot: employees, managers, HR staff and admins read and change HR records (employees, departments, leave requests, salaries, performance reviews) in plain language. **Core principle: the AI proposes, the API decides.** The agent never touches the database; every permission check, validation and limit lives in the CRUD API. A jailbroken or prompt-injected agent must be able to do no more than the logged-in user could already do.

## Planned stack and services

| Layer | Choice |
| --- | --- |
| Frontend (chat UI, login form, confirm dialog) | React + TypeScript |
| Agent service | Python, FastAPI, LlamaIndex |
| LLM | Local models through Ollama (`http://localhost:11434`), via LlamaIndex's Ollama integration; no cloud LLM API keys |
| CRUD API (the security boundary; issues JWT access + refresh tokens) | Python, FastAPI, SQLAlchemy, Pydantic |
| Database (app data, pending actions, audit log; pgvector when RAG is on) | PostgreSQL |
| Local setup | Docker Compose |

The agent service and CRUD API are separate services. The agent service has **no database credentials for app data**; it reaches the CRUD API over HTTP only. The CRUD API connects with a least-privilege DB role (no DDL).

## Request flow

1. User logs in via the frontend form (never credentials in chat). The agent service keeps the JWT in the session.
2. Each user turn: agent service calls `GET /me/permissions`, loads policy docs, filters the tool list by role (UX only), runs the LLM.
3. LLM requests a tool call → agent service validates args against the tool's Pydantic schema.
4. Read tool → agent service calls the CRUD API with `Authorization: Bearer <user token>`; result returned to the LLM wrapped as untrusted data.
5. Write tool tagged as needing confirmation → agent service stores a pending action instead of calling the API (see below).
6. CRUD API verifies token, role, ownership/row-level scope and limits, writes, and records an audit entry.

## Invariants (do not break these)

**Identity and tokens**
- The token never appears in LLM context, prompts, tool arguments or logs (a test checks this).
- The CRUD API reads `user_id`, `role`, `tenant_id` only from the verified token. No tool accepts them as arguments; if the model sends them they are ignored.
- Agent actions always run as the logged-in user, never a shared service account.

**Tools**
- Narrow verb-noun tools, one per business action (e.g. `get_employee`, `approve_leave_request`, `update_employee_salary`, `terminate_employee`). Never add `run_sql`, `update_record(table, fields)` or any generic query/update tool.
- Each tool has a typed Pydantic schema (real enums, validated IDs, hard caps on list limits), a `read`/`write` tag in the registry, a field allowlist for what reaches the LLM, and a role-to-tools allowlist entry.
- Whether confirmation applies is decided by the registry tag, never by the model.
- Sensitive fields (salary, bank details, national ID, home address, leave reasons, review comments) are masked/dropped before reaching the LLM unless the tool needs them and the role allows it.
- Tool results and RAG chunks are wrapped in delimited blocks and treated as data, not instructions.
- A record referenced by name must be resolved via a search/list tool; on multiple matches the agent asks the user, never guesses an ID.
- Loop limits: 8–10 tool calls per turn plus a per-turn timeout. On 403/422 the agent explains; it does not retry with different arguments.

**Confirmation flow** (always for terminations, deletes, bulk updates, and changes to salary, role, manager or employment status)
- Pending action stores id, user_id, tool name, exact args, target record version/`updated_at`, created_at, expires_at (~5 min), status.
- Preview is built by the backend from real API reads (bulk: count + first N records), never from the LLM's description.
- Only `POST /actions/{id}/confirm` from the UI executes, with a valid session. Typing "yes" in chat never confirms. Actions are single-use, user-bound, expire, and execute exactly the stored args.
- If the record changed since preview, the API returns 409 and the UI shows a fresh preview.
- Multi-step requests become one pending action with a list of steps, run in a single DB transaction.

**CRUD API guardrails**
- Strict request schemas (reject unknown fields), role check then ownership/tenant check on every endpoint.
- Audit log on every write: user_id, endpoint, args, before/after, timestamp, `source = agent | ui`, plus `pending_action_id` for agent writes.
- Soft deletes (`deleted_at`) only, plus a restore tool; bulk row caps; per-user rate limits (stricter on writes); `Idempotency-Key` header on writes.

**HR domain rules (enforced in the API)**
- Nobody approves their own leave or writes their own review.
- Manager scope is computed from `manager_id` on every request, never cached, never taken from the agent.
- Salary changes insert a new `salaries` row; history is never overwritten.
- Termination sets status to terminated and disables login; no hard deletes.

**RAG / policy docs**
- Permissions always come live from the API (`/me/permissions`, `get_roles()` → `GET /roles`); never index permission data.
- The index holds policy docs only, each chunk tagged `visible_to_roles` and filtered by the user's role. RAG explains; it never decides what is allowed and is never a source of records.
- While docs are small they go straight into the system prompt behind the same interface, so switching to vector retrieval is a config change.

## Roles (default)

| Role | Can see | Can change |
| --- | --- | --- |
| employee | Own profile, leave, reviews, salary | Own contact details; submit/cancel own leave |
| manager | Direct reports' profiles, leave, reviews; no salaries | Approve/reject reports' leave; write reviews for reports |
| hr | All employees incl. salaries | Create/update employees, salary, role, department; terminate |
| admin | Everything | Roles, permissions, system settings |

## Build order

Each milestone is done only when its acceptance criteria in the spec pass:
1. CRUD API with auth, role/ownership checks, audit log, soft deletes.
2. Agent service with read-only tools and role-filtered tool list.
3. Write tools + confirmation flow.
4. Live permissions + policy docs (role-filtered retrieval).
5. Hardening: the spec's security test cases (automated), rate limits, bulk caps, idempotency, version checks, loop limits, eval suite (30–50 requests → expected tool calls, incl. ambiguous cases) in CI, redacted tracing.

## Out of scope for v1

Model-generated SQL/analytics, DDL via chat, agents running without a logged-in user, step-up re-auth on confirm, exposing the CRUD API as an MCP server (keep the tool registry designed so this can be added later).
