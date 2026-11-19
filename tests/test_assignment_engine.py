"""Engine behaviour exercised through the real ticket workflows."""

from typing import Any, Dict, List

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assignment_rule import AssignmentRotation, AssignmentRuleExecution
from app.models.team import Team
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.assignment_rule import AssignmentRuleCreate
from app.schemas.ticket import TicketCreate, TicketUpdate
from app.services.assignment import engine as engine_module
from app.services.assignment.agent_selection import AgentSelectionService
from app.services.assignment.engine import AssignmentEngine
from app.services.assignment.rule_service import AssignmentRuleService
from app.services.ticket_service import TicketService
from tests.conftest import cond, group

HIGH = group("AND", cond("priority", "is", "High"))
ANY_OPEN = group("AND", cond("status", "is", "Open"))


@pytest.fixture()
def make_rule(db: Session, admin: User):
    def _make(name: str, assignment_type: str, config: Dict[str, Any], **kw):
        payload = AssignmentRuleCreate(
            name=name,
            triggers=kw.pop("triggers", ["ticket_created"]),
            conditions=kw.pop("conditions", ANY_OPEN),
            assignment_type=assignment_type,
            assignment_config=config,
            **kw,
        )
        return AssignmentRuleService(db, admin).create(payload)

    return _make


@pytest.fixture()
def tickets(db: Session, admin: User) -> TicketService:
    return TicketService(db, admin)


def new_ticket(svc: TicketService, **kw) -> Ticket:
    return svc.create(TicketCreate(subject=kw.pop("subject", "Help"), **kw))


def executions(db: Session, ticket: Ticket) -> List[AssignmentRuleExecution]:
    return list(db.scalars(
        select(AssignmentRuleExecution).where(AssignmentRuleExecution.ticket_id == ticket.id)
        .order_by(AssignmentRuleExecution.id)
    ))


# ----------------------------------------------------------- assignment types
def test_direct_agent_assignment_on_create(db, tickets, make_rule, agents):
    rule = make_rule("High to Bob", "agent", {"agent_id": agents[1].id}, conditions=HIGH)
    t = new_ticket(tickets, priority="High")
    assert t.assigned_agent_id == agents[1].id
    assert t.assignment_source == "rule" and t.assigned_by_rule_id == rule.id
    [ex] = executions(db, t)
    assert ex.execution_status == "assigned" and ex.rule_id == rule.id
    assert ex.previous_agent_id is None and ex.assigned_agent_id == agents[1].id
    assert ex.matched_conditions[0]["passed"] is True


def test_non_matching_ticket_is_untouched(db, tickets, make_rule, agents):
    make_rule("High to Bob", "agent", {"agent_id": agents[1].id}, conditions=HIGH)
    t = new_ticket(tickets, priority="Low")
    assert t.assigned_agent_id is None and executions(db, t) == []


def test_team_assignment(db, tickets, make_rule, team):
    make_rule("All to Tier 1", "team", {"team_id": team.id})
    t = new_ticket(tickets)
    assert t.assigned_team_id == team.id and t.assigned_agent_id is None


def test_round_robin_cycles_through_team(db, tickets, make_rule, team, agents):
    rule = make_rule("RR", "round_robin", {"team_id": team.id})
    got = [new_ticket(tickets).assigned_agent_id for _ in range(5)]
    ids = sorted(a.id for a in agents)
    assert got == [ids[0], ids[1], ids[2], ids[0], ids[1]]
    rotation = db.scalar(select(AssignmentRotation).where(AssignmentRotation.rule_id == rule.id))
    assert rotation.last_assigned_agent_id == ids[1]


def test_round_robin_skips_unavailable_and_inactive(db, tickets, make_rule, team, agents):
    make_rule("RR", "round_robin", {"team_id": team.id})
    AgentSelectionService(db, agents[0].company_id).set_availability(agents[1].id, False)
    agents[2].is_active = False
    db.commit()
    got = {new_ticket(tickets).assigned_agent_id for _ in range(3)}
    assert got == {agents[0].id}


def test_unavailable_agents_used_when_skip_disabled(db, tickets, make_rule, agents):
    AgentSelectionService(db, agents[0].company_id).set_availability(agents[0].id, False)
    db.commit()
    make_rule("Direct", "agent", {"agent_id": agents[0].id}, skip_unavailable_agents=False)
    assert new_ticket(tickets).assigned_agent_id == agents[0].id


def test_inactive_direct_agent_fails_and_is_audited(db, tickets, make_rule, agents):
    make_rule("Direct", "agent", {"agent_id": agents[0].id})
    agents[0].is_active = False
    db.commit()
    t = new_ticket(tickets)
    assert t.assigned_agent_id is None
    [ex] = executions(db, t)
    assert ex.execution_status == "failed" and "inactive" in ex.execution_reason


def test_least_assigned_picks_lowest_load_with_id_tiebreak(db, tickets, make_rule, team, agents):
    # Give Alice 2 open tickets and Bob 1 (manual assignments, rules not yet created)
    for agent_id in (agents[0].id, agents[0].id, agents[1].id):
        new_ticket(tickets, assigned_agent_id=agent_id)
    closed = new_ticket(tickets, assigned_agent_id=agents[2].id)
    tickets.update(closed.ticket_number, TicketUpdate(status="Resolved"))  # closed: not counted

    make_rule("Least", "least_assigned", {"team_id": team.id})
    assert new_ticket(tickets).assigned_agent_id == agents[2].id  # 0 open
    assert new_ticket(tickets).assigned_agent_id == agents[1].id  # tie 1/1 -> lower id (Bob < Cara)
    assert new_ticket(tickets).assigned_agent_id == agents[2].id


def test_pool_from_explicit_agent_list(db, tickets, make_rule, agents):
    make_rule("RR list", "round_robin", {"agent_ids": [agents[2].id, agents[0].id]})
    got = [new_ticket(tickets).assigned_agent_id for _ in range(3)]
    assert got == [agents[0].id, agents[2].id, agents[0].id]


def test_empty_pool_fails_with_reason(db, tickets, make_rule, team, agents):
    make_rule("RR", "round_robin", {"team_id": team.id})
    for a in agents:
        a.is_active = False
    db.commit()
    t = new_ticket(tickets)
    [ex] = executions(db, t)
    assert ex.execution_status == "failed" and "No eligible agents" in ex.execution_reason


# --------------------------------------------------- priority / multiple rules
def test_lower_priority_number_wins_and_stops(db, tickets, make_rule, agents):
    make_rule("Second", "agent", {"agent_id": agents[1].id}, priority=20)
    first = make_rule("First", "agent", {"agent_id": agents[0].id}, priority=5)
    t = new_ticket(tickets)
    assert t.assigned_agent_id == agents[0].id
    assert [e.rule_id for e in executions(db, t)] == [first.id]


def test_inactive_rules_are_ignored(db, tickets, make_rule, agents):
    make_rule("Off", "agent", {"agent_id": agents[0].id}, is_active=False)
    assert new_ticket(tickets).assigned_agent_id is None


def test_continue_to_next_rule_combines_team_and_agent(db, tickets, make_rule, team, agents):
    make_rule("Team first", "team", {"team_id": team.id}, priority=1, continue_to_next_rule=True)
    make_rule("Then agent", "agent", {"agent_id": agents[2].id}, priority=2)
    t = new_ticket(tickets)
    assert (t.assigned_team_id, t.assigned_agent_id) == (team.id, agents[2].id)
    assert [e.execution_status for e in executions(db, t)] == ["assigned", "assigned"]


def test_continue_to_next_rule_prevents_conflicting_assignment(db, tickets, make_rule, agents):
    make_rule("A", "agent", {"agent_id": agents[0].id}, priority=1, continue_to_next_rule=True)
    make_rule("B", "agent", {"agent_id": agents[1].id}, priority=2)
    t = new_ticket(tickets)
    assert t.assigned_agent_id == agents[0].id
    second = executions(db, t)[1]
    assert second.execution_status == "skipped" and "conflicting" in second.execution_reason


# ------------------------------------------------------------------ triggers
def test_created_only_rule_does_not_fire_on_update(db, tickets, make_rule, agents):
    make_rule("Create only", "agent", {"agent_id": agents[0].id}, conditions=HIGH)
    t = new_ticket(tickets, priority="Low")
    t = tickets.update(t.ticket_number, TicketUpdate(priority="High"))
    assert t.assigned_agent_id is None


def test_updated_trigger_fires_on_field_change(db, tickets, make_rule, agents):
    make_rule("On update", "agent", {"agent_id": agents[0].id}, conditions=HIGH, triggers=["ticket_updated"])
    t = new_ticket(tickets, priority="High")
    assert t.assigned_agent_id is None  # creation does not match the trigger
    t = tickets.update(t.ticket_number, TicketUpdate(subject="Still broken"))
    assert t.assigned_agent_id == agents[0].id


def test_assignment_only_update_never_triggers_rules(db, tickets, make_rule, agents):
    """Loop prevention: assignment writes (manual or engine) are not 'updates'."""
    make_rule("On update", "agent", {"agent_id": agents[0].id}, triggers=["ticket_updated"],
              overwrite_existing_assignment=True)
    t = new_ticket(tickets)
    t = tickets.update(t.ticket_number, TicketUpdate(assigned_agent_id=agents[1].id))
    assert t.assigned_agent_id == agents[1].id and executions(db, t) == []


def test_meta_only_update_does_not_trigger(db, tickets, make_rule, agents):
    make_rule("On update", "agent", {"agent_id": agents[0].id}, triggers=["ticket_updated"])
    t = new_ticket(tickets)
    t = tickets.update(t.ticket_number, TicketUpdate(meta={"conversation": [{"id": "m1"}]}))
    assert t.assigned_agent_id is None


def test_engine_rejects_reentrant_runs(db, tickets, agents):
    t = new_ticket(tickets)
    token = engine_module._ENGINE_ACTIVE.set(True)
    try:
        assert AssignmentEngine(db, t.company_id).run_for_ticket(t, "ticket_created") is None
    finally:
        engine_module._ENGINE_ACTIVE.reset(token)


# -------------------------------------------------- manual / existing assignments
def test_manual_assignment_is_protected_by_default(db, tickets, make_rule, agents):
    make_rule("Rule", "agent", {"agent_id": agents[0].id})
    t = new_ticket(tickets, assigned_agent_id=agents[2].id)
    assert t.assigned_agent_id == agents[2].id and t.assignment_source == "manual"
    [ex] = executions(db, t)
    assert ex.execution_status == "skipped" and "manual" in ex.execution_reason


def test_manual_assignment_overwritten_when_configured(db, tickets, make_rule, agents):
    make_rule("Rule", "agent", {"agent_id": agents[0].id}, overwrite_existing_assignment=True)
    t = new_ticket(tickets, assigned_agent_id=agents[2].id)
    assert t.assigned_agent_id == agents[0].id and t.assignment_source == "rule"


def test_rule_assigned_ticket_not_reassigned_unless_enabled(db, tickets, make_rule, team, agents):
    rr = make_rule("RR", "round_robin", {"team_id": team.id},
                   triggers=["ticket_created", "ticket_updated"])
    t = new_ticket(tickets)
    first = t.assigned_agent_id
    t = tickets.update(t.ticket_number, TicketUpdate(subject="More detail"))
    assert t.assigned_agent_id == first
    assert executions(db, t)[-1].execution_status == "skipped"

    payload = AssignmentRuleCreate(
        name="RR", triggers=["ticket_created", "ticket_updated"], conditions=ANY_OPEN,
        assignment_type="round_robin", assignment_config={"team_id": team.id}, reassign_existing=True,
    )
    AssignmentRuleService(db, tickets.actor).update(rr.id, payload)
    t = tickets.update(t.ticket_number, TicketUpdate(subject="Even more detail"))
    assert t.assigned_agent_id != first
    assert executions(db, t)[-1].execution_status == "assigned"


def test_no_duplicate_assignment_operation(db, tickets, make_rule, agents):
    make_rule("Direct", "agent", {"agent_id": agents[0].id},
              triggers=["ticket_created", "ticket_updated"], reassign_existing=True)
    t = new_ticket(tickets)
    t = tickets.update(t.ticket_number, TicketUpdate(subject="Changed"))
    statuses = [e.execution_status for e in executions(db, t)]
    assert statuses == ["assigned", "no_change"]


def test_engine_failure_does_not_block_ticket_creation(db, tickets, make_rule, agents, monkeypatch):
    make_rule("Direct", "agent", {"agent_id": agents[0].id})

    def boom(self, *a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr(AssignmentEngine, "evaluate_rules", boom)
    t = new_ticket(tickets)
    assert t.id is not None and t.assigned_agent_id is None
    assert db.get(Ticket, t.id) is not None


def test_rules_are_tenant_isolated(db, make_user, agents, make_rule):
    from app.models.company import Company

    other = Company(name="Other Co")
    db.add(other)
    db.commit()
    other_admin = make_user("Olga Other", role="admin", company_id=other.id)
    make_rule("Acme rule", "agent", {"agent_id": agents[0].id})
    t = TicketService(db, other_admin).create(TicketCreate(subject="Hi"))
    assert t.assigned_agent_id is None
    assert AssignmentRuleService(db, other_admin).list() == []


def test_deleting_team_used_by_rule_is_blocked(db, admin, make_rule, team):
    from app.core.exceptions import ConflictException
    from app.services.team_service import TeamService

    make_rule("Team", "team", {"team_id": team.id})
    with pytest.raises(ConflictException):
        TeamService(db, admin).delete(team.id)
    assert db.get(Team, team.id) is not None
