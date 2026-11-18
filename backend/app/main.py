from fastapi import Depends, FastAPI

from app.idempotency import idempotency_middleware
from app.rate_limits import rate_limit
from app.routers import actions, analytics, audit_log, auth, departments, employees, leave, reviews, roles, salaries

# The tables are created and updated by Alembic migrations, not here.
# Run this before starting the server:   alembic upgrade head

app = FastAPI(title="Gatekeeper HR API")

# Every request passes through this first: a write sent twice with the same Idempotency-Key
# header only happens once (see app/idempotency.py).
app.middleware("http")(idempotency_middleware)

# Plug in the endpoints from the routers folder.
# Every router except auth needs a login, and counts each request for the rate limits
# (app/rate_limits.py). The login endpoints have their own limit, per email (see routers/auth.py).
LIMITED = [Depends(rate_limit)]
app.include_router(auth.router)
app.include_router(roles.router, dependencies=LIMITED)
app.include_router(departments.router, dependencies=LIMITED)
app.include_router(employees.router, dependencies=LIMITED)
app.include_router(leave.router, dependencies=LIMITED)
app.include_router(salaries.router, dependencies=LIMITED)
app.include_router(reviews.router, dependencies=LIMITED)
app.include_router(audit_log.router, dependencies=LIMITED)
app.include_router(actions.router, dependencies=LIMITED)
app.include_router(analytics.router, dependencies=LIMITED)


@app.get("/health")
def health():
    """A simple check to see if the server is running."""
    return {"status": "ok"}
