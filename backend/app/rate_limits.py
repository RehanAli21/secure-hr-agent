# Rate limits: how many requests one user may send per minute.
#
# Why: a runaway script, or an AI agent stuck in a loop, should not be able to flood the API, and
# nobody should be able to guess a password by trying thousands of them.
#   - reads  (GET):              READS_PER_MINUTE per user
#   - writes (POST, PATCH, ...): WRITES_PER_MINUTE per user (stricter: they change data)
#   - logins:                    LOGINS_PER_MINUTE per email address (right or wrong password)
# Over the limit: 429 "Too many requests", with a Retry-After header (seconds to wait).
#
# How: for every user we keep the times of their requests in the last minute ("sliding window").
# The times live in memory, so they are lost on a restart, and each server process counts on its
# own. That is fine for one process (as we run it). With several, keep them in Redis instead.

import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException, Request

from app.security import Account, account_kind, get_current_user

WINDOW_SECONDS = 60
READS_PER_MINUTE = 300   # one chat message can make ~20 reads (permissions, a search, names...)
WRITES_PER_MINUTE = 30   # the agent makes at most one change per chat message
LOGINS_PER_MINUTE = 10

# "user:3:write" -> the times (in seconds) of that user's recent writes, oldest first.
recent_requests: dict[str, deque[float]] = defaultdict(deque)
# FastAPI runs requests in several threads at once: only one may change the counts at a time.
lock = threading.Lock()


def count_request(key: str, limit: int) -> None:
    """Count one request for this key. Stops with 429 if there were already `limit` in the last minute."""
    now = time.monotonic()  # a clock that never jumps (unlike the wall clock)
    with lock:
        times = recent_requests[key]
        while times and times[0] <= now - WINDOW_SECONDS:
            times.popleft()  # older than a minute: no longer counts
        if len(times) >= limit:
            wait = int(times[0] + WINDOW_SECONDS - now) + 1  # until the oldest one drops out
            raise HTTPException(status_code=429, detail=f"Too many requests. Try again in {wait} seconds.",
                                headers={"Retry-After": str(wait)})
        times.append(now)


def rate_limit(request: Request, account: Account = Depends(get_current_user)) -> None:
    """A dependency for every protected endpoint: count this request for the logged-in user."""
    is_write = request.method not in ("GET", "HEAD", "OPTIONS")
    # account_kind: user 3 and superuser 3 are different accounts, so they get separate counts.
    who = f"{account_kind(account)}:{account.id}"
    if is_write:
        count_request(f"{who}:write", WRITES_PER_MINUTE)
    else:
        count_request(f"{who}:read", READS_PER_MINUTE)


def limit_logins(email: str) -> None:
    """Count one login attempt for this email (lowercase, so "Ana@X" and "ana@x" count together)."""
    count_request(f"login:{email.lower()}", LOGINS_PER_MINUTE)


def forget_all() -> None:
    """Start counting from zero again (for the tests: each test starts fresh)."""
    with lock:
        recent_requests.clear()
