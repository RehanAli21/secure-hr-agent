# The OWNER's connection to the database: for setup work only, NEVER for the API.
#
# We use two database users (see create_app_user.py):
#   DATABASE_URL        gatekeeper_app: the API. It may only read and write rows, and only the
#                       ones it needs (it can't change tables, edit the audit log, or delete a salary).
#   OWNER_DATABASE_URL  the owner of the tables: migrations (alembic), seed.py, create_user.py,
#                       create_superuser.py, create_app_user.py and the test setup.
#
# This file is outside the app/ folder on purpose: the API never imports it, so a running API
# doesn't need (and shouldn't have) the owner's password at all.

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

OWNER_DATABASE_URL = os.getenv("OWNER_DATABASE_URL")
if OWNER_DATABASE_URL is None:
    raise RuntimeError("OWNER_DATABASE_URL is missing. Add it to your .env file (see .env.example).")

owner_engine = create_engine(OWNER_DATABASE_URL)
OwnerSession = sessionmaker(bind=owner_engine)
