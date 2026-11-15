# Gatekeeper HR — Agent service

The AI assistant for Gatekeeper HR. It sits between the chat UI and the CRUD API:

```
Chat UI (React)  -->  agent service (this folder)  -->  CRUD API (../backend)  -->  database
```

**The AI proposes, the API decides.** This service has no database password. It reaches HR data only
through the CRUD API, using the logged-in user's token, so it can never do more than that user.

Right now it only has a health check. The AI and its tools come in the next steps.

## What you need

- Python 3.10 or newer
- The CRUD API from `../backend`, running (see its README)

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

## Run the tests

```
python -m pytest
```

The tests don't need the backend running.
