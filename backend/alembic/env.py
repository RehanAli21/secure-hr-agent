# Alembic runs this file every time we use an alembic command.
# Its job: connect to our database and know what our tables should look like.

from logging.config import fileConfig

from alembic import context

from owner_database import owner_engine
from app import models

# Turn on Alembic's log messages (settings come from alembic.ini).
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# This is how Alembic knows our tables. It compares models.py with the real
# database to write new migrations for us (alembic revision --autogenerate).
target_metadata = models.Base.metadata


def run_migrations() -> None:
    # Migrations create and change tables, so they run as the OWNER (OWNER_DATABASE_URL in .env),
    # never as the API's user, which isn't allowed to (see create_app_user.py).
    with owner_engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
