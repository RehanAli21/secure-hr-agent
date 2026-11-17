# Tests for the API's database user (create_app_user.py): the DATABASE itself must refuse what
# the API is never supposed to do, even if a bug or a leaked password got past the Python code.
#
# Each test connects with app.database.engine (the API's own connection, as gatekeeper_app) and
# tries one forbidden thing directly in SQL. PostgreSQL must refuse it for lack of rights.

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.database import engine
from app.models import AuditLog, Salary
from tests.helpers import Org


def refused(sql: str) -> bool:
    """Run one statement as the API's user. True if PostgreSQL refused it for lack of rights."""
    with engine.connect() as connection:
        try:
            connection.execute(text(sql))
        except ProgrammingError as error:
            # 42501 = PostgreSQL's code for "not allowed" ("permission denied", "must be owner").
            # Any other error (a typo in the SQL...) is NOT a pass.
            return getattr(error.orig, "pgcode", None) == "42501"
        finally:
            connection.rollback()  # if it did run, undo it
    return False


def add_salary_and_audit_row(db: Session, org: Org) -> None:
    db.add(Salary(employee_id=org.report.id, amount=Decimal("50000"), effective_date=date(2026, 1, 1)))
    db.add(AuditLog(account_kind="user", account_id=1, action="create", table_name="salaries",
                    record_id=1, source="ui"))
    db.commit()


def test_the_api_user_is_not_the_owner_or_a_superuser() -> None:
    with engine.connect() as connection:
        is_superuser = connection.execute(text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")).scalar()
        user = connection.execute(text("SELECT current_user")).scalar()
    assert is_superuser is False
    assert user == "gatekeeper_app"


def test_the_api_user_cannot_create_change_or_drop_tables() -> None:
    assert refused("CREATE TABLE stolen (data text)")
    assert refused("DROP TABLE employees")
    assert refused("ALTER TABLE salaries ADD COLUMN bonus numeric")
    assert refused("TRUNCATE audit_log")


def test_the_audit_log_cannot_be_changed_or_deleted(db: Session, org: Org) -> None:
    add_salary_and_audit_row(db, org)
    assert refused("UPDATE audit_log SET action = 'nothing happened'")
    assert refused("DELETE FROM audit_log")


def test_salary_history_cannot_be_changed_or_deleted(db: Session, org: Org) -> None:
    add_salary_and_audit_row(db, org)
    assert refused("UPDATE salaries SET amount = 1000000")
    assert refused("DELETE FROM salaries")


def test_employees_and_records_are_never_deleted(org: Org) -> None:
    # Employees are terminated, departments and reviews soft-deleted (deleted_at), never removed.
    for table in ("employees", "departments", "performance_reviews", "leave_requests", "users", "pending_actions"):
        assert refused(f"DELETE FROM {table}"), table


def test_logins_cannot_be_created_and_passwords_cannot_be_changed(org: Org) -> None:
    assert refused("UPDATE users SET password_hash = 'x'")
    assert refused("UPDATE users SET email = 'attacker@evil.test'")
    assert refused("INSERT INTO users (email, password_hash, role, is_active) VALUES ('a@b.c', 'x', 'admin', true)")
    assert refused("INSERT INTO super_users (email, password_hash) VALUES ('a@b.c', 'x')")
    assert refused("UPDATE super_users SET password_hash = 'x'")


def test_the_alembic_version_table_is_out_of_reach() -> None:
    # Which migrations have run is owner business only.
    assert refused("SELECT * FROM alembic_version")


def test_what_the_api_needs_still_works(org: Org) -> None:
    # The flip side: the allowed rights really are there (the other 160 tests use them all).
    assert not refused("UPDATE users SET role = role, is_active = is_active")
    assert not refused("SELECT count(*) FROM salaries")
