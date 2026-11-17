# Gatekeeper HR

An HR assistant chatbot: employees, managers and HR staff ask about and change HR records in plain
language. **The AI proposes, the API decides:** the AI agent can never do more than the user
who is logged in.

| Folder | What it is | Runs on |
| --- | --- | --- |
| [`backend/`](backend/README.md) | The CRUD API: every permission check, the database, the audit log | port 8000 |
| [`agent/`](agent/README.md) | The AI agent service (a local Ollama model + narrow tools; big changes need the user's Confirm click) | port 8001 |
| [`frontend/`](frontend/README.md) | The chat page (React + TypeScript) | port 5173 |

Start them in that order: backend, agent (with Ollama running), then the frontend.

[`SECURITY.md`](SECURITY.md) lists the attacks the design must survive, and the tests that prove it does.
