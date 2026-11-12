from fastapi import FastAPI

from app.routers import departments, employees

# The tables are created and updated by Alembic migrations, not here.
# Run this before starting the server:   alembic upgrade head

app = FastAPI(title="Gatekeeper HR API")

# Plug in the endpoints from the routers folder.
app.include_router(departments.router)
app.include_router(employees.router)


@app.get("/health")
def health():
    """A simple check to see if the server is running."""
    return {"status": "ok"}
