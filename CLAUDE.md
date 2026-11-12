# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

Project name: **Gatekeeper HR**. Being built step by step, backend (CRUD API + database) first; the agent service and React frontend come later. The authoritative design is the build spec doc "Secure AI Database Assistant — Build Spec": https://claude.ai/artifact/1ADs6yz96sNXaoCd39ZMPi (read it with the Claude Docs tools, not web fetch). When the spec and this file disagree, the spec wins; update this file.

**Code style:** the owner makes tutorial videos from this code. Keep it super simple and beginner-readable: plain explicit code, short files, comments explaining the why, no clever abstractions. Build one small step at a time and stop for review; don't build several features at once.

## Commands

Backend (run from `backend/`, on Windows):

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload    # API at http://localhost:8000, docs at /docs
```

The database is PostgreSQL: during development the locally installed PostgreSQL 18 (port 5432, database `gatekeeper_hr`). The connection string is `DATABASE_URL` in `backend/.env` (gitignored; template in `.env.example`), loaded by `app/database.py`. Tables are created on startup by `Base.metadata.create_all`. There are no migrations yet: `create_all` won't alter existing tables, so after changing a column in `models.py` drop the affected tables. A Docker Compose setup for one-command install comes at the end of the project. No tests yet. The owner uses Pylance in **strict** mode: code must type-check cleanly (models use SQLAlchemy 2.0 `Mapped[...]`/`mapped_column` style; timestamps are timezone-aware UTC). To check from the terminal, put `{"include":["app"],"typeCheckingMode":"strict","venvPath":".","venv":".venv"}` in a temporary `backend/pyrightconfig.json` and run `npx pyright` from `backend/`.

## What this is

An HR assistant chatbot: employees, managers, HR staff and admins read and change HR records (employees, departments, leave requests, salaries, performance reviews) in plain language. **Core principle: the AI proposes, the API decides.** The agent never touches the database; every permission check, validation and limit lives in the CRUD API. A jailbroken or prompt-injected agent must be able to do no more than the logged-in user could already do.

## Planned stack and services

| Layer | Choice |
| --- | --- |
| Frontend (chat UI, login form, confirm dialog) | React + TypeScript |
| Agent service | Python, FastAPI, LlamaIndex |
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
