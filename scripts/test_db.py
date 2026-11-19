"""
Simple database connectivity test script.
Run from backend directory: python scripts/test_db.py
"""
import sys
from pathlib import Path

# Add backend root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from app.core.config import settings
from app.db.session import engine


def test_connection() -> bool:
    print(f"Testing database connection to: {settings.POSTGRES_SERVER}:{settings.POSTGRES_PORT} (DB: {settings.POSTGRES_DB})")
    try:
        with engine.connect() as conn:
            result = conn.execute(text("SELECT version();")).scalar()
            print("Successfully connected to PostgreSQL!")
            print(f"Database version: {result}")
            return True
    except Exception as exc:
        print(f"Database connection failed: {exc}")
        return False


if __name__ == "__main__":
    success = test_connection()
    sys.exit(0 if success else 1)

