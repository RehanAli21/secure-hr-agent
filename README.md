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

**Checked in CI:** on every push, GitHub builds the containers and starts them without the AI
model (`.github/docker-smoke-test.sh`): the page loads, the agent reaches the CRUD API, a demo
user can log in, and the API is connected to the database as `gatekeeper_app`. Run the same
check yourself in Git Bash with `bash .github/docker-smoke-test.sh` (it uses its own Docker
project, so it never touches your data).

## Run on Windows without Docker

You need Python 3.10+, Node 20+, [PostgreSQL](https://www.postgresql.org/download/windows/) with
an empty database called `gatekeeper_hr` (see [`backend/README.md`](backend/README.md), step 1),
and [Ollama](https://ollama.com/download).

1. Double-click **`setup.bat`**. The first time it creates `backend\.env` and stops: fill in the
   passwords (see `backend/README.md`, step 3), then run it again. It installs everything, creates
   the tables, the API's database user and the demo company, and downloads the AI model. Run it
   again after you pull new code: every step is safe to repeat.
2. Double-click **`start.bat`**. It opens one window each for the backend, the agent and the page
   (and Ollama, if it isn't running yet), then opens http://localhost:5173. Close a window to
   stop that service. A service that is already running is left alone.

Note: everything talks to whichever Ollama answers on port 11434. If you also have Ollama
installed inside WSL (Ubuntu), it can start with WSL (for example when Docker Desktop starts) and
take that port before the Windows app: the WSL one then gets used instead, with its own model
folder. Run only one of them.

## Run the services by hand (for development)

Start them in that order: backend, agent (with Ollama running), then the frontend.
Each folder's README explains its setup.

[`SECURITY.md`](SECURITY.md) lists the attacks the design must survive, and the tests that prove it does.

**CI** ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): GitHub runs the backend, agent and frontend
checks on every push. The eval with the real AI model is slow on GitHub's computers (no GPU), so it
only runs when you start it by hand (Actions tab, CI, "Run workflow") and every Monday night.
