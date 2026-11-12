from datetime import datetime

from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey

from app.database import Base


class Department(Base):
    __tablename__ = "departments"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)


class Employee(Base):
    __tablename__ = "employees"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    job_title = Column(String, nullable=False)

    # Which department this employee belongs to.
    department_id = Column(Integer, ForeignKey("departments.id"), nullable=False)

    # Who this employee reports to. It points to another row in this same table.
    # It is empty (None) for the person at the top.
    manager_id = Column(Integer, ForeignKey("employees.id"), nullable=True)

    # "active" or "terminated". We never delete employees, we only terminate them.
    employment_status = Column(String, nullable=False, default="active")

    start_date = Column(Date, nullable=False)

    # When the row was created and last changed.
    # updated_at will later help us notice if a record changed before a user confirms an action.
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
