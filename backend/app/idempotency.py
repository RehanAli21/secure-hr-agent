# Idempotency keys: a write that is sent twice must only happen once.
#
# Why: a client sends "ask for leave", the network is slow, the client gives up waiting and sends
# it again. Did the first one arrive? Without this, the leave could be asked for twice.
# So the client sends a random "Idempotency-Key" header with every write, the same one on a retry:
#   - a new key:                     the request runs, and its answer is saved with the key
#   - the same key, same request:    the saved answer is sent again, NOTHING runs a second time
#                                    (the answer has the header "Idempotent-Replayed: true")
#   - the same key, other request:   422 (a key can't be reused for something else)
#   - the same key, still running:   409 (the first one hasn't finished yet)
# A server error (500+) is not saved, so the client can simply try again.
# A key belongs to the logged-in account, and is forgotten after KEEP_FOR (24 hours).
# The header is optional: requests without it work exactly as before. Our agent always sends one.
#
# This is a "middleware": a function that every request passes through before (and after) its
# endpoint, so all write endpoints get this at once.

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from typing import AsyncIterable, Awaitable, Callable, Optional

from fastapi import HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import IdempotencyKey, utc_now
from app.security import read_token

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MAX_KEY_LENGTH = 100
KEEP_FOR = timedelta(hours=24)


@dataclass
class Claim:
    """What claim_key found: "run" (a new key), "replay", "running" or "different"."""
    outcome: str
    row_id: int = 0          # for "run": the row to save the answer in
    status_code: int = 0     # for "replay": the saved answer
    body: str = ""


def who_is_asking(request: Request) -> Optional[tuple[str, int]]:
    """(account kind, account id) from the access token, or None if there is no valid one.
    No valid token: we do nothing here, and the endpoint itself answers 401."""
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    try:
        account_id, kind = read_token(header.removeprefix("Bearer "), expected_type="access")
    except HTTPException:
        return None
    return kind, account_id


def fingerprint(request: Request, body: bytes) -> str:
    """A short code for "this exact request": method, path, query, source and body."""
    text = f"{request.method} {request.url.path}?{request.url.query} {request.headers.get('X-Request-Source')}\n"
    return hashlib.sha256(text.encode() + body).hexdigest()


def claim_key(kind: str, account_id: int, key: str, request_hash: str) -> Claim:
    """Look the key up. If it is new, save it as "running" so a second request with it must wait."""
    with SessionLocal() as db:
        row = db.query(IdempotencyKey).filter_by(account_kind=kind, account_id=account_id, key=key).first()
        if row is not None and row.created_at < utc_now() - KEEP_FOR:
            db.delete(row)  # too old: forget it, and treat the key as new
            db.commit()
            row = None

        if row is None:
            row = IdempotencyKey(account_kind=kind, account_id=account_id, key=key, request_hash=request_hash)
            db.add(row)
            try:
                db.commit()
            except IntegrityError:
                # Another request with the same key saved it a split second before us (the
                # unique index allows only one), so that one is running.
                return Claim("running")
            return Claim("run", row_id=row.id)

        if row.request_hash != request_hash:
            return Claim("different")
        if row.status_code is None or row.response_body is None:
            return Claim("running")
        return Claim("replay", status_code=row.status_code, body=row.response_body)


def save_answer(row_id: int, status_code: int, body: str) -> None:
    with SessionLocal() as db:
        row = db.get(IdempotencyKey, row_id)
        if row is not None:
            row.status_code = status_code
            row.response_body = body
            db.commit()


def forget_key(row_id: int) -> None:
    """Remove a key whose request failed with a server error, so a retry can run again."""
    with SessionLocal() as db:
        row = db.get(IdempotencyKey, row_id)
        if row is not None:
            db.delete(row)
            db.commit()


async def read_answer(response: Response) -> bytes:
    """The whole body of the endpoint's answer. It comes in pieces ("chunks"), so we join them.
    (getattr: the answer is Starlette's own streaming kind, which has no public type to name.)"""
    body_iterator: AsyncIterable[bytes] = getattr(response, "body_iterator")
    return b"".join([chunk async for chunk in body_iterator])


async def idempotency_middleware(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    key = request.headers.get("Idempotency-Key")
    if key is None or request.method not in WRITE_METHODS:
        return await call_next(request)  # nothing to do: reads, and writes without a key
    if not 1 <= len(key) <= MAX_KEY_LENGTH:
        return JSONResponse(status_code=400, content={"detail": f"Idempotency-Key must be 1 to {MAX_KEY_LENGTH} characters"})
    who = who_is_asking(request)
    if who is None:
        return await call_next(request)  # not logged in: the endpoint says 401, nothing to save

    # The database calls are normal (blocking) code: run_in_threadpool keeps them from holding up
    # every other request while they wait.
    body = await request.body()
    claim = await run_in_threadpool(claim_key, who[0], who[1], key, fingerprint(request, body))
    if claim.outcome == "different":
        return JSONResponse(status_code=422, content={"detail": "This Idempotency-Key was already used for a different request"})
    if claim.outcome == "running":
        return JSONResponse(status_code=409, content={"detail": "A request with this Idempotency-Key is still running"})
    if claim.outcome == "replay":
        return Response(content=claim.body, status_code=claim.status_code, media_type="application/json",
                        headers={"Idempotent-Replayed": "true"})

    # A new key: run the endpoint, then save its answer.
    try:
        response = await call_next(request)
    except Exception:
        await run_in_threadpool(forget_key, claim.row_id)
        raise
    if response.status_code >= 500:
        await run_in_threadpool(forget_key, claim.row_id)
        return response

    answer = await read_answer(response)
    await run_in_threadpool(save_answer, claim.row_id, response.status_code, answer.decode())
    return Response(content=answer, status_code=response.status_code, headers=dict(response.headers),
                    media_type=response.media_type)
