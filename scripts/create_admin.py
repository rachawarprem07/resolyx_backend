"""
Script to create or update an Admin user in PostgreSQL to verify database connectivity.
Run from backend directory: python scripts/create_admin.py
"""
import sys
from pathlib import Path

# Add backend root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select
from app.core.security import hash_password, verify_password
from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.company import Company
from app.models.user import User

ADMIN_EMAIL = "admin@resolyx.com"
ADMIN_PASSWORD = "resolyx@123"
ADMIN_FULL_NAME = "System Administrator"


def create_or_update_admin() -> None:
    print("=" * 60)
    print("Resolyx Database Admin Seeder")
    print("=" * 60)

    # 1. Ensure database tables exist
    print("1. Creating database tables if not present...")
    Base.metadata.create_all(bind=engine)
    print("   Tables verified.")

    # 2. Open DB Session
    with SessionLocal() as db:
        # Check if user already exists
        query = select(User).where(User.email == ADMIN_EMAIL)
        existing_user = db.scalar(query)

        hashed = hash_password(ADMIN_PASSWORD)

        company = db.scalar(select(Company).limit(1))
        if not company:
            print("   Creating default company workspace...")
            company = Company(name="Resolyx Inc", currency="USD", timezone="UTC")
            db.add(company)
            db.flush()

        if existing_user:
            print(f"2. User '{ADMIN_EMAIL}' already exists. Updating password...")
            existing_user.hashed_password = hashed
            existing_user.role = "super_admin"
            existing_user.is_superuser = True
            existing_user.is_active = True
            existing_user.company_id = company.id
            db.commit()
            db.refresh(existing_user)
            user_record = existing_user
            print("   Password updated.")
        else:
            print(f"2. Creating new admin user '{ADMIN_EMAIL}'...")
            new_user = User(
                company_id=company.id,
                email=ADMIN_EMAIL,
                hashed_password=hashed,
                full_name=ADMIN_FULL_NAME,
                role="super_admin",
                is_active=True,
                is_superuser=True,
            )
            db.add(new_user)
            db.commit()
            db.refresh(new_user)
            user_record = new_user
            print("   User created successfully.")

        # 3. Verify inserted data
        print("3. Verifying credentials against database record...")
        is_valid = verify_password(ADMIN_PASSWORD, user_record.hashed_password)

        if is_valid:
            print("   Credential verification: PASSED (Argon2 hash verified)")
            print(f"   User ID: {user_record.id}")
            print(f"   Email: {user_record.email}")
            print(f"   Role: {user_record.role}")
            print(f"   Is Active: {user_record.is_active}")
            print(f"   Is Superuser: {user_record.is_superuser}")
            print(f"   Created At: {user_record.created_at}")
            print("=" * 60)
            print("Database is fully connected and admin record is ready!")
            print("=" * 60)
        else:
            print("   Credential verification: FAILED")
            sys.exit(1)


if __name__ == "__main__":
    create_or_update_admin()

