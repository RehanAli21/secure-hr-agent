from fastapi import FastAPI

from app.database import Base, engine

# this line imports all table which then create table in database if tables does not exists.
from app import models  # noqa: F401  (importing this registers our tables with Base)
# Create all tables in the database if they don't exist yet.
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Gatekeeper HR API")


@app.get("/health")
def health():
    """A simple check to see if the server is running."""
    return {"status": "ok"}
