# Talking to the CRUD API (the backend).
#
# Every call to HR data goes through this file, over HTTP.
# The CRUD API checks the user's token and permissions on every request, so even if the AI
# is tricked, it can never do more than the logged-in user is allowed to do.
#
# This is also the ONLY file that touches the tokens. They go into the Authorization header
# and nowhere else: never into a prompt, a tool argument or a log.

import uuid
from typing import Any, Optional

import httpx

from app.config import CRUD_API_URL
from app.sessions import ChatSession

# Give up if the CRUD API takes longer than this to answer.
TIMEOUT_SECONDS = 10

# One HTTP client for all calls. (The tests swap it for a fake CRUD API.)
http = httpx.Client(base_url=CRUD_API_URL, timeout=TIMEOUT_SECONDS)


class CrudApiError(Exception):
    """The CRUD API said no (or could not be reached). Carries its status code and message."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def error_from(response: httpx.Response) -> CrudApiError:
    """Turn an error response from the CRUD API into a CrudApiError, keeping its message."""
    try:
        detail = response.json().get("detail", "Error from the CRUD API")
    except ValueError:
        detail = "Error from the CRUD API"
    return CrudApiError(response.status_code, detail)


def is_crud_api_up() -> bool:
    """Ask the CRUD API's /health endpoint if it is running."""
    try:
        response = http.get("/health")
    except httpx.HTTPError:
        return False  # not running, wrong address, timeout...
    return response.status_code == 200


def log_in(email: str, password: str) -> tuple[str, str]:
    """Log in at the CRUD API. Returns (access_token, refresh_token)."""
    try:
        response = http.post("/auth/login", json={"email": email, "password": password})
    except httpx.HTTPError:
        raise CrudApiError(503, "The HR system is not reachable right now")
    if response.status_code != 200:
        raise error_from(response)
    tokens = response.json()
    return tokens["access_token"], tokens["refresh_token"]


def get_new_access_token(session: ChatSession) -> Optional[str]:
    """Trade the refresh token for a new access token. None if that fails (logged out, disabled...)."""
    response = http.post("/auth/refresh", json={"refresh_token": session.refresh_token})
    if response.status_code != 200:
        return None
    return response.json()["access_token"]


def call(
    session: ChatSession,
    method: str,
    path: str,
    json: Optional[dict[str, Any]] = None,    # the request body, for POST and PATCH
    params: Optional[dict[str, Any]] = None,  # the "?status=pending" part of the URL
    source: str = "agent",                    # "ui" ONLY for the Confirm/Cancel buttons (app/routers/actions.py)
) -> Any:
    """Call the CRUD API as the logged-in user, for example call(session, "GET", "/me").

    - The user's own token goes in the Authorization header, so the CRUD API checks THEIR permissions.
    - "X-Request-Source: agent" tells the CRUD API this came from the agent, so it is written in the
      audit log, and big changes are blocked unless the user confirms them in the app.
      Every tool uses "agent". Only the Confirm and Cancel buttons send "ui": a person clicked.
    - The access token only lasts 15 minutes. If it has run out (401), we get a new one with the
      refresh token and try once more.
    - Every write gets an Idempotency-Key: a random id for THIS change. When we send it again (after
      a new token, or a network failure), the same key goes with it, so the CRUD API makes the
      change only once (see backend/app/idempotency.py).
    """
    idempotency_key = None if method == "GET" else str(uuid.uuid4())

    def send() -> httpx.Response:
        headers = {"Authorization": f"Bearer {session.access_token}", "X-Request-Source": source}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        return http.request(method, path, json=json, params=params, headers=headers)

    try:
        try:
            response = send()
        except httpx.TransportError:
            # The network failed, or the CRUD API was too slow to answer. Did the request arrive?
            # We can't know. Sending it once more is safe: a write carries the same key.
            response = send()
        if response.status_code == 401:
            new_token = get_new_access_token(session)
            if new_token is None:
                raise CrudApiError(401, "Your login has ended. Please log in again.")
            session.access_token = new_token
            response = send()
    except httpx.HTTPError:
        raise CrudApiError(503, "The HR system is not reachable right now")

    if response.status_code >= 400:
        raise error_from(response)
    return response.json()
