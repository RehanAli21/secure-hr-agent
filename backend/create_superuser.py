# Create a superuser account from the terminal.
# A superuser can access everything, so only create one for someone you fully trust.
#
# Run it from the backend folder:   python create_superuser.py

from getpass import getpass

from app.database import SessionLocal
from app.models import SuperUser
from app.security import hash_password


def main() -> None:
    email = input("Superuser email: ").strip()
    password = getpass("Password (hidden while you type): ")

    if len(password) < 8:
        print("Password must be at least 8 characters.")
        return

    db = SessionLocal()
    try:
        if db.query(SuperUser).filter(SuperUser.email == email).first() is not None:
            print("A superuser with this email already exists.")
            return

        superuser = SuperUser(email=email, password_hash=hash_password(password))
        db.add(superuser)
        db.commit()
        print(f"Created superuser {superuser.id} ({superuser.email}).")
    finally:
        db.close()


if __name__ == "__main__":
    main()
