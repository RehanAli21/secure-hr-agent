# Fill the database with realistic demo data for a company of about 550 people.
#
# Run it from the backend folder:
#   python seed.py           -> fills every table that is still empty
#   python seed.py --reset   -> deletes ALL demo data first, then fills everything again
#
# What it creates:
#   1. people:       departments, employees (a real org chart), a login with a role for everyone, a demo superuser
#   2. salaries:     a starting salary for everyone, plus a raise every year (full history)
#   3. leave:        annual, sick, unpaid and parental leave, approved/rejected by managers
#   4. reviews:      a performance review every half year, written by the manager
#
# Each part uses its own fixed random seed, so the data is the SAME every time you run it.

import random
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Callable, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal
from app.models import Department, Employee, LeaveRequest, PerformanceReview, Salary, SuperUser, User
from app.security import hash_password

DEMO_PASSWORD = "Password123!"
SUPERUSER_EMAIL = "admin@gatekeeper.local"
EMAIL_DOMAIN = "gatekeeper.local"

# We pretend "today" is this date, so the data never changes depending on when you run it.
TODAY = date(2026, 11, 1)

# (department name, how many staff, job titles for normal staff)
DEPARTMENTS: list[tuple[str, int, list[str]]] = [
    ("Engineering", 140, ["Software Engineer", "Senior Software Engineer", "QA Engineer", "DevOps Engineer", "Data Engineer"]),
    ("Sales", 90, ["Account Executive", "Sales Development Rep", "Senior Account Executive"]),
    ("Customer Support", 70, ["Support Specialist", "Senior Support Specialist", "Technical Support Engineer"]),
    ("Operations", 50, ["Operations Analyst", "Logistics Coordinator", "Office Administrator"]),
    ("Marketing", 40, ["Marketing Specialist", "Content Writer", "Graphic Designer", "SEO Specialist"]),
    ("Product", 30, ["Associate Product Manager", "Product Designer", "UX Researcher"]),
    ("Finance", 25, ["Accountant", "Financial Analyst", "Payroll Specialist"]),
    ("Human Resources", 20, ["HR Generalist", "Recruiter", "HR Business Partner"]),
    ("IT", 15, ["IT Support Technician", "System Administrator", "Security Analyst"]),
    ("Legal", 10, ["Legal Counsel", "Paralegal", "Compliance Officer"]),
]

FIRST_NAMES = [
    "Ali", "Sara", "Ahmed", "Fatima", "Usman", "Ayesha", "Hamza", "Zainab", "Bilal", "Hira",
    "Omar", "Maryam", "Hassan", "Amna", "Saad", "Sana", "Imran", "Mahnoor", "Faisal", "Iqra",
    "John", "Emma", "Michael", "Olivia", "David", "Sophia", "James", "Isabella", "Daniel", "Mia",
    "Arjun", "Priya", "Wei", "Mei", "Carlos", "Lucia", "Yusuf", "Leila", "Kenji", "Aiko",
    "Noah", "Ava", "Liam", "Chloe", "Ethan", "Grace", "Ryan", "Nadia", "Adam", "Zara",
]

LAST_NAMES = [
    "Khan", "Ahmed", "Malik", "Hussain", "Raza", "Sheikh", "Qureshi", "Butt", "Chaudhry", "Siddiqui",
    "Smith", "Johnson", "Brown", "Williams", "Jones", "Miller", "Davis", "Wilson", "Taylor", "Clark",
    "Patel", "Sharma", "Chen", "Wang", "Garcia", "Lopez", "Rahman", "Ali", "Tanaka", "Kim",
    "Anderson", "Thomas", "Moore", "Martin", "Lee", "Walker", "Young", "Hall", "Allen", "King",
]


# =====================================================================
# Small helpers
# =====================================================================

def random_date(start: date, end: date) -> date:
    """A random day between start and end."""
    days = (end - start).days
    return start + timedelta(days=random.randint(0, days))


def at_9am(day: date) -> datetime:
    """Turn a date into a timestamp (9:00 UTC on that day)."""
    return datetime.combine(day, time(9, 0), tzinfo=timezone.utc)


def pick_termination_date(employee: Employee) -> date:
    """Pick the last working day for someone who left: at least 4 months after they started.

    random.Random(employee.id) always gives the same date for the same person.
    """
    earliest = employee.start_date + timedelta(days=120)
    if earliest >= TODAY:
        return TODAY
    return earliest + timedelta(days=random.Random(employee.id).randint(0, (TODAY - earliest).days))


STREETS = ["Oak Street", "Maple Avenue", "Park Road", "Hill Lane", "River Drive", "Lake View", "Garden Way", "Station Road"]
CITIES = ["Springfield", "Riverton", "Lakeside", "Fairview", "Greenville", "Brookfield"]


def set_contact_details(employee: Employee) -> None:
    """Give an employee a made-up phone number and home address.

    random.Random(f"contact-{id}") always gives the same details for the same person,
    and it does not change any of the other random data.
    """
    rng = random.Random(f"contact-{employee.id}")
    employee.phone = f"+1 555 {rng.randint(100, 999)} {rng.randint(1000, 9999)}"
    employee.home_address = f"{rng.randint(1, 250)} {rng.choice(STREETS)}, {rng.choice(CITIES)}"


def last_day_worked(employee: Employee) -> date:
    """The last day this person worked here: their termination date, or today if they still work here."""
    if employee.termination_date is not None:
        return employee.termination_date
    return TODAY


# =====================================================================
# 1. People: departments, employees, logins
# =====================================================================

used_emails: set[str] = set()


def staff_start_date() -> date:
    """A start date between 2015 and today, with MORE hires in recent years (a growing company)."""
    # triangular(low, high, mode): most values land near "mode", which here is today.
    days_ago = int(random.triangular(0, (TODAY - date(2015, 1, 1)).days, 0))
    return TODAY - timedelta(days=days_ago)


def make_person(job_title: str, department: Department, manager: Optional[Employee], start_date: date) -> Employee:
    """Build one employee with a random name and a unique email."""
    first = random.choice(FIRST_NAMES)
    last = random.choice(LAST_NAMES)

    # first.last@... and if that is taken: first.last2@..., first.last3@..., and so on
    base = f"{first}.{last}".lower()
    email = f"{base}@{EMAIL_DOMAIN}"
    number = 2
    while email in used_emails:
        email = f"{base}{number}@{EMAIL_DOMAIN}"
        number += 1
    used_emails.add(email)

    return Employee(
        name=f"{first} {last}",
        email=email,
        job_title=job_title,
        department_id=department.id,
        manager_id=manager.id if manager is not None else None,
        start_date=start_date,
        employment_status="active",
    )


def pick_role(employee: Employee, hr_department_id: int, manager_ids: set[int]) -> str:
    """Give each login a role that fits the person's job."""
    if employee.job_title == "Head of IT":
        return "admin"     # IT runs the system
    if employee.department_id == hr_department_id:
        return "hr"        # everyone in Human Resources
    if employee.id in manager_ids:
        return "manager"   # anyone who has people reporting to them
    return "employee"


def seed_people(db: Session) -> None:
    random.seed(42)

    # ---------- The executive team ----------
    executive = Department(name="Executive")
    db.add(executive)
    db.flush()  # flush = send to the database now, so we get the new id

    ceo = make_person("Chief Executive Officer", executive, None, date(2015, 1, 5))
    db.add(ceo)
    db.flush()
    db.add(make_person("Executive Assistant", executive, ceo, date(2016, 3, 1)))

    # ---------- Every other department ----------
    for department_name, staff_count, staff_titles in DEPARTMENTS:
        department = Department(name=department_name)
        db.add(department)
        db.flush()

        # The head of the department reports to the CEO. Heads joined early.
        head = make_person(f"Head of {department_name}", department, ceo, random_date(date(2015, 1, 1), date(2018, 12, 31)))
        db.add(head)
        db.flush()

        # About one manager for every 10 staff. Managers report to the head.
        manager_count = max(1, staff_count // 10)
        managers: list[Employee] = []
        for _ in range(manager_count):
            manager = make_person(f"{department_name} Manager", department, head, random_date(date(2015, 6, 1), date(2022, 12, 31)))
            db.add(manager)
            managers.append(manager)
        db.flush()

        # Normal staff, each with a random manager in their department.
        for _ in range(staff_count):
            person = make_person(random.choice(staff_titles), department, random.choice(managers), staff_start_date())

            # About 8% of staff have left the company (only people who joined before this year).
            if person.start_date.year < 2026 and random.random() < 0.08:
                person.employment_status = "terminated"

            db.add(person)

    db.flush()

    # ---------- When did the terminated people leave? ----------
    # We do this after flush(), because pick_termination_date needs each person's id.
    for employee in db.query(Employee).filter(Employee.employment_status == "terminated").all():
        employee.termination_date = pick_termination_date(employee)

    # ---------- Phone numbers and home addresses ----------
    for employee in db.query(Employee).all():
        set_contact_details(employee)

    # ---------- A login for every employee ----------
    # Hashing a password is slow ON PURPOSE (it protects against attackers).
    # So we hash the demo password once and reuse it. Only OK for demo data!
    demo_hash = hash_password(DEMO_PASSWORD)

    employees = db.query(Employee).all()
    hr_department = db.query(Department).filter(Department.name == "Human Resources").one()
    # Everyone who appears as somebody's manager.
    manager_ids = {employee.manager_id for employee in employees if employee.manager_id is not None}

    for employee in employees:
        db.add(User(
            email=employee.email,
            password_hash=demo_hash,
            employee_id=employee.id,
            role=pick_role(employee, hr_department.id, manager_ids),
            is_active=employee.employment_status == "active",
        ))

    if db.query(SuperUser).filter(SuperUser.email == SUPERUSER_EMAIL).first() is None:
        db.add(SuperUser(email=SUPERUSER_EMAIL, password_hash=demo_hash))

    db.flush()


# =====================================================================
# 2. Salaries
# =====================================================================

# Some departments pay more than others.
DEPARTMENT_PAY = {
    "Executive": 1.0, "Engineering": 1.2, "Product": 1.15, "Legal": 1.15, "Finance": 1.05, "IT": 1.0,
    "Sales": 0.95, "Marketing": 0.95, "Human Resources": 0.9, "Operations": 0.85, "Customer Support": 0.8,
}

JUNIOR_WORDS = ["Assistant", "Coordinator", "Administrator", "Rep", "Technician", "Paralegal"]


def base_salary(job_title: str, department_name: str) -> float:
    """A typical yearly salary (in 2026 dollars) for this job."""
    if job_title.startswith("Chief"):
        level = 250_000
    elif job_title.startswith("Head of"):
        level = 160_000
    elif job_title == f"{department_name} Manager":
        level = 110_000
    elif "Senior" in job_title:
        level = 95_000
    elif any(word in job_title for word in JUNIOR_WORDS):
        level = 50_000
    else:
        level = 70_000
    return level * DEPARTMENT_PAY[department_name]


def round_to_100(amount: float) -> Decimal:
    return Decimal(round(amount / 100) * 100)


def seed_salaries(db: Session) -> None:
    random.seed(43)
    department_names = {d.id: d.name for d in db.query(Department).all()}

    for employee in db.query(Employee).order_by(Employee.id).all():
        last_day = last_day_worked(employee)

        # Salaries were lower in the past, about 3% less for every year back.
        years_ago = TODAY.year - employee.start_date.year
        amount = base_salary(employee.job_title, department_names[employee.department_id])
        amount = amount * (0.97 ** years_ago) * random.uniform(0.9, 1.1)

        # The starting salary.
        db.add(Salary(employee_id=employee.id, amount=round_to_100(amount), effective_date=employee.start_date))

        # A raise on every work anniversary, while they still worked here.
        year = employee.start_date.year + 1
        while True:
            # date(..., 28) if they started on Feb 29, because not every year has a Feb 29
            day = min(employee.start_date.day, 28) if employee.start_date.month == 2 else employee.start_date.day
            anniversary = date(year, employee.start_date.month, day)
            if anniversary > last_day:
                break

            if random.random() < 0.15:
                raise_percent = random.uniform(8, 15)  # a promotion
            else:
                raise_percent = random.uniform(2, 6)   # a normal yearly raise
            amount = amount * (1 + raise_percent / 100)

            db.add(Salary(employee_id=employee.id, amount=round_to_100(amount), effective_date=anniversary))
            year += 1

    db.flush()


# =====================================================================
# 3. Leave requests
# =====================================================================

LEAVE_REASONS = {
    "annual": ["Family vacation", "Wedding", "Personal time", "Travel", "Moving house", "Eid holidays"],
    "sick": ["Flu", "Fever", "Doctor appointment", "Migraine", "Dental surgery", "Back pain"],
    "unpaid": ["Personal reasons", "Extended travel", "Family matter"],
    "parental": ["Birth of child", "Adoption"],
}

# (leave type, shortest, longest in days)
LEAVE_LENGTHS = {"annual": (1, 10), "sick": (1, 4), "unpaid": (3, 20), "parental": (30, 90)}


def make_leave(employee: Employee, leave_type: str, start: date, pending: bool) -> LeaveRequest:
    shortest, longest = LEAVE_LENGTHS[leave_type]
    end = start + timedelta(days=random.randint(shortest, longest) - 1)
    reason = random.choice(LEAVE_REASONS[leave_type])

    if pending:
        # Not decided yet: no approver, no decision date.
        return LeaveRequest(
            employee_id=employee.id, leave_type=leave_type, start_date=start, end_date=end,
            reason=reason, status="pending", created_at=at_9am(TODAY - timedelta(days=random.randint(0, 10))),
        )

    roll = random.random()
    status = "approved" if roll < 0.88 else "rejected" if roll < 0.96 else "cancelled"
    decided_on = start - timedelta(days=random.randint(1, 14))

    return LeaveRequest(
        employee_id=employee.id, leave_type=leave_type, start_date=start, end_date=end, reason=reason,
        status=status,
        approver_id=employee.manager_id,  # the manager decides (the CEO has no manager, so None)
        decided_at=at_9am(decided_on),
        created_at=at_9am(decided_on - timedelta(days=random.randint(1, 7))),
    )


def seed_leave(db: Session) -> None:
    random.seed(44)

    for employee in db.query(Employee).order_by(Employee.id).all():
        last_day = last_day_worked(employee)

        # Leave in the past: go through every year they worked here.
        for year in range(employee.start_date.year, last_day.year + 1):
            window_start = max(employee.start_date, date(year, 1, 1))
            window_end = min(last_day, date(year, 12, 31))
            if (window_end - window_start).days < 30:
                continue

            requests = ["annual"] * random.randint(1, 4) + ["sick"] * random.randint(0, 3)
            if random.random() < 0.05:
                requests.append("unpaid")
            if random.random() < 0.02:
                requests.append("parental")

            for leave_type in requests:
                latest_start = window_end - timedelta(days=LEAVE_LENGTHS[leave_type][1])
                if latest_start < window_start:
                    continue  # this leave is too long to fit in the time they worked this year
                start = random_date(window_start, latest_start)
                db.add(make_leave(employee, leave_type, start, pending=False))

        # Some active people have asked for leave in the coming weeks. It is still pending.
        if employee.employment_status == "active" and random.random() < 0.15:
            start = TODAY + timedelta(days=random.randint(7, 60))
            db.add(make_leave(employee, "annual", start, pending=True))

    db.flush()


# =====================================================================
# 4. Performance reviews
# =====================================================================

COMMENTS = {
    1: ["Did not meet most goals this period. Needs a clear improvement plan.",
        "Frequent missed deadlines. We agreed on weekly check-ins."],
    2: ["Met some goals, but quality was uneven. Needs more focus.",
        "Some progress, but still needs regular support from the team."],
    3: ["Solid work. Met expectations on all main goals.",
        "Reliable and steady. Delivered what was planned."],
    4: ["Strong period. Often went beyond what was asked.",
        "Great results and a big help to teammates."],
    5: ["Outstanding work. A role model for the team.",
        "Exceptional results. Ready for more responsibility."],
}


def review_periods() -> list[tuple[str, date]]:
    """Every finished half year from 2022 until today: [("2022-H1", June 30), ("2022-H2", Dec 31), ...]"""
    periods: list[tuple[str, date]] = []
    for year in range(2022, TODAY.year + 1):
        for name, period_end in ((f"{year}-H1", date(year, 6, 30)), (f"{year}-H2", date(year, 12, 31))):
            if period_end < TODAY:
                periods.append((name, period_end))
    return periods


def seed_reviews(db: Session) -> None:
    random.seed(45)
    periods = review_periods()

    for employee in db.query(Employee).order_by(Employee.id).all():
        if employee.manager_id is None:
            continue  # nobody reviews the CEO

        # Every person has their own "talent" level, so their ratings stay similar over time.
        # People who later left the company were, on average, rated a bit lower.
        talent = random.gauss(3.3, 0.6)
        if employee.employment_status == "terminated":
            talent -= 0.6

        last_day = last_day_worked(employee)
        for period, period_end in periods:
            # Only review people who worked here for 3+ months of that period and were still here at the end.
            if employee.start_date > period_end - timedelta(days=90) or last_day < period_end:
                continue

            rating = max(1, min(5, round(talent + random.gauss(0, 0.6))))
            db.add(PerformanceReview(
                employee_id=employee.id,
                reviewer_id=employee.manager_id,  # the manager writes the review
                period=period,
                rating=rating,
                comments=random.choice(COMMENTS[rating]),
                created_at=at_9am(period_end + timedelta(days=random.randint(7, 30))),
            ))

    db.flush()


# =====================================================================
# Run everything
# =====================================================================

def reset(db: Session) -> None:
    """Delete all demo data. Superusers you created yourself are kept."""
    # TRUNCATE empties tables fast. RESTART IDENTITY makes the ids start at 1 again.
    db.execute(text(
        "TRUNCATE audit_log, pending_actions, leave_requests, salaries, performance_reviews, users, employees, departments "
        "RESTART IDENTITY CASCADE"
    ))
    db.query(SuperUser).filter(SuperUser.email == SUPERUSER_EMAIL).delete()
    db.commit()
    print("All demo data deleted.\n")


def run_part(db: Session, name: str, table: type[Base], seed_function: Callable[[Session], None]) -> None:
    """Run one part of the seed, but only if its table is still empty."""
    if db.query(table).first() is not None:
        print(f"{name:<12} skipped (already has data)")
        return
    seed_function(db)
    print(f"{name:<12} created")


def main() -> None:
    db = SessionLocal()
    try:
        if "--reset" in sys.argv:
            answer = input("This deletes ALL employees, logins, salaries, leave, reviews and the audit log. Type yes to continue: ")
            if answer.strip().lower() != "yes":
                print("Nothing deleted.")
                return
            reset(db)

        run_part(db, "people", Employee, seed_people)
        run_part(db, "salaries", Salary, seed_salaries)
        run_part(db, "leave", LeaveRequest, seed_leave)
        run_part(db, "reviews", PerformanceReview, seed_reviews)
        db.commit()

        print_summary(db)
    finally:
        db.close()


def print_summary(db: Session) -> None:
    """Print what is in the database, so we can check it looks right."""
    print("\nWhat is in the database now:")
    tables: list[tuple[str, type[Base]]] = [
        ("departments", Department), ("employees", Employee), ("logins", User),
        ("salaries", Salary), ("leave", LeaveRequest), ("reviews", PerformanceReview),
    ]
    for name, table in tables:
        print(f"  {name:<12}{db.query(table).count():>6}")

    print("\nLogins per role:")
    for role in ("employee", "manager", "hr", "admin"):
        print(f"  {role:<12}{db.query(User).filter(User.role == role).count():>6}")

    print(f"\nLog in as any employee with password: {DEMO_PASSWORD}")
    print(f"Superuser: {SUPERUSER_EMAIL} / {DEMO_PASSWORD}  (use /auth/superuser/login)")


if __name__ == "__main__":
    main()
