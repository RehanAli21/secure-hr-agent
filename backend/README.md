# Gatekeeper HR — Backend

The CRUD API for Gatekeeper HR, built with Python, FastAPI, SQLAlchemy and PostgreSQL.

## What you need

- Python 3.10 or newer
- PostgreSQL (running on port 5432)

## First-time setup

Run every command from the `backend` folder.

**1. Create the database** (once). In `psql` or pgAdmin:

```sql
CREATE DATABASE gatekeeper_hr;
```

**2. Create a virtual environment and install the packages:**

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

On macOS/Linux, activate it with `source .venv/bin/activate` instead.

**3. Create your `.env` file:**

```
copy .env.example .env
```

Open `.env` and fill in:
- `DATABASE_URL`: put your PostgreSQL password in place of `YOUR_PASSWORD`
- `JWT_SECRET_KEY`: a long random secret. Make one with:

  ```
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

**4. Create the tables:**

```
alembic upgrade head
```

**5. Fill the database with demo data** (optional, but recommended):

```
python seed.py
```

This creates about 550 employees, each with a login, plus salaries, leave requests and performance reviews.

## Run the API

```
python -m uvicorn app.main:app --reload
```

- API: http://localhost:8000
- Interactive docs: http://localhost:8000/docs

## Log in

Every demo login uses the password `Password123!`.

| Who | How |
| --- | --- |
| Any employee | `POST /auth/login` with their email, e.g. `sara.wilson@gatekeeper.local` |
| Superuser (can do everything) | `POST /auth/superuser/login` with `admin@gatekeeper.local` |

Copy the `access_token` from the response, click **Authorize** on the `/docs` page and paste it in.

Each login has a role: `employee`, `manager`, `hr` or `admin`. Call `GET /me` to see yours.

Call `GET /me/permissions` to see what you may do, and `GET /roles` to see every role.

## Big changes need confirmation

Terminating someone, changing a salary, role or manager, and deleting a department or review are big changes.
When the AI agent asks for one (it sends the header `X-Request-Source: agent`), it must go through a pending action:

1. `POST /actions` with for example `{"action": "terminate_employee", "target_id": 7, "args": {"termination_date": "2026-12-31"}}`.
   Nothing changes yet. The answer has a `preview` built from the real data.
2. The user checks the preview and clicks Confirm in the app, which calls `POST /actions/{id}/confirm`.
   The user has 5 minutes, and each action can be confirmed only once.

Every change, confirmed or not, is written to the audit log: `GET /audit-log` (hr and admin only).

## Other useful commands

| Command | What it does |
| --- | --- |
| `python create_user.py` | Create a login account (asks for email, password, employee id and role) |
| `python create_superuser.py` | Create a superuser that can access everything |
| `python seed.py --reset` | Delete all demo data and create it again (keeps superusers you created yourself) |

## Database migrations

After you change `app/models.py`:

```
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
```

**Always read the new file in `alembic/versions/` before running `upgrade`.** Autogenerate can miss things, for example check constraints added to an existing table.

To undo the last migration:

```
alembic downgrade -1
```
