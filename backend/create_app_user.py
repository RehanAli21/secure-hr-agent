# Create the database user the API runs as (gatekeeper_app), with ONLY the rights it needs.
#
# Why: the API's Python code already enforces the rules (no hard deletes, salary history is kept,
# the audit log is never edited). This makes the DATABASE enforce them too, as a second wall. Even
# if a bug, a leaked password or a bad library got around the code, the database would still say
# no to: changing or dropping tables, editing or deleting audit rows, changing or deleting a salary,
# deleting an employee, changing a password, creating a login.
#
# It reads the user name and password from DATABASE_URL, and runs as the owner (OWNER_DATABASE_URL).
# Run it from the backend folder after every "alembic upgrade head" (a new table needs its rights):
#     .venv\Scripts\python create_app_user.py
# Running it again is safe: it sets everything to exactly what RIGHTS says.

from sqlalchemy import Connection, text

from app.database import Base, engine
from owner_database import owner_engine

# What the API may do with each table. Anything not written here is NOT allowed.
#   SELECT = read rows, INSERT = add rows, UPDATE = change rows, DELETE = remove rows
RIGHTS = {
    "departments":         "SELECT, INSERT, UPDATE",          # deleting = setting deleted_at
    "employees":           "SELECT, INSERT, UPDATE",          # never deleted: terminated instead
    "users":               "SELECT, UPDATE (role, is_active)",  # only these two columns may change
    "super_users":         "SELECT",                          # created by create_superuser.py only
    "salaries":            "SELECT, INSERT",                  # a raise is a NEW row: history stays
    "leave_requests":      "SELECT, INSERT, UPDATE",
    "performance_reviews": "SELECT, INSERT, UPDATE",          # deleting = setting deleted_at
    "audit_log":           "SELECT, INSERT",                  # never changed, never deleted
    "pending_actions":     "SELECT, INSERT, UPDATE",
    "idempotency_keys":    "SELECT, INSERT, UPDATE, DELETE",  # the only table with real deletes
}


def give_rights(connection: Connection, app_user: str) -> None:
    """Take every right away from app_user, then give back exactly what RIGHTS says."""
    # A new table without an entry in RIGHTS would silently be unusable: stop with a clear message.
    missing = [table.name for table in Base.metadata.sorted_tables if table.name not in RIGHTS]
    if missing:
        raise RuntimeError(f"Add these tables to RIGHTS in create_app_user.py: {', '.join(missing)}")

    connection.execute(text(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{app_user}"'))
    connection.execute(text(f'REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM "{app_user}"'))
    connection.execute(text(f'GRANT USAGE ON SCHEMA public TO "{app_user}"'))  # see the tables, not create them
    for table, rights in RIGHTS.items():
        connection.execute(text(f'GRANT {rights} ON "{table}" TO "{app_user}"'))
        if "INSERT" in rights:
            # A new row gets its id from a counter (a "sequence"): the user must be allowed to use it.
            sequence = connection.execute(text(f"SELECT pg_get_serial_sequence('{table}', 'id')")).scalar()
            if sequence is not None:
                connection.execute(text(f'GRANT USAGE ON SEQUENCE {sequence} TO "{app_user}"'))


def main() -> None:
    url = engine.url  # the API's DATABASE_URL, e.g. postgresql://gatekeeper_app:PASSWORD@127.0.0.1:5432/gatekeeper_hr
    app_user, password = url.username, url.password
    if app_user is None or password is None or url.database is None:
        raise RuntimeError("DATABASE_URL needs a user name, a password and a database name.")
    if app_user == owner_engine.url.username:
        raise RuntimeError("DATABASE_URL must use its own user (e.g. gatekeeper_app), not the owner.")

    with owner_engine.begin() as connection:
        exists = connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :name"), {"name": app_user}).first()
        # A plain login: not a superuser, can't create databases or other users.
        # A password can't be a query parameter here, so we quote it by hand ('' = one ').
        # exec_driver_sql, not text(): text() would read a ":" in the password as a parameter.
        quoted_password = password.replace("'", "''")
        action = "ALTER" if exists else "CREATE"
        connection.exec_driver_sql(
            f"{action} ROLE \"{app_user}\" LOGIN PASSWORD '{quoted_password}' NOSUPERUSER NOCREATEDB NOCREATEROLE"
        )
        connection.execute(text(f'GRANT CONNECT ON DATABASE "{url.database}" TO "{app_user}"'))
        give_rights(connection, app_user)
    print(f"{'Updated' if exists else 'Created'} the database user {app_user} for {url.database}.")


if __name__ == "__main__":
    main()
