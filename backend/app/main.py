from fastapi import FastAPI

from app.database import engine
from app.routers import departments, employees

# this line imports all table which then create table in database if tables does not exists.
from app import models

# Create all tables in the database if they don't exist yet.
# models.Base knows every table class written in models.py.
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Gatekeeper HR API")

# Plug in the endpoints from the routers folder.
app.include_router(departments.router)
app.include_router(employees.router)


@app.get("/health")
def health():
    """A simple check to see if the server is running."""
    return {"status": "ok"}
