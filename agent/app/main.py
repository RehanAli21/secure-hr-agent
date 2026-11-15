# The agent service: the AI assistant that sits between the chat UI and the CRUD API.
#
#   Chat UI (React)  -->  agent service (this)  -->  CRUD API  -->  database
#
# The AI proposes, the API decides: this service never touches the database.
# Right now it only has a health check. The AI and the tools come in the next steps.

from fastapi import FastAPI

from app import crud_api

app = FastAPI(title="Gatekeeper HR Agent")


@app.get("/health")
def health():
    """Is this service running, and can it reach the CRUD API?"""
    if crud_api.is_crud_api_up():
        return {"status": "ok", "crud_api": "ok"}
    return {"status": "ok", "crud_api": "unreachable"}
