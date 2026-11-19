"""Assignment execution engine.

Flow for a real run (``run_for_ticket``):

1. A ticket is created or updated (the ticket service calls the engine inside a
   SAVEPOINT of the request transaction).
2. Active rules for the tenant whose triggers include the event are loaded and
   ordered by ``priority`` ascending (lower number = runs first), then by id.
3. Each rule's condition tree is evaluated against the ticket.
4. The first matching rule's action is planned and applied; evaluation stops
   unless that rule has ``continue_to_next_rule`` enabled.
5. Every rule that matched is written to the execution history.

Loop prevention: the engine writes assignment columns directly on the ticket and
never calls back into the ticket service, the ticket service only fires the
``ticket_updated`` trigger when a tracked (non-assignment) field changed, and a
context-variable guard rejects re-entrant runs.

"Continue to Next Rule": when a matching rule has it enabled, lower-priority
rules are still evaluated, but they may only fill an assignment slot (agent or
team) that no earlier rule in the same run has set. A later rule that would
overwrite a slot is recorded as ``skipped`` (conflict), so one run can never
produce contradictory assignments.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assignment_rule import (
    ASSIGN_AGENT,
    ASSIGN_LEAST_ASSIGNED,
    ASSIGN_ROUND_ROBIN,
    ASSIGN_TEAM,
    EXEC_ASSIGNED,
    EXEC_FAILED,
    EXEC_NO_CHANGE,
    EXEC_SKIPPED,
    AssignmentRotation,
    AssignmentRule,
)
from app.models.ticket import ASSIGNMENT_SOURCE_RULE, Ticket
from app.services.assignment.agent_selection import (
    AgentSelectionService,
    LeastAssignedService,
    RoundRobinService,
    company_scope,
    get_rotation,
)
from app.services.assignment.audit import AssignmentAuditService
from app.services.assignment.conditions import ConditionEvaluator, EvaluationResult
from app.services.assignment.context import context_from_ticket

_ENGINE_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "assignment_engine_active", default=False
)

SLOT_AGENT = "agent"
SLOT_TEAM = "team"

ASSIGNMENT_TYPE_LABELS = {
    ASSIGN_AGENT: "direct agent assignment",
    ASSIGN_TEAM: "team assignment",
    ASSIGN_ROUND_ROBIN: "round robin",
    ASSIGN_LEAST_ASSIGNED: "least assigned",
}


@dataclass
class AssignmentState:
    agent_id: Optional[int] = None
    team_id: Optional[int] = None
    source: Optional[str] = None
    by_rule_id: Optional[int] = None

    @property
    def has_assignment(self) -> bool:
        return self.agent_id is not None or self.team_id is not None


@dataclass
class RuleDecision:
    rule: AssignmentRule
    evaluation: EvaluationResult
    status: Optional[str] = None  # None when conditions did not match
    reason: str = ""
    agent_id: Optional[int] = None
    team_id: Optional[int] = None
    slots: Set[str] = field(default_factory=set)
    rotation_update: Optional[Tuple[int, int]] = None  # (agent_id, index)

    @property
    def matched(self) -> bool:
        return self.evaluation.matched


@dataclass
class EngineResult:
    decisions: List[RuleDecision]
    initial: AssignmentState
    final: AssignmentState
    rules_evaluated: int

    @property
    def matched_decisions(self) -> List[RuleDecision]:
        return [d for d in self.decisions if d.matched]


class AssignmentEngine:
    def __init__(self, db: Session, company_id: Optional[int], now: Optional[datetime] = None):
        self.db = db
        self.company_id = company_id
        self.now = now or datetime.now(timezone.utc)
        self.selection = AgentSelectionService(db, company_id)
        self.audit = AssignmentAuditService(db, company_id)
        self.evaluator = ConditionEvaluator(self.now)
        self._agent_labels: Dict[int, str] = {}
        self._team_labels: Dict[int, str] = {}

    # ------------------------------------------------------------------ rules
    def active_rules(self, trigger: Optional[str]) -> List[AssignmentRule]:
        rules = self.db.scalars(
            select(AssignmentRule)
            .where(
                company_scope(AssignmentRule.company_id, self.company_id),
                AssignmentRule.is_active.is_(True),
            )
            .order_by(AssignmentRule.priority.asc(), AssignmentRule.id.asc())
        ).all()
        if trigger is None:
            return list(rules)
        return [r for r in rules if trigger in (r.triggers or [])]

    # ----------------------------------------------------------------- labels
    def agent_label(self, agent_id: Optional[int]) -> Optional[str]:
        if agent_id is None:
            return None
        if agent_id not in self._agent_labels:
            agent = self.selection.get_agent(agent_id)
            self._agent_labels[agent_id] = (
                (agent.full_name or agent.email) if agent else f"Agent #{agent_id}"
            )
        return self._agent_labels[agent_id]

    def team_label(self, team_id: Optional[int]) -> Optional[str]:
        if team_id is None:
            return None
        if team_id not in self._team_labels:
            team = self.selection.get_team(team_id)
            self._team_labels[team_id] = team.name if team else f"Team #{team_id}"
        return self._team_labels[team_id]

    # --------------------------------------------------------------- planning
    def _plan(
        self,
        rule: AssignmentRule,
        evaluation: EvaluationResult,
        pre: AssignmentState,
        current: AssignmentState,
        claimed: Dict[str, str],
        ticket_id: Optional[int],
        dry_run: bool,
    ) -> RuleDecision:
        decision = RuleDecision(rule=rule, evaluation=evaluation)
        cfg = rule.assignment_config or {}

        # 1. Protect existing assignments (judged on the state before this run).
        if pre.has_assignment:
            is_manual = pre.source != ASSIGNMENT_SOURCE_RULE
            if is_manual and not rule.overwrite_existing_assignment:
                decision.status = EXEC_SKIPPED
                decision.reason = (
                    "Ticket has a manual assignment and this rule is configured not to "
                    "overwrite manual assignments."
                )
                return decision
            if not is_manual and not rule.reassign_existing:
                decision.status = EXEC_SKIPPED
                decision.reason = (
                    "Ticket is already assigned by an earlier automated run and "
                    "'Reassign existing tickets' is disabled for this rule."
                )
                return decision

        # 2. Which slots does this action write?
        slots: Set[str] = set()
        if rule.assignment_type == ASSIGN_TEAM:
            slots.add(SLOT_TEAM)
        else:
            slots.add(SLOT_AGENT)
            if rule.assignment_type in (ASSIGN_ROUND_ROBIN, ASSIGN_LEAST_ASSIGNED) and cfg.get("team_id"):
                slots.add(SLOT_TEAM)
        conflicts = sorted(s for s in slots if s in claimed)
        if conflicts:
            decision.status = EXEC_SKIPPED
            decision.reason = (
                f"Skipped to avoid a conflicting assignment: the {' and '.join(conflicts)} "
                f"was already set by rule '{claimed[conflicts[0]]}' earlier in this run."
            )
            return decision
        decision.slots = slots

        # 3. Resolve the target.
        target_agent: Optional[int] = None
        target_team: Optional[int] = None
        detail = ""

        if rule.assignment_type == ASSIGN_AGENT:
            agent_id = cfg.get("agent_id")
            if not agent_id:
                return self._fail(decision, "Rule has no agent configured.")
            agent, problem = self.selection.check_agent(int(agent_id), rule.skip_unavailable_agents)
            if problem:
                return self._fail(decision, problem)
            target_agent = agent.id  # type: ignore[union-attr]

        elif rule.assignment_type == ASSIGN_TEAM:
            team_id = cfg.get("team_id")
            team = self.selection.get_team(int(team_id)) if team_id else None
            if team is None:
                return self._fail(decision, "The configured team no longer exists.")
            if not team.is_active:
                return self._fail(decision, f"Team {team.name} is inactive.")
            target_team = team.id

        elif rule.assignment_type in (ASSIGN_ROUND_ROBIN, ASSIGN_LEAST_ASSIGNED):
            team_id = cfg.get("team_id")
            if team_id:
                team = self.selection.get_team(int(team_id))
                if team is None or not team.is_active:
                    return self._fail(decision, "The configured team is missing or inactive.")
                target_team = team.id
            pool = self.selection.eligible_pool(cfg, rule.skip_unavailable_agents)
            if not pool.eligible:
                excluded = ", ".join(
                    f"{self.agent_label(uid)} ({why})" for uid, why in pool.excluded.items()
                )
                return self._fail(
                    decision,
                    "No eligible agents in the assignment pool"
                    + (f" — excluded: {excluded}." if excluded else "."),
                )
            # Lock the per-rule rotation row: serializes concurrent selections.
            rotation = get_rotation(self.db, rule.id, lock=not dry_run) if rule.id else None

            if rule.assignment_type == ASSIGN_ROUND_ROBIN:
                last = rotation.last_assigned_agent_id if rotation else None
                target_agent, index = RoundRobinService.pick(pool.eligible, last)
                decision.rotation_update = (target_agent, index)
                detail = f"round robin, position {index + 1} of {len(pool.eligible)} eligible agents"
            else:
                counts = self.selection.open_ticket_counts(pool.eligible, exclude_ticket_id=ticket_id)
                target_agent, open_count = LeastAssignedService.pick(counts)
                detail = (
                    f"least assigned, {open_count} open ticket{'s' if open_count != 1 else ''} "
                    f"among {len(pool.eligible)} eligible agents"
                )
        else:
            return self._fail(decision, f"Unknown assignment type '{rule.assignment_type}'.")

        decision.agent_id = target_agent if SLOT_AGENT in slots else None
        decision.team_id = target_team if SLOT_TEAM in slots else None

        # 4. Avoid duplicate assignment operations.
        same_agent = SLOT_AGENT not in slots or current.agent_id == decision.agent_id
        same_team = SLOT_TEAM not in slots or current.team_id == decision.team_id
        if same_agent and same_team:
            decision.status = EXEC_NO_CHANGE
            decision.rotation_update = None
            decision.reason = f"Ticket is already assigned to {self._target_text(decision)}."
            return decision

        decision.status = EXEC_ASSIGNED
        how = ASSIGNMENT_TYPE_LABELS.get(rule.assignment_type, rule.assignment_type)
        decision.reason = f"Assigned to {self._target_text(decision)} via {how}" + (
            f" ({detail})." if detail else "."
        )
        return decision

    def _fail(self, decision: RuleDecision, reason: str) -> RuleDecision:
        decision.status = EXEC_FAILED
        decision.reason = reason
        decision.slots = set()
        return decision

    def _target_text(self, d: RuleDecision) -> str:
        parts = []
        if d.agent_id is not None:
            parts.append(f"agent {self.agent_label(d.agent_id)}")
        if d.team_id is not None:
            parts.append(f"team {self.team_label(d.team_id)}")
        return " in ".join(parts) if parts else "nobody"

    def evaluate_rules(
        self,
        rules: Sequence[AssignmentRule],
        context: Dict[str, Any],
        state: AssignmentState,
        *,
        ticket_id: Optional[int],
        dry_run: bool,
        stop_on_match: bool = True,
    ) -> EngineResult:
        pre = replace(state)
        current = replace(state)
        claimed: Dict[str, str] = {}
        decisions: List[RuleDecision] = []

        for rule in rules:
            evaluation = self.evaluator.evaluate(rule.condition_json, context)
            if not evaluation.matched:
                decisions.append(
                    RuleDecision(rule=rule, evaluation=evaluation, reason="Conditions were not met.")
                )
                continue

            decision = self._plan(rule, evaluation, pre, current, claimed, ticket_id, dry_run)
            decisions.append(decision)

            if decision.status == EXEC_ASSIGNED:
                if SLOT_AGENT in decision.slots:
                    current.agent_id = decision.agent_id
                if SLOT_TEAM in decision.slots:
                    current.team_id = decision.team_id
                current.source = ASSIGNMENT_SOURCE_RULE
                current.by_rule_id = rule.id
                # Keep the evaluation context in sync for continued rules.
                context = {**context, "assigned_agent": current.agent_id,
                           "assigned_team": current.team_id,
                           "is_unassigned": not current.has_assignment}
            if decision.status in (EXEC_ASSIGNED, EXEC_NO_CHANGE):
                for slot in decision.slots:
                    claimed.setdefault(slot, rule.name)

            if stop_on_match and not rule.continue_to_next_rule:
                break

        return EngineResult(decisions, pre, current, len(decisions))

    # --------------------------------------------------------------- real run
    def run_for_ticket(self, ticket: Ticket, trigger: str) -> Optional[EngineResult]:
        """Evaluates and applies rules for ``ticket``. Caller owns the transaction."""
        if _ENGINE_ACTIVE.get():
            return None  # re-entrant call (e.g. from an assignment side effect)
        token = _ENGINE_ACTIVE.set(True)
        try:
            self.db.flush()
            # Row-lock the ticket so concurrent updates evaluate one at a time.
            self.db.execute(select(Ticket.id).where(Ticket.id == ticket.id).with_for_update())

            rules = self.active_rules(trigger)
            if not rules:
                return EngineResult([], AssignmentState(), AssignmentState(), 0)

            result = self.evaluate_rules(
                rules, context_from_ticket(ticket, self.now), state_of(ticket),
                ticket_id=ticket.id, dry_run=False,
            )

            for d in result.matched_decisions:
                if d.status == EXEC_ASSIGNED and d.rotation_update and d.rule.id:
                    rotation = self.db.scalar(
                        select(AssignmentRotation).where(AssignmentRotation.rule_id == d.rule.id)
                    )
                    if rotation is not None:
                        RoundRobinService.advance(rotation, *d.rotation_update)
                self.audit.record_execution(
                    rule=d.rule,
                    ticket=ticket,
                    trigger=trigger,
                    status=d.status or EXEC_SKIPPED,
                    reason=d.reason,
                    previous_agent_id=result.initial.agent_id,
                    previous_team_id=result.initial.team_id,
                    assigned_agent_id=d.agent_id if d.status in (EXEC_ASSIGNED, EXEC_NO_CHANGE) else None,
                    assigned_team_id=d.team_id if d.status in (EXEC_ASSIGNED, EXEC_NO_CHANGE) else None,
                    matched_conditions=[c.to_dict() for c in d.evaluation.conditions],
                )

            final = result.final
            if (final.agent_id, final.team_id) != (ticket.assigned_agent_id, ticket.assigned_team_id):
                ticket.assigned_agent_id = final.agent_id
                ticket.assigned_team_id = final.team_id
                ticket.assignment_source = final.source
                ticket.assigned_by_rule_id = final.by_rule_id
            self.db.flush()
            return result
        finally:
            _ENGINE_ACTIVE.reset(token)

    # ---------------------------------------------------------------- dry run
    def dry_run(
        self,
        context: Dict[str, Any],
        state: AssignmentState,
        *,
        rules: Optional[Sequence[AssignmentRule]] = None,
        trigger: Optional[str] = None,
        ticket_id: Optional[int] = None,
    ) -> EngineResult:
        """Evaluates without writing anything (used by Test Rule and Preview)."""
        target_rules = list(rules) if rules is not None else self.active_rules(trigger)
        return self.evaluate_rules(
            target_rules, context, state, ticket_id=ticket_id, dry_run=True,
            stop_on_match=rules is None or len(target_rules) > 1,
        )


def state_of(ticket: Ticket) -> AssignmentState:
    return AssignmentState(
        ticket.assigned_agent_id, ticket.assigned_team_id,
        ticket.assignment_source, ticket.assigned_by_rule_id,
    )
