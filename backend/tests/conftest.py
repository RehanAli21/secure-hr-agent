# pytest reads this file automatically, before it runs any test.
# It holds the "fixtures": small setup helpers that tests can ask for by name.
#
# Example: a test written like this
#     def test_something(client: TestClient) -> None:
# gets the `client` fixture below handed to it. pytest matches the parameter name to the fixture name.

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from dotenv import dotenv_values

# ---------- Step 1: point the app at the TEST database ----------
# This MUST happen before we import anything from `app`, because app/database.py
# connects to DATABASE_URL the moment it is imported.
# (load_dotenv in the app does not overwrite a variable that is already set, so ours wins.)

BACKEND_FOLDER = Path(__file__).parent.parent
settings = dotenv_values(BACKEND_FOLDER / ".env")

TEST_DATABASE_URL = settings.get("TEST_DATABASE_URL")
if TEST_DATABASE_URL is None:
    raise RuntimeError("TEST_DATABASE_URL is missing. Add it to your .env file (see .env.example).")

# Safety check: the tests delete ALL data, so never let them touch the real database.
if TEST_DATABASE_URL == settings.get("DATABASE_URL") or not TEST_DATABASE_URL.endswith("_test"):
    raise RuntimeError("TEST_DATABASE_URL must be a separate database whose name ends with _test.")

os.environ["DATABASE_URL"] = TEST_DATABASE_URL

# ---------- Step 2: now it is safe to import the app ----------
# (Normally imports go at the top of a file. These must come AFTER step 1.)

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.main import app
from tests.helpers import Org, create_org


# ---------- Fixtures ----------

@pytest.fixture(scope="session", autouse=True)
def create_tables() -> None:
    """Runs ONCE before all tests: build every table in the test database with our migrations.

    scope="session" = only once per test run.  autouse=True = no test has to ask for it.
    Running the real migrations also tests that they work.
    """
    config = Config(str(BACKEND_FOLDER / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_FOLDER / "alembic"))
    command.upgrade(config, "head")


def empty_all_tables() -> None:
    """Delete every row in every table, so each test starts with an empty database."""
    table_names = ", ".join(table.name for table in Base.metadata.sorted_tables)
    with engine.begin() as connection:
        # RESTART IDENTITY = ids start at 1 again.  CASCADE = also empty tables that point to these.
        connection.execute(text(f"TRUNCATE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db() -> Iterator[Session]:
    """A database session for a test, on an EMPTY test database.

    Everything before `yield` runs before the test. Everything after it runs after the test.
    """
    empty_all_tables()
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db: Session) -> TestClient:
    """Sends requests to our API without starting a server. Example: client.get("/health")

    It asks for `db` too, so the database is always emptied first.
    """
    return TestClient(app)


@pytest.fixture
def org(db: Session) -> Org:
    """The small test company from tests/helpers.py: Hana (hr), Mona (manager), Ravi (Mona's report), Omar."""
    return create_org(db)
