import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Read settings from the .env file, so passwords stay out of our code.
load_dotenv()

# Where the database lives, for example:
# postgresql://postgres:mypassword@localhost:5432/gatekeeper_hr
DATABASE_URL = os.getenv("DATABASE_URL")

# The engine is the actual connection to the database.
engine = create_engine(DATABASE_URL)

# A session is one "conversation" with the database.
# We open one session per request, and close it when the request is done.
SessionLocal = sessionmaker(bind=engine)

# Every table we create will inherit from Base.
Base = declarative_base()


def get_db():
    """Give a database session to an endpoint, then close it afterwards."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
