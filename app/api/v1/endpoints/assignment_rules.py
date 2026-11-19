"""Assignment Rules REST API (administrators only).

Static routes (``/fields``, ``/agents``, ``/teams``, ``/executions``, ``/preview``)
are declared before ``/{rule_id}`` routes; rule ids are typed as ``int`` so the
paths can never collide.
"""

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import distinct, select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundException
from app.db.session import get_db
from app.dependencies.auth import require_admin
from app.models.assignment_rule import AssignmentRule, AssignmentRuleExecution
from app.models.team import Team
from app.models.ticket import Ticket
from app.models.user import User
from app.schemas.assignment_rule import (
    AgentAvailabilityUpdate,
    AgentOption,
    AssignmentRuleBase,
    AssignmentRuleCreate,
    AssignmentRuleListResponse,
    AssignmentRuleResponse,
    AssignmentRuleStats,
    AssignmentRuleStatusUpdate,
    AssignmentRuleUpdate,
    AssignmentTarget,
    ConditionTrace,
    ExecutionListResponse,
    ExecutionResponse,
    ExecutionStatusLiteral,
    FieldMetadata,
    FieldOption,
    FieldsMetadataResponse,
    OperatorMetadata,
    PreviewRequest,
    PreviewResponse,
    RuleEvaluation,
    RuleTestRequest,
    RuleTestResponse,
    TeamOption,
    TicketTarget,
)
from app.services.assignment import conditions as C
from app.services.assignment import fields as F
from app.services.assignment.agent_selection import AgentSelectionService, company_scope
from app.services.assignment.audit import AssignmentAuditService
from app.services.assignment.context import build_context, context_from_ticket
from app.services.assignment.engine import (
    AssignmentEngine,
    AssignmentState,
    EngineResult,
    RuleDecision,
    state_of,
)
from app.services.assignment.rule_service import AssignmentRuleService, count_conditions
from app.services.ticket_service import find_ticket

router = APIRouter()

SortLiteral = Literal["priority_asc", "priority_desc", "updated_desc", "name_asc"]


# ---------------------------------------------------------------- presenters
def _rule_response(
    service: AssignmentRuleService, rule: AssignmentRule, labels: Dict[str, Dict[Any, str]],
    creators: Dict[int, str],
) -> AssignmentRuleResponse:
    summary, target = service.describe(rule, labels)
    return AssignmentRuleResponse(
        id=rule.id,
        name=rule.name,
        description=rule.description,
        is_active=rule.is_active,
        priority=rule.priority,
        triggers=list(rule.triggers or []),
        conditions=rule.condition_json,
        conditions_summary=summary,
        condition_count=count_conditions(rule.condition_json),
        assignment_type=rule.assignment_type,
        assignment_config=dict(rule.assignment_config or {}),
        assigned_to_label=target,
        overwrite_existing_assignment=rule.overwrite_existing_assignment,
        reassign_existing=rule.reassign_existing,
        skip_unavailable_agents=rule.skip_unavailable_agents,
        continue_to_next_rule=rule.continue_to_next_rule,
        created_by=rule.created_by,
        created_by_name=creators.get(rule.created_by) if rule.created_by else None,
        created_at=rule.created_at,
        updated_at=rule.updated_at,
    )


def _user_names(db: Session, ids: List[Optional[int]]) -> Dict[int, str]:
    clean = {i for i in ids if i}
    if not clean:
        return {}
    return {u.id: (u.full_name or u.email) for u in db.scalars(select(User).where(User.id.in_(clean)))}


def _team_names(db: Session, ids: List[Optional[int]]) -> Dict[int, str]:
    clean = {i for i in ids if i}
    if not clean:
        return {}
    return {t.id: t.name for t in db.scalars(select(Team).where(Team.id.in_(clean)))}


def _single_rule_response(db: Session, service: AssignmentRuleService, rule: AssignmentRule) -> AssignmentRuleResponse:
    return _rule_response(service, rule, service.option_labels(), _user_names(db, [rule.created_by]))


def _transient_rule(payload: AssignmentRuleBase, tree: Dict[str, Any], rule_id: Optional[int]) -> AssignmentRule:
    """An unsaved rule object for dry runs. It is never added to the session."""
    return AssignmentRule(
        id=rule_id,
        name=payload.name,
        priority=payload.priority,
        is_active=payload.is_active,
        triggers=list(payload.triggers),
        condition_json=tree,
        assignment_type=payload.assignment_type,
        assignment_config=payload.assignment_config.model_dump(exclude_none=True),
        overwrite_existing_assignment=payload.overwrite_existing_assignment,
        reassign_existing=payload.reassign_existing,
        skip_unavailable_agents=payload.skip_unavailable_agents,
        continue_to_next_rule=payload.continue_to_next_rule,
    )


def _resolve_target(
    db: Session, company_id: Optional[int], target: TicketTarget
) -> Tuple[Dict[str, Any], AssignmentState, Optional[int], str]:
    """Returns (context, assignment state, ticket id, display ref)."""
    if target.ticket_id:
        ticket = find_ticket(db, company_id, target.ticket_id)
        if ticket is None:
            raise NotFoundException(f"Ticket '{target.ticket_id}' not found.")
        return context_from_ticket(ticket), state_of(ticket), ticket.id, ticket.ticket_number or str(ticket.id)

    s = target.sample_ticket
    assert s is not None
    ctx = build_context(
        ticket_number=s.ticket_id, subject=s.subject, description=s.description, status=s.status,
        priority=s.priority, category=s.category, subcategory=s.subcategory, ticket_type=s.ticket_type,
        source=s.source, created_at=s.created_at or datetime.now(timezone.utc), updated_at=s.updated_at,
        customer_name=s.customer_name, customer_email=s.customer_email,
        customer_company=s.customer_company, customer_type=s.customer_type,
        assigned_agent_id=s.assigned_agent_id, assigned_team_id=s.assigned_team_id, tags=s.tags,
        sla_status=s.sla_status, due_date=s.due_date, message_count=s.message_count,
    )
    state = AssignmentState(s.assigned_agent_id, s.assigned_team_id, s.assignment_source)
    return ctx, state, None, s.ticket_id or "Sample ticket"


def _target(engine: AssignmentEngine, agent_id: Optional[int], team_id: Optional[int], kind: Optional[str] = None) -> AssignmentTarget:
    return AssignmentTarget(
        assignment_type=kind,
        agent_id=agent_id, agent_name=engine.agent_label(agent_id),
        team_id=team_id, team_name=engine.team_label(team_id),
    )


def _evaluation(engine: AssignmentEngine, d: RuleDecision) -> RuleEvaluation:
    traces = [ConditionTrace(**c.to_dict()) for c in d.evaluation.conditions]
    expected = None
    if d.matched and d.status in ("assigned", "no_change"):
        expected = _target(engine, d.agent_id, d.team_id, d.rule.assignment_type)
    return RuleEvaluation(
        rule_id=d.rule.id,
        rule_name=d.rule.name,
        priority=d.rule.priority,
        matched=d.matched,
        status=d.status,
        reason=d.reason,
        passed_conditions=[t for t in traces if t.passed],
        failed_conditions=[t for t in traces if not t.passed],
        expected_assignment=expected,
    )


def _changed(result: EngineResult) -> bool:
    return (result.initial.agent_id, result.initial.team_id) != (result.final.agent_id, result.final.team_id)


# ------------------------------------------------------------------- metadata
@router.get("/fields", response_model=FieldsMetadataResponse, summary="Condition builder fields & operators")
def get_fields(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> FieldsMetadataResponse:
    selection = AgentSelectionService(db, admin.company_id)
    dynamic: Dict[str, List[FieldOption]] = {
        F.DYNAMIC_AGENTS: [FieldOption(value=u.id, label=u.full_name or u.email) for u in selection.list_agents()],
        F.DYNAMIC_TEAMS: [FieldOption(value=t.id, label=t.name) for t in selection.list_teams()],
    }
    used = db.scalars(
        select(distinct(Ticket.category)).where(
            company_scope(Ticket.company_id, admin.company_id), Ticket.category.is_not(None)
        )
    ).all()
    categories = list(dict.fromkeys([*F.DEFAULT_CATEGORIES, *sorted(c for c in used if c)]))
    dynamic[F.DYNAMIC_CATEGORIES] = [FieldOption(value=c, label=c) for c in categories]

    fields = []
    for f in F.FIELDS:
        options = dynamic.get(f.dynamic_options or "") or [FieldOption(value=o, label=o) for o in f.options]
        fields.append(FieldMetadata(key=f.key, label=f.label, group=f.group, data_type=f.data_type,
                                    id_valued=f.id_valued, options=options))
    return FieldsMetadataResponse(
        fields=fields,
        operators={
            t: [OperatorMetadata(key=o.key, label=o.label, value_shape=o.value_shape) for o in ops]
            for t, ops in F.OPERATORS_BY_TYPE.items()
        },
        period_units=list(F.PERIOD_UNITS),
        limits={"max_depth": C.MAX_DEPTH, "max_conditions": C.MAX_CONDITIONS,
                "max_children_per_group": C.MAX_CHILDREN_PER_GROUP},
    )


@router.get("/agents", response_model=List[AgentOption], summary="Active agents eligible for assignment")
def list_agents(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> List[AgentOption]:
    selection = AgentSelectionService(db, admin.company_id)
    agents = selection.list_agents(active_only=True)
    ids = [a.id for a in agents]
    avail = selection.availability(ids)
    counts = selection.open_ticket_counts(ids)
    memberships: Dict[int, List[int]] = {}
    for team in selection.list_teams(active_only=False):
        for uid in team.member_ids:
            memberships.setdefault(uid, []).append(team.id)
    return [
        AgentOption(id=a.id, name=a.full_name or a.email, email=a.email, role=a.role,
                    is_available=avail.get(a.id, True), open_tickets=counts.get(a.id, 0),
                    team_ids=memberships.get(a.id, []))
        for a in agents
    ]


@router.patch("/agents/{agent_id}/availability", response_model=AgentOption, summary="Set agent availability")
def set_agent_availability(
    agent_id: int, payload: AgentAvailabilityUpdate,
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
) -> AgentOption:
    selection = AgentSelectionService(db, admin.company_id)
    agent = selection.get_agent(agent_id)
    if agent is None:
        raise NotFoundException("Agent not found.")
    selection.set_availability(agent.id, payload.is_available)
    db.commit()
    teams = [t.id for t in selection.list_teams(active_only=False) if agent.id in t.member_ids]
    return AgentOption(id=agent.id, name=agent.full_name or agent.email, email=agent.email, role=agent.role,
                       is_available=payload.is_available,
                       open_tickets=selection.open_ticket_counts([agent.id]).get(agent.id, 0), team_ids=teams)


@router.get("/teams", response_model=List[TeamOption], summary="Active teams eligible for assignment")
def list_teams(db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> List[TeamOption]:
    selection = AgentSelectionService(db, admin.company_id)
    return [TeamOption(id=t.id, name=t.name, member_count=len(t.members), member_ids=t.member_ids)
            for t in selection.list_teams(active_only=True)]


# ------------------------------------------------------------------- history
def _executions_response(db: Session, rows: List[Tuple[AssignmentRuleExecution, Optional[str]]], total: int) -> ExecutionListResponse:
    users = _user_names(db, [e.previous_agent_id for e, _ in rows] + [e.assigned_agent_id for e, _ in rows])
    teams = _team_names(db, [e.previous_team_id for e, _ in rows] + [e.assigned_team_id for e, _ in rows])
    items = [
        ExecutionResponse(
            id=e.id, rule_id=e.rule_id, rule_name=e.rule_name, ticket_id=e.ticket_id, ticket_number=num,
            trigger=e.trigger,
            previous_agent_id=e.previous_agent_id, previous_agent_name=users.get(e.previous_agent_id or 0),
            previous_team_id=e.previous_team_id, previous_team_name=teams.get(e.previous_team_id or 0),
            assigned_agent_id=e.assigned_agent_id, assigned_agent_name=users.get(e.assigned_agent_id or 0),
            assigned_team_id=e.assigned_team_id, assigned_team_name=teams.get(e.assigned_team_id or 0),
            execution_status=e.execution_status, execution_reason=e.execution_reason,
            matched_conditions=list(e.matched_conditions or []), executed_at=e.executed_at,
        )
        for e, num in rows
    ]
    return ExecutionListResponse(items=items, total=total)


@router.get("/executions", response_model=ExecutionListResponse, summary="Execution history (all rules)")
def list_all_executions(
    rule_id: Optional[int] = Query(None, gt=0),
    ticket_id: Optional[str] = Query(None, max_length=32, description="Ticket number (partial match)"),
    agent_id: Optional[int] = Query(None, gt=0),
    execution_status: Optional[ExecutionStatusLiteral] = Query(None, alias="status"),
    date_from: Optional[datetime] = Query(None),
    date_to: Optional[datetime] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
) -> ExecutionListResponse:
    rows, total = AssignmentAuditService(db, admin.company_id).list_executions(
        rule_id=rule_id, ticket_ref=ticket_id, agent_id=agent_id, status=execution_status,
        date_from=date_from, date_to=date_to, limit=limit, offset=offset,
    )
    return _executions_response(db, rows, total)


# ------------------------------------------------------------------- preview
@router.post("/preview", response_model=PreviewResponse, summary="Preview which rule would assign a ticket")
def preview_assignment(
    payload: PreviewRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin),
) -> PreviewResponse:
    """Dry run against the active rule set (or a single unsaved draft). Nothing is written."""
    try:
        ctx, state, ticket_id, ref = _resolve_target(db, admin.company_id, payload)
        engine = AssignmentEngine(db, admin.company_id)
        rules = None
        if payload.rule is not None:
            tree = AssignmentRuleService(db, admin).validate_payload(payload.rule)
            rules = [_transient_rule(payload.rule, tree, None)]
        result = engine.dry_run(ctx, state, rules=rules, trigger=payload.trigger, ticket_id=ticket_id)

        winner = next((d for d in result.decisions if d.status == "assigned"), None)
        changed = _changed(result)
        if not result.decisions:
            summary = "No active rules apply to this trigger."
        elif winner:
            summary = f"Rule '{winner.rule.name}' would run: {winner.reason}"
        elif result.matched_decisions:
            d = result.matched_decisions[0]
            summary = f"Rule '{d.rule.name}' matches but would not change the assignment: {d.reason}"
        else:
            summary = "No rule matches this ticket; the assignment would stay unchanged."
        return PreviewResponse(
            ticket_ref=ref, trigger=payload.trigger, rules_considered=len(result.decisions),
            winning_rule_id=winner.rule.id if winner else None,
            winning_rule_name=winner.rule.name if winner else None,
            current_assignment=_target(engine, result.initial.agent_id, result.initial.team_id),
            final_assignment=_target(engine, result.final.agent_id, result.final.team_id),
            would_change_assignment=changed, summary=summary,
            evaluations=[_evaluation(engine, d) for d in result.decisions],
        )
    finally:
        db.rollback()  # guarantee a dry run never persists anything


# ------------------------------------------------------------------- CRUD
@router.get("", response_model=AssignmentRuleListResponse, summary="List assignment rules")
def list_rules(
    search: Optional[str] = Query(None, max_length=120),
    rule_status: Optional[Literal["active", "inactive"]] = Query(None, alias="status"),
    assignment_type: Optional[Literal["agent", "team", "round_robin", "least_assigned"]] = Query(None),
    sort: SortLiteral = Query("priority_asc"),
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
) -> AssignmentRuleListResponse:
    service = AssignmentRuleService(db, admin)
    rules = service.list(search=search, status_filter=rule_status, assignment_type=assignment_type, sort=sort)
    labels = service.option_labels()
    creators = _user_names(db, [r.created_by for r in rules])
    return AssignmentRuleListResponse(
        items=[_rule_response(service, r, labels, creators) for r in rules],
        total=len(rules),
        stats=AssignmentRuleStats(**service.stats()),
    )


@router.post("", response_model=AssignmentRuleResponse, status_code=status.HTTP_201_CREATED, summary="Create rule")
def create_rule(payload: AssignmentRuleCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> AssignmentRuleResponse:
    service = AssignmentRuleService(db, admin)
    return _single_rule_response(db, service, service.create(payload))


@router.get("/{rule_id}", response_model=AssignmentRuleResponse, summary="Get rule")
def get_rule(rule_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> AssignmentRuleResponse:
    service = AssignmentRuleService(db, admin)
    return _single_rule_response(db, service, service.get(rule_id))


@router.put("/{rule_id}", response_model=AssignmentRuleResponse, summary="Update rule")
def update_rule(rule_id: int, payload: AssignmentRuleUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> AssignmentRuleResponse:
    service = AssignmentRuleService(db, admin)
    return _single_rule_response(db, service, service.update(rule_id, payload))


@router.delete("/{rule_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete rule")
def delete_rule(rule_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> Response:
    AssignmentRuleService(db, admin).delete(rule_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{rule_id}/status", response_model=AssignmentRuleResponse, summary="Activate / deactivate rule")
def set_rule_status(rule_id: int, payload: AssignmentRuleStatusUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> AssignmentRuleResponse:
    service = AssignmentRuleService(db, admin)
    return _single_rule_response(db, service, service.set_status(rule_id, payload.is_active))


@router.post("/{rule_id}/duplicate", response_model=AssignmentRuleResponse, status_code=status.HTTP_201_CREATED, summary="Duplicate rule (created inactive)")
def duplicate_rule(rule_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> AssignmentRuleResponse:
    service = AssignmentRuleService(db, admin)
    return _single_rule_response(db, service, service.duplicate(rule_id))


@router.post("/{rule_id}/test", response_model=RuleTestResponse, summary="Test a rule against a ticket (no changes made)")
def test_rule(rule_id: int, payload: RuleTestRequest, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> RuleTestResponse:
    try:
        service = AssignmentRuleService(db, admin)
        rule = service.get(rule_id)
        if payload.rule is not None:
            rule = _transient_rule(payload.rule, service.validate_payload(payload.rule), rule.id)
        ctx, state, ticket_id, ref = _resolve_target(db, admin.company_id, payload)
        engine = AssignmentEngine(db, admin.company_id)
        result = engine.dry_run(ctx, state, rules=[rule], ticket_id=ticket_id)
        evaluation = _evaluation(engine, result.decisions[0])
        return RuleTestResponse(**evaluation.model_dump(), ticket_ref=ref, would_change_assignment=_changed(result))
    finally:
        db.rollback()


@router.get("/{rule_id}/executions", response_model=ExecutionListResponse, summary="Execution history for a rule")
def list_rule_executions(
    rule_id: int,
    limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
    db: Session = Depends(get_db), admin: User = Depends(require_admin),
) -> ExecutionListResponse:
    AssignmentRuleService(db, admin).get(rule_id)  # 404 + tenant check
    rows, total = AssignmentAuditService(db, admin.company_id).list_executions(rule_id=rule_id, limit=limit, offset=offset)
    return _executions_response(db, rows, total)
