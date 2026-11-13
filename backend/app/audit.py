# The audit log: every change anybody makes is written down here.
#
# How an endpoint uses it:
#   before = snapshot(employee)                  # 1. copy the row BEFORE changing it (skip for new rows)
#   employee.job_title = "Senior Developer"      # 2. make the change
#   db.flush()                                   # 3. send it to the database (gives new rows their id)
#   record_audit(db, account, origin, "update_employee", employee, before)   # 4. write it down
#   db.commit()                                  # 5. save the change AND the audit row together
#
# Because the change and its audit row are saved in ONE commit, you can never have one without the other.

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from fastapi import Header
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.database import Base
from app.models import AuditLog
from app.security import Account, account_kind


@dataclass
class Origin:
    """Where a change comes from.

    source:            "ui" (the person used the app) or "agent" (the AI assistant asked for it)
    pending_action_id: set when the change runs because the user confirmed a pending action
    """

    source: str
    pending_action_id: Optional[int] = None


def get_origin(x_request_source: Optional[str] = Header(default=None)) -> Origin:
    """Read the "X-Request-Source" header. Our AI agent service sends "agent". Everything else is "ui"."""
    if x_request_source == "agent":
        return Origin(source="agent")
    return Origin(source="ui")


def snapshot(record: Base) -> dict[str, Any]:
    """Copy every column of a row into a dict, e.g. {"id": 7, "name": "Sara", ...}.

    jsonable_encoder turns dates into text, so the dict can be stored as JSON.
    custom_encoder={Decimal: str} keeps money exact ("71234.50"). Without it, money would become a float.
    """
    data: dict[str, Any] = {}
    for column in record.__table__.columns:
        if column.name == "password_hash":
            continue  # never copy password hashes into the log
        data[column.name] = getattr(record, column.name)
    return jsonable_encoder(data, custom_encoder={Decimal: str})


def record_audit(
    db: Session,
    account: Account,
    origin: Origin,
    action: str,
    record: Base,
    before: Optional[dict[str, Any]],
) -> None:
    """Add one audit row. It is saved by the endpoint's db.commit(), together with the change."""
    after = snapshot(record)
    db.add(AuditLog(
        account_kind=account_kind(account),
        account_id=account.id,
        action=action,
        table_name=str(record.__tablename__),
        record_id=after["id"],
        before=before,
        after=after,
        source=origin.source,
        pending_action_id=origin.pending_action_id,
    ))
