# Talking to the CRUD API (the backend).
#
# Every call to HR data goes through this file, over HTTP.
# The CRUD API checks the user's token and permissions on every request, so even if the AI
# is tricked, it can never do more than the logged-in user is allowed to do.

import httpx

from app.config import CRUD_API_URL

# Give up if the CRUD API takes longer than this to answer.
TIMEOUT_SECONDS = 10


def is_crud_api_up() -> bool:
    """Ask the CRUD API's /health endpoint if it is running."""
    try:
        response = httpx.get(f"{CRUD_API_URL}/health", timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError:
        return False  # not running, wrong address, timeout...
    return response.status_code == 200
