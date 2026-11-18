# Gatekeeper HR

An HR assistant chatbot: employees, managers and HR staff ask about and change HR records in plain
language. **The AI proposes, the API decides:** the AI agent can never do more than the user
who is logged in.

| Folder | What it is | Runs on |
| --- | --- | --- |
| [`backend/`](backend/README.md) | The CRUD API: every permission check, the database, the audit log | port 8000 |
| [`agent/`](agent/README.md) | The AI agent service (a local Ollama model + narrow tools; big changes need the user's Confirm click) | port 8001 |
| [`frontend/`](frontend/README.md) | The chat page (React + TypeScript) | port 5173 |

## Run everything with Docker (one command)

You only need [Docker Desktop](https://www.docker.com/products/docker-desktop/). From this folder:

```
copy .env.example .env
```

Open `.env` and fill in the three secrets (`POSTGRES_PASSWORD`, `APP_DB_PASSWORD`,
`JWT_SECRET_KEY`); make each one with `python -c "import secrets; print(secrets.token_hex(32))"`.
Then:

```
docker compose up --build
```

Open **http://localhost:8080** and log in as `sara.wilson@gatekeeper.local` (an employee),
`leila.ali@gatekeeper.local` (a manager) or `grace.raza@gatekeeper.local` (HR), password
`Password123!`. The first start takes a while: it downloads the AI model (~2.5 GB) and creates
the demo company. Later starts are quick, and your data stays (it lives in Docker "volumes").

| Container | What it does |
| --- | --- |
| `db` | PostgreSQL |
| `backend-setup` | Runs once and stops: migrations, the API's limited database user, the demo data. The only container with the database owner's password |
| `backend` | The CRUD API, connected as the limited `gatekeeper_app` user |
| `ollama`, `ollama-pull` | The model server, and the one-time model download |
| `agent` | The AI agent service: no database settings at all |
| `frontend` | The page (nginx), which forwards `/api/...` to the agent |

Only the page (port 8080) is reachable from your browser; everything else talks on Docker's
private network. Useful commands:

```
docker compose logs -f agent      # watch one service
docker compose down               # stop (your data stays)
docker compose down -v            # stop AND delete all data, including the downloaded model
```

**Speed:** in Docker the model runs on the CPU, which is slow (a minute or more per answer).
With an NVIDIA card use `docker compose -f compose.yaml -f compose.gpu.yaml up --build`.
For real use, set `SEED_DEMO_DATA=no` in `.env` before the first start.

## Run the services by hand (for development)

Start them in that order: backend, agent (with Ollama running), then the frontend.
Each folder's README explains its setup.

[`SECURITY.md`](SECURITY.md) lists the attacks the design must survive, and the tests that prove it does.

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): GitHub runs the backend, agent and frontend
checks on every push. The eval with the real AI model is slow on GitHub's computers (no GPU), so it
only runs when you start it by hand (Actions tab, CI, "Run workflow") and every Monday night.
