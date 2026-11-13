# Read the audit log: who changed what, and when.
# Only hr/admin may read it. Nobody can change or delete audit rows: there are no endpoints for that.

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AuditLog
from app.permissions import HR_ROLES, require_role
from app.schemas import AuditLogOut
from app.security import Account, get_current_user

router = APIRouter(prefix="/audit-log", tags=["Audit log"], dependencies=[Depends(get_current_user)])


@router.get("", response_model=list[AuditLogOut])
def read_audit_log(
    table_name: Optional[str] = None,  # e.g. "employees"
    record_id: Optional[int] = None,   # e.g. 7 = the history of row 7
    action: Optional[str] = None,      # e.g. "terminate_employee"
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    account: Account = Depends(get_current_user),
):
    """The newest changes first."""
    require_role(account, HR_ROLES)

    query = db.query(AuditLog)
    if table_name is not None:
        query = query.filter(AuditLog.table_name == table_name)
    if record_id is not None:
        query = query.filter(AuditLog.record_id == record_id)
    if action is not None:
        query = query.filter(AuditLog.action == action)

    return query.order_by(AuditLog.id.desc()).limit(limit).all()
