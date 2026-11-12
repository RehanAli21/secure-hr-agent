import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Read settings from the .env file, so passwords stay out of our code.
load_dotenv()

# Where the database lives, for example:
# postgresql://postgres:mypassword@localhost:5432/gatekeeper_hr
DATABASE_URL = os.getenv("DATABASE_URL")

# Stop right away with a clear message if the setting is missing.
if DATABASE_URL is None:
    raise RuntimeError("DATABASE_URL is missing. Copy .env.example to .env and fill it in.")

# The engine is the actual connection to the database.
engine = create_engine(DATABASE_URL)

# A session is one "conversation" with the database.
# We open one session per request, and close it when the request is done.
SessionLocal = sessionmaker(bind=engine)


# Every table we create will inherit from Base.
class Base(DeclarativeBase):
    pass


def get_db():
    """Give a database session to an endpoint, then close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
