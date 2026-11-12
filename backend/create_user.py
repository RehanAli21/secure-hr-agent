# Create a login account from the terminal.
# We need this for the very first user, because you can't log in to the API before any user exists.
#
# Run it from the backend folder:   python create_user.py

from getpass import getpass

from app.database import SessionLocal
from app.models import Employee, User
from app.security import hash_password


def main() -> None:
    email = input("Email: ").strip()
    password = getpass("Password (hidden while you type): ")
    employee_id_text = input("Employee id (press Enter to skip): ").strip()

    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return

    db = SessionLocal()
    try:
        if db.query(User).filter(User.email == email).first() is not None:
            print("A user with this email already exists.")
            return

        employee_id = None
        if employee_id_text != "":
            if not employee_id_text.isdigit():
                print("Employee id must be a number.")
                return
            employee_id = int(employee_id_text)
            if db.get(Employee, employee_id) is None:
                print("No employee with this id.")
                return

        user = User(email=email, password_hash=hash_password(password), employee_id=employee_id)
        db.add(user)
        db.commit()
        print(f"Created user {user.id} ({user.email}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
