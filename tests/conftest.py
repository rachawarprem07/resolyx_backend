"""Shared fixtures: an isolated in-memory SQLite database per test."""

from typing import Callable, Dict, Iterator, List, Optional

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register all tables)
from app.core.security import create_access_token
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app
from app.models.company import Company
from app.models.team import Team, TeamMember
from app.models.user import User


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(eng, "connect")
    def _fk_on(dbapi_conn, _):  # enforce ON DELETE behaviour like Postgres
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def db(engine) -> Iterator[Session]:
    session = sessionmaker(bind=engine, autoflush=False, autocommit=False)()
    yield session
    session.close()


@pytest.fixture()
def company(db: Session) -> Company:
    c = Company(name="Acme Support")
    db.add(c)
    db.commit()
    return c


@pytest.fixture()
def make_user(db: Session, company: Company) -> Callable[..., User]:
    counter = {"n": 0}

    def _make(
        name: str, role: str = "support_agent", active: bool = True, company_id: Optional[int] = -1
    ) -> User:
        counter["n"] += 1
        user = User(
            company_id=company.id if company_id == -1 else company_id,
            email=f"{name.lower().replace(' ', '.')}.{counter['n']}@acme.test",
            full_name=name,
            hashed_password="x",
            role=role,
            is_active=active,
        )
        db.add(user)
        db.commit()
        return user

    return _make


@pytest.fixture()
def admin(make_user) -> User:
    return make_user("Ada Admin", role="admin")


@pytest.fixture()
def agents(make_user) -> List[User]:
    return [make_user("Alice Agent"), make_user("Bob Agent"), make_user("Cara Agent")]


@pytest.fixture()
def customer(make_user) -> User:
    return make_user("Carl Customer", role="customer")


@pytest.fixture()
def team(db: Session, company: Company, agents: List[User]) -> Team:
    t = Team(company_id=company.id, name="Tier 1")
    t.members = [TeamMember(user_id=a.id) for a in agents]
    db.add(t)
    db.commit()
    return t


@pytest.fixture()
def client(db: Session) -> Iterator[TestClient]:
    fastapi_app.dependency_overrides[get_db] = lambda: db
    # Not entered as a context manager, so the lifespan (Postgres create_all) never runs.
    yield TestClient(fastapi_app)
    fastapi_app.dependency_overrides.clear()


def auth(user: User) -> Dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.email)}"}


def cond(field: str, operator: str, value=None) -> Dict:
    return {"type": "condition", "field": field, "operator": operator, "value": value}


def group(logic: str, *children) -> Dict:
    return {"type": "group", "logic": logic, "children": list(children)}
