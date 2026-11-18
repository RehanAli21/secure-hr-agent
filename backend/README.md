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
- `OWNER_DATABASE_URL`: put your PostgreSQL (`postgres`) password in place of `YOUR_PASSWORD`
- `DATABASE_URL`: make up a password for the API's own database user, `gatekeeper_app`, in place
  of `APP_PASSWORD` (step 4 creates that user). Make one with
  `python -c "import secrets; print(secrets.token_urlsafe(24))"`
- `JWT_SECRET_KEY`: a long random secret. Make one with:

  ```
  python -c "import secrets; print(secrets.token_hex(32))"
  ```

**4. Create the tables, and the API's database user:**

```
alembic upgrade head
python create_app_user.py
```

Why two database users? The API connects as `gatekeeper_app`, which may only read and write
the rows it needs. So even if a bug or a leaked password got past the Python code, PostgreSQL
itself refuses to change or drop tables, edit or delete the audit log, change or delete a salary
(a raise is always a new row), delete an employee, change a password or create a login. The
exact rights are the `RIGHTS` table in `create_app_user.py`. Everything else (migrations, the
seed, `create_user.py`, `create_superuser.py`) runs as the owner, `OWNER_DATABASE_URL`; the
running API never uses it. `tests/test_database_rights.py` tries each forbidden thing.

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

## Rate limits and idempotency keys

**Rate limits** (`app/rate_limits.py`): each user may send 300 reads and 30 writes per minute, and
each email address may try to log in 10 times per minute. Over the limit, the API answers
`429 Too many requests` with a `Retry-After` header (seconds to wait). The counts live in memory:
a restart resets them, and with several server processes each one counts on its own.

**Idempotency keys** (`app/idempotency.py`): a write may carry an `Idempotency-Key` header (any
text up to 100 characters, e.g. a random UUID). If the same user sends the same request with the
same key again, for example a retry after a timeout, the API does NOT run it again: it sends back
the saved first answer, with the header `Idempotent-Replayed: true`. The same key for a different
request gives 422; while the first one is still running, 409. Server errors are not saved, so a
retry runs for real. Keys are kept for 24 hours in the `idempotency_keys` table. The header is
optional; the AI agent sends one with every write.

## Analytics (numbers about many people)

`GET /analytics` answers questions like "average salary by department" or "sick days by month".
The question is never SQL: it is a choice from fixed lists, and the API runs its own queries
(`app/analytics.py`). Try it at `/docs`.

| Parameter | Choices |
| --- | --- |
| `metric` | `headcount`, `new_hires`, `leavers`, `average_salary`, `payroll_total`, `leave_days`, `average_rating` |
| `group_by` | `department`, `month`, `year`, `leave_type`, `period` (each metric allows only some; leave it out for one number) |
| `department`, `leave_type` | narrow it down (`leave_type` only for `leave_days`) |
| `from_date`, `to_date` | for `new_hires`, `leavers`, `leave_days` (default: the last 12 months, at most 5 years) |

Who may ask: the **analyst** role (new; in the demo data, the Financial Analysts, for example
`liam.king@gatekeeper.local`), hr and admin see the whole company. Anyone with direct reports sees
only their team, and no salary numbers. Everyone else gets 403. An analyst is an employee for
records: they still can't open anyone else's profile, salary or reviews.

**The small-group rule** for private numbers (salaries, leave, ratings), so no one person's
details can be worked out:
1. Nobody from a department with fewer than 5 active employees is ever counted: not in a total,
   not with a filter, not in a group. (Only hiding such a department from one answer is not
   enough: "everyone's payroll" minus "every other department" would give it back.)
2. A group in an answer that is about fewer than 5 people is left out ("parental leave in
   Legal: 2 people"). This also covers a manager's team of fewer than 5.

## Other useful commands

| Command | What it does |
| --- | --- |
| `python create_user.py` | Create a login account (asks for email, password, employee id and role) |
| `python create_superuser.py` | Create a superuser that can access everything |
| `python seed.py --reset` | Delete all demo data and create it again (keeps superusers you created yourself) |

## Run the automated tests

The tests use a **separate** database, so your demo data is never touched. One-time setup:

1. Create the test database: `CREATE DATABASE gatekeeper_hr_test;`
2. Add `TEST_DATABASE_URL` and `TEST_OWNER_DATABASE_URL` to your `.env` (see `.env.example`).
   Use the same `gatekeeper_app` password as in `DATABASE_URL`: the tests set its rights up on
   the test database themselves, and run the API as that user, so a missing right fails a test.

Then run all tests with:

```
pytest
```

`pytest -v` shows every test by name. `pytest tests/test_setup.py` runs only one file.
The tests build the tables themselves (with the migrations) and empty them before every test.

## Database migrations

After you change `app/models.py`:

```
alembic revision --autogenerate -m "describe the change"
alembic upgrade head
python create_app_user.py
```

A new table also needs a line in `RIGHTS` in `create_app_user.py` (the script stops and tells
you if one is missing), then run the script again.

**Always read the new file in `alembic/versions/` before running `upgrade`.** Autogenerate can miss things, for example check constraints added to an existing table.

To undo the last migration:

```
alembic downgrade -1
```
