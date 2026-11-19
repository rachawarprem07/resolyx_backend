"""CRUD and validation for assignment rules (kept separate from HTTP handlers)."""

from typing import Any, Dict, List, Optional, Tuple

from fastapi import status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import AppException, ConflictException, NotFoundException
from app.models.assignment_rule import POOL_ASSIGNMENT_TYPES, AssignmentRule
from app.models.user import User
from app.schemas.assignment_rule import AssignmentRuleBase
from app.services.assignment.agent_selection import (
    AgentSelectionService,
    company_scope,
    ensure_rotation,
)
from app.services.assignment.audit import AssignmentAuditService
from app.services.assignment.conditions import (
    ConditionValidationError,
    summarize_tree,
    validate_condition_tree,
)

SORT_OPTIONS = {
    "priority_asc": (AssignmentRule.priority.asc(), AssignmentRule.id.asc()),
    "priority_desc": (AssignmentRule.priority.desc(), AssignmentRule.id.desc()),
    "updated_desc": (AssignmentRule.updated_at.desc(), AssignmentRule.id.desc()),
    "name_asc": (AssignmentRule.name.asc(), AssignmentRule.id.asc()),
}


class ValidationProblem(AppException):
    """422 with a structured body: {"message": str, "errors": [{"path", "message"}]}."""

    def __init__(self, message: str, errors: Optional[List[Dict[str, str]]] = None):
        super().__init__(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"message": message, "errors": errors or []},
        )


def count_conditions(tree: Dict[str, Any]) -> int:
    if tree.get("type") == "condition":
        return 1
    return sum(count_conditions(c) for c in tree.get("children", []))


class AssignmentRuleService:
    def __init__(self, db: Session, actor: User):
        self.db = db
        self.actor = actor
        self.company_id = actor.company_id
        self.selection = AgentSelectionService(db, self.company_id)
        self.audit = AssignmentAuditService(db, self.company_id)

    # ------------------------------------------------------------- queries
    def _base(self):
        return select(AssignmentRule).where(company_scope(AssignmentRule.company_id, self.company_id))

    def get(self, rule_id: int) -> AssignmentRule:
        rule = self.db.scalar(self._base().where(AssignmentRule.id == rule_id))
        if rule is None:
            raise NotFoundException("Assignment rule not found.")
        return rule

    def list(
        self,
        *,
        search: Optional[str] = None,
        status_filter: Optional[str] = None,
        assignment_type: Optional[str] = None,
        sort: str = "priority_asc",
    ) -> List[AssignmentRule]:
        stmt = self._base()
        if search and search.strip():
            q = f"%{search.strip()}%"
            stmt = stmt.where(AssignmentRule.name.ilike(q) | AssignmentRule.description.ilike(q))
        if status_filter == "active":
            stmt = stmt.where(AssignmentRule.is_active.is_(True))
        elif status_filter == "inactive":
            stmt = stmt.where(AssignmentRule.is_active.is_(False))
        if assignment_type:
            stmt = stmt.where(AssignmentRule.assignment_type == assignment_type)
        return list(self.db.scalars(stmt.order_by(*SORT_OPTIONS.get(sort, SORT_OPTIONS["priority_asc"]))))

    def stats(self) -> Dict[str, int]:
        total, active = self.db.execute(
            select(
                func.count(AssignmentRule.id),
                func.count(AssignmentRule.id).filter(AssignmentRule.is_active.is_(True)),
            ).where(company_scope(AssignmentRule.company_id, self.company_id))
        ).one()
        return {
            "total_rules": int(total or 0),
            "active_rules": int(active or 0),
            "inactive_rules": int((total or 0) - (active or 0)),
            "tickets_assigned_automatically": self.audit.auto_assigned_ticket_count(),
        }

    # ---------------------------------------------------------- validation
    def validate_payload(self, payload: AssignmentRuleBase) -> Dict[str, Any]:
        """Validates conditions and targets; returns the normalized condition tree."""
        try:
            tree = validate_condition_tree(payload.conditions)
        except ConditionValidationError as exc:
            raise ValidationProblem("Some conditions are invalid.", exc.errors) from None

        errors: List[Dict[str, str]] = []
        cfg = payload.assignment_config
        if cfg.agent_id:
            agent = self.selection.get_agent(cfg.agent_id)
            if agent is None:
                errors.append({"path": "assignment_config.agent_id", "message": "Agent not found in this workspace."})
            elif not agent.is_active:
                errors.append({"path": "assignment_config.agent_id", "message": "Agent is inactive and cannot receive assignments."})
        if cfg.team_id:
            team = self.selection.get_team(cfg.team_id)
            if team is None:
                errors.append({"path": "assignment_config.team_id", "message": "Team not found in this workspace."})
            elif not team.is_active:
                errors.append({"path": "assignment_config.team_id", "message": "Team is inactive."})
            elif payload.assignment_type in POOL_ASSIGNMENT_TYPES and not team.members and not cfg.agent_ids:
                errors.append({"path": "assignment_config.team_id", "message": "Team has no members to distribute tickets to."})
        if cfg.agent_ids:
            found = {
                u.id: u
                for u in self.db.scalars(
                    self.selection.agent_query().where(User.id.in_(cfg.agent_ids))
                )
            }
            missing = [i for i in cfg.agent_ids if i not in found]
            inactive = [found[i].full_name or found[i].email for i in cfg.agent_ids if i in found and not found[i].is_active]
            if missing:
                errors.append({"path": "assignment_config.agent_ids", "message": f"Unknown agent ids: {missing}."})
            if inactive:
                errors.append({"path": "assignment_config.agent_ids", "message": f"Inactive agents: {', '.join(inactive)}."})
        if errors:
            raise ValidationProblem("The assignment target is invalid.", errors)
        return tree

    # ------------------------------------------------------------ mutations
    def _apply(self, rule: AssignmentRule, payload: AssignmentRuleBase, tree: Dict[str, Any]) -> None:
        rule.name = payload.name
        rule.description = payload.description
        rule.is_active = payload.is_active
        rule.priority = payload.priority
        rule.triggers = list(payload.triggers)
        rule.condition_json = tree
        rule.assignment_type = payload.assignment_type
        rule.assignment_config = payload.assignment_config.model_dump(exclude_none=True, exclude_defaults=False)
        rule.overwrite_existing_assignment = payload.overwrite_existing_assignment
        rule.reassign_existing = payload.reassign_existing
        rule.skip_unavailable_agents = payload.skip_unavailable_agents
        rule.continue_to_next_rule = payload.continue_to_next_rule
        rule.updated_by = self.actor.id

    def _commit(self, rule: AssignmentRule) -> AssignmentRule:
        try:
            self.db.flush()
            if rule.assignment_type in POOL_ASSIGNMENT_TYPES:
                ensure_rotation(self.db, rule)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictException("An assignment rule with this name already exists.") from None
        self.db.refresh(rule)
        return rule

    def _name_taken(self, name: str, exclude_id: Optional[int] = None) -> bool:
        stmt = self._base().where(func.lower(AssignmentRule.name) == name.lower())
        if exclude_id is not None:
            stmt = stmt.where(AssignmentRule.id != exclude_id)
        return self.db.scalar(stmt) is not None

    def create(self, payload: AssignmentRuleBase) -> AssignmentRule:
        tree = self.validate_payload(payload)
        if self._name_taken(payload.name):
            raise ConflictException("An assignment rule with this name already exists.")
        rule = AssignmentRule(company_id=self.company_id, created_by=self.actor.id)
        self._apply(rule, payload, tree)
        self.db.add(rule)
        self.db.flush()
        self.audit.record_change(rule=rule, action="created", actor_id=self.actor.id, snapshot=snapshot(rule))
        return self._commit(rule)

    def update(self, rule_id: int, payload: AssignmentRuleBase) -> AssignmentRule:
        rule = self.get(rule_id)
        tree = self.validate_payload(payload)
        if self._name_taken(payload.name, exclude_id=rule.id):
            raise ConflictException("An assignment rule with this name already exists.")
        self._apply(rule, payload, tree)
        self.audit.record_change(rule=rule, action="updated", actor_id=self.actor.id, snapshot=snapshot(rule))
        return self._commit(rule)

    def set_status(self, rule_id: int, is_active: bool) -> AssignmentRule:
        rule = self.get(rule_id)
        if rule.is_active != is_active:
            rule.is_active = is_active
            rule.updated_by = self.actor.id
            self.audit.record_change(
                rule=rule, action="activated" if is_active else "deactivated",
                actor_id=self.actor.id, snapshot={"is_active": is_active},
            )
        return self._commit(rule)

    def duplicate(self, rule_id: int) -> AssignmentRule:
        source = self.get(rule_id)
        base = f"Copy of {source.name}"[:110]
        name, n = base, 2
        while self._name_taken(name):
            name, n = f"{base} ({n})", n + 1
        copy = AssignmentRule(
            company_id=self.company_id,
            name=name,
            description=source.description,
            is_active=False,  # duplicates start inactive so they never fire unexpectedly
            priority=source.priority,
            triggers=list(source.triggers or []),
            condition_json=source.condition_json,
            assignment_type=source.assignment_type,
            assignment_config=dict(source.assignment_config or {}),
            overwrite_existing_assignment=source.overwrite_existing_assignment,
            reassign_existing=source.reassign_existing,
            skip_unavailable_agents=source.skip_unavailable_agents,
            continue_to_next_rule=source.continue_to_next_rule,
            created_by=self.actor.id,
            updated_by=self.actor.id,
        )
        self.db.add(copy)
        self.db.flush()
        self.audit.record_change(
            rule=copy, action="duplicated", actor_id=self.actor.id,
            snapshot={"source_rule_id": source.id, **snapshot(copy)},
        )
        return self._commit(copy)

    def delete(self, rule_id: int) -> None:
        rule = self.get(rule_id)
        self.audit.record_change(rule=rule, action="deleted", actor_id=self.actor.id, snapshot=snapshot(rule))
        self.db.delete(rule)
        self.db.commit()

    # ------------------------------------------------------------ presenting
    def option_labels(self) -> Dict[str, Dict[Any, str]]:
        agents = {u.id: (u.full_name or u.email) for u in self.selection.list_agents(active_only=False)}
        teams = {t.id: t.name for t in self.selection.list_teams(active_only=False)}
        return {"assigned_agent": agents, "assigned_team": teams}

    def describe(self, rule: AssignmentRule, labels: Dict[str, Dict[Any, str]]) -> Tuple[str, str]:
        """Returns (conditions_summary, assigned_to_label)."""
        cfg = rule.assignment_config or {}
        agents, teams = labels.get("assigned_agent", {}), labels.get("assigned_team", {})
        if rule.assignment_type == "agent":
            target = agents.get(cfg.get("agent_id"), "Unknown agent")
        elif rule.assignment_type == "team":
            target = teams.get(cfg.get("team_id"), "Unknown team")
        else:
            parts = []
            if cfg.get("team_id"):
                parts.append(teams.get(cfg["team_id"], "Unknown team"))
            if cfg.get("agent_ids"):
                n = len(cfg["agent_ids"])
                parts.append(f"{n} agent{'s' if n != 1 else ''}")
            target = " + ".join(parts) or "No pool"
        return summarize_tree(rule.condition_json, labels), target


def snapshot(rule: AssignmentRule) -> Dict[str, Any]:
    return {
        "name": rule.name,
        "is_active": rule.is_active,
        "priority": rule.priority,
        "triggers": rule.triggers,
        "conditions": rule.condition_json,
        "assignment_type": rule.assignment_type,
        "assignment_config": rule.assignment_config,
        "overwrite_existing_assignment": rule.overwrite_existing_assignment,
        "reassign_existing": rule.reassign_existing,
        "skip_unavailable_agents": rule.skip_unavailable_agents,
        "continue_to_next_rule": rule.continue_to_next_rule,
    }
