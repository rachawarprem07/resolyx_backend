from typing import Any, Dict
from fastapi import APIRouter, status
from sqlalchemy import text

from app.core.config import settings
from app.db.session import engine

router = APIRouter()


@router.get(
    "/health",
    status_code=status.HTTP_200_OK,
    summary="Health Check",
    response_description="Current API operational status",
)
def health_check() -> Dict[str, Any]:
    """Health check endpoint to verify backend service and database status."""
    db_status = "disconnected"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as exc:
        db_status = f"unavailable ({exc.__class__.__name__})"

    return {
        "status": "healthy",
        "database": db_status,
        "project": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT,
    }

