import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from fastapi import APIRouter, status

logger = logging.getLogger("resolva")
router = APIRouter()

DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "data"
ARTICLES_FILE = DATA_DIR / "kb_articles.json"
CATEGORIES_FILE = DATA_DIR / "kb_categories.json"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not ARTICLES_FILE.exists():
        ARTICLES_FILE.write_text("[]", encoding="utf-8")
    if not CATEGORIES_FILE.exists():
        CATEGORIES_FILE.write_text("[]", encoding="utf-8")


@router.get(
    "/articles",
    status_code=status.HTTP_200_OK,
    summary="Get All Knowledge Base Articles",
)
def get_articles() -> List[Dict[str, Any]]:
    """Returns all published knowledge base articles for all roles."""
    ensure_data_dir()
    try:
        content = ARTICLES_FILE.read_text(encoding="utf-8")
        articles = json.loads(content)
        if isinstance(articles, list):
            return articles
    except Exception as exc:
        logger.error(f"Error reading kb articles: {exc}")
    return []


@router.post(
    "/articles",
    status_code=status.HTTP_200_OK,
    summary="Save Knowledge Base Articles",
)
def save_articles(articles: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Persists knowledge base articles list."""
    ensure_data_dir()
    try:
        ARTICLES_FILE.write_text(json.dumps(articles, indent=2), encoding="utf-8")
        return {"success": True, "count": len(articles)}
    except Exception as exc:
        logger.error(f"Error saving kb articles: {exc}")
        return {"success": False, "error": str(exc)}


@router.get(
    "/categories",
    status_code=status.HTTP_200_OK,
    summary="Get All Knowledge Base Categories",
)
def get_categories() -> List[Dict[str, Any]]:
    """Returns all knowledge base categories."""
    ensure_data_dir()
    try:
        content = CATEGORIES_FILE.read_text(encoding="utf-8")
        categories = json.loads(content)
        if isinstance(categories, list):
            return categories
    except Exception as exc:
        logger.error(f"Error reading kb categories: {exc}")
    return []


@router.post(
    "/categories",
    status_code=status.HTTP_200_OK,
    summary="Save Knowledge Base Categories",
)
def save_categories(categories: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Persists knowledge base categories list."""
    ensure_data_dir()
    try:
        CATEGORIES_FILE.write_text(json.dumps(categories, indent=2), encoding="utf-8")
        return {"success": True, "count": len(categories)}
    except Exception as exc:
        logger.error(f"Error saving kb categories: {exc}")
        return {"success": False, "error": str(exc)}
