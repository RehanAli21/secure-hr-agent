# Everything about passwords and login tokens lives here.
#
# How login works:
#   1. The user sends email + password to /auth/login.
#   2. If they are correct, we give back two tokens:
#        - access token:  short-lived (15 minutes). Sent with every request.
#        - refresh token: long-lived (7 days). Only used to get a new access token.
#   3. Every protected endpoint reads the access token to know WHO is calling.
#
# There are two kinds of accounts:
#   - "user":      a normal login, linked to an employee (table: users)
#   - "superuser": a special login that can access everything (table: super_users)
# The token remembers which kind it is, because user #1 and superuser #1 are different people.
#
# A token is "signed" with our secret key, so nobody can fake or change one.

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import bcrypt
import jwt
from dotenv import load_dotenv
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SuperUser, User

load_dotenv()

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY")
if JWT_SECRET_KEY is None:
    raise RuntimeError("JWT_SECRET_KEY is missing. Add it to your .env file.")

ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = 15
REFRESH_TOKEN_DAYS = 7

# The logged-in account can be a normal User or a SuperUser.
Account = Union[User, SuperUser]


# ---------- Passwords ----------

def hash_password(password: str) -> str:
    """Turn a password into a hash we can safely store in the database."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    """Check if a password matches the stored hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ---------- Tokens ----------

def create_token(account_id: int, kind: str, token_type: str, lifetime: timedelta) -> str:
    """Make a signed token that says: this is account X of this kind, access/refresh, expires at Y."""
    payload: dict[str, Any] = {
        "sub": str(account_id),  # "sub" (subject) = who this token is for
        "kind": kind,            # "user" or "superuser"
        "type": token_type,      # "access" or "refresh"
        "exp": datetime.now(timezone.utc) + lifetime,  # "exp" = when it expires
    }
    # The "pyright: ignore" is only for Pylance strict mode: PyJWT's type hints mention an
    # optional package (cryptography) we don't use, so Pylance sees part of it as "unknown".
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=ALGORITHM)  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]


def create_access_token(account_id: int, kind: str) -> str:
    return create_token(account_id, kind, "access", timedelta(minutes=ACCESS_TOKEN_MINUTES))


def create_refresh_token(account_id: int, kind: str) -> str:
    return create_token(account_id, kind, "refresh", timedelta(days=REFRESH_TOKEN_DAYS))


def read_token(token: str, expected_type: str) -> tuple[int, str]:
    """Check a token and return (account id, kind). Stop with 401 if anything is wrong."""
    try:
        # jwt.decode checks the signature AND the expiry time for us.
        payload: dict[str, Any] = jwt.decode(token, JWT_SECRET_KEY, algorithms=[ALGORITHM])  # pyright: ignore[reportUnknownMemberType]
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")

    # A refresh token must not work as an access token, and the other way around.
    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail="Wrong type of token")

    kind = payload.get("kind")
    if kind not in ("user", "superuser"):
        raise HTTPException(status_code=401, detail="Invalid token")

    return int(payload["sub"]), kind


def load_account(db: Session, account_id: int, kind: str) -> Account:
    """Load the account from the right table. Stop with 401 if it is gone or disabled."""
    if kind == "superuser":
        superuser = db.get(SuperUser, account_id)
        if superuser is None:
            raise HTTPException(status_code=401, detail="User is not active")
        return superuser

    user = db.get(User, account_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User is not active")
    return user


# ---------- Who is calling? ----------

# This reads the "Authorization: Bearer <token>" header from the request.
# It also adds the "Authorize" button to the /docs page.
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Account:
    """Find the logged-in account from the access token. Used by every protected endpoint."""
    if credentials is None:
        raise HTTPException(status_code=401, detail="Not logged in")

    account_id, kind = read_token(credentials.credentials, expected_type="access")

    # Always load the account fresh from the database, so a disabled user is blocked right away.
    return load_account(db, account_id, kind)


def account_kind(account: Account) -> str:
    """ "superuser" or "user". We save this next to the id, because the ids of the two tables overlap."""
    return "superuser" if isinstance(account, SuperUser) else "user"


def is_superuser(account: Account) -> bool:
    """True if this account is a superuser. Superusers are allowed to do everything."""
    return isinstance(account, SuperUser)
