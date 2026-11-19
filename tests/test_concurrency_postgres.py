"""Concurrent ticket creation against real PostgreSQL row locks.

SQLite has no SELECT ... FOR UPDATE, so these tests only run when
``TEST_DATABASE_URL`` points at a disposable PostgreSQL database, e.g.::

    TEST_DATABASE_URL=postgresql+psycopg2://postgres:pw@localhost:5432/resolyx_test pytest

All tables in that database are dropped and recreated.
"""

import os
import threading
from collections import Counter

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.models  # noqa: F401
from app.db.base import Base
from app.models.company import Company
from app.models.team import Team, TeamMember
from app.models.user import User
from app.schemas.assignment_rule import AssignmentRuleCreate
from app.schemas.ticket import TicketCreate
from app.services.assignment.rule_service import AssignmentRuleService
from app.services.ticket_service import TicketService
from tests.conftest import cond, group

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL (PostgreSQL) not set")

WORKERS = 12
AGENTS = 4


@pytest.fixture()
def pg():
    engine = create_engine(URL, pool_size=WORKERS + 2, max_overflow=0)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    with Session() as s:
        company = Company(name="Concurrent Co")
        s.add(company)
        s.flush()
        admin = User(company_id=company.id, email="admin@cc.test", hashed_password="x", role="admin", full_name="Admin")
        agents = [User(company_id=company.id, email=f"a{i}@cc.test", hashed_password="x",
                       role="support_agent", full_name=f"Agent {i}") for i in range(AGENTS)]
        s.add_all([admin, *agents])
        s.flush()
        team = Team(company_id=company.id, name="Pool", members=[TeamMember(user_id=a.id) for a in agents])
        s.add(team)
        s.commit()
        ids = {"admin": admin.id, "team": team.id, "agents": [a.id for a in agents]}
    yield Session, ids
    engine.dispose()
    Base.metadata.drop_all(create_engine(URL))


def _make_rule(Session, ids, assignment_type):
    with Session() as s:
        admin = s.get(User, ids["admin"])
        AssignmentRuleService(s, admin).create(AssignmentRuleCreate(
            name=assignment_type, triggers=["ticket_created"],
            conditions=group("AND", cond("status", "is", "Open")),
            assignment_type=assignment_type, assignment_config={"team_id": ids["team"]},
        ))


def _create_concurrently(Session, ids, n):
    barrier = threading.Barrier(n)
    results, errors = [], []

    def worker(i):
        try:
            with Session() as s:
                admin = s.get(User, ids["admin"])
                barrier.wait()
                t = TicketService(s, admin).create(TicketCreate(subject=f"Concurrent {i}"))
                results.append(t.assigned_agent_id)
        except Exception as exc:  # pragma: no cover - surfaced by the assertion below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors
    return results


def test_concurrent_round_robin_distributes_evenly(pg):
    Session, ids = pg
    _make_rule(Session, ids, "round_robin")
    results = _create_concurrently(Session, ids, WORKERS)
    counts = Counter(results)
    assert None not in counts
    # 12 tickets over 4 agents: a lost update would give someone more than 3.
    assert sorted(counts.values()) == [WORKERS // AGENTS] * AGENTS


def test_concurrent_updates_of_one_ticket_on_postgres(pg):
    """Row-locking an existing ticket (eager-loaded joins) works on PostgreSQL, and
    parallel updates of the same ticket serialize without errors."""
    from app.schemas.ticket import TicketUpdate

    Session, ids = pg
    _make_rule(Session, ids, "round_robin")
    with Session() as s:
        number = TicketService(s, s.get(User, ids["admin"])).create(TicketCreate(subject="shared")).ticket_number

    barrier = threading.Barrier(6)
    errors = []

    def worker(i):
        try:
            with Session() as s:
                barrier.wait()
                TicketService(s, s.get(User, ids["admin"])).update(number, TicketUpdate(subject=f"edit {i}"))
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert not errors, errors


def test_concurrent_least_assigned_stays_balanced(pg):
    Session, ids = pg
    _make_rule(Session, ids, "least_assigned")
    counts = Counter(_create_concurrently(Session, ids, WORKERS))
    assert None not in counts
    assert max(counts.values()) - min(counts.values()) == 0
