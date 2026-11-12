# Alembic runs this file every time we use an alembic command.
# Its job: connect to our database and know what our tables should look like.

from logging.config import fileConfig

from alembic import context

from app.database import engine
from app import models

# Turn on Alembic's log messages (settings come from alembic.ini).
if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

# This is how Alembic knows our tables. It compares models.py with the real
# database to write new migrations for us (alembic revision --autogenerate).
target_metadata = models.Base.metadata


def run_migrations() -> None:
    # We reuse the same engine as the app, so the URL comes from .env.
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


run_migrations()
