# Resolyx FastAPI Backend

A production-ready, modular FastAPI backend built with Python 3.12, PostgreSQL, SQLAlchemy 2.0, Alembic, and Pydantic v2.

---

## Architecture Overview

```text
backend/
├── app/
│   ├── api/v1/          # HTTP request handlers & routing
│   ├── core/            # Configuration, logging, security & custom exceptions
│   ├── db/              # SQLAlchemy session & Base declarative models
│   ├── dependencies/    # FastAPI dependency injection helpers
│   ├── models/          # SQLAlchemy database models
│   ├── repositories/    # Database queries and persistence abstractions
│   ├── schemas/         # Pydantic v2 request/response models
│   ├── services/        # Domain business logic
│   ├── utils/           # Shared helper functions
│   └── main.py          # Application factory & ASGI entrypoint
├── alembic/             # Database migration environments & revisions
├── tests/               # Unit and integration test suites
├── scripts/             # Operational & deployment automation scripts
├── docs/                # Architecture & API documentation
├── alembic.ini          # Migration tool settings
├── Dockerfile           # Production container configuration
├── pyproject.toml       # Linter (Ruff), type-checker (MyPy) & dependencies
└── requirements.txt     # Python pinned dependencies
```

---

## Quick Start

### 1. Setup Virtual Environment
```bash
python -m venv .venv

# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate
```

### 2. Install Dependencies
```bash
pip install -r requirements.txt
```

### 3. Run Development Server
```bash
uvicorn app.main:app --reload --port 8000
```

- **Interactive Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
- **Health Check**: [http://localhost:8000/api/v1/health](http://localhost:8000/api/v1/health)

