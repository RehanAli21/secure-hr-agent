# The agent service: the AI assistant that sits between the chat UI and the CRUD API.
#
#   Chat UI (React)  -->  agent service (this)  -->  CRUD API  -->  database
#
# The AI proposes, the API decides: this service never touches the database.
# Right now it has login, logout and "who am I?". The AI and the tools come in the next steps.

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import crud_api
from app.routers import auth

app = FastAPI(title="Gatekeeper HR Agent")

app.include_router(auth.router)


@app.exception_handler(crud_api.CrudApiError)
def crud_api_error(request: Request, error: crud_api.CrudApiError) -> JSONResponse:
    """When the CRUD API says no, pass its answer on unchanged (same status code, same message)."""
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


@app.get("/health")
def health():
    """Is this service running, and can it reach the CRUD API?"""
    if crud_api.is_crud_api_up():
        return {"status": "ok", "crud_api": "ok"}
    return {"status": "ok", "crud_api": "unreachable"}
