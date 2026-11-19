"""Audit trail for rule executions and administrative rule changes."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.logging import logger
from app.models.assignment_rule import (
    EXEC_ASSIGNED,
    AssignmentRule,
    AssignmentRuleChangeLog,
    AssignmentRuleExecution,
)
from app.models.ticket import Ticket
from app.services.assignment.agent_selection import company_scope


class AssignmentAuditService:
    def __init__(self, db: Session, company_id: Optional[int]):
        self.db = db
        self.company_id = company_id

    # -- writes -------------------------------------------------------------
    def record_execution(
        self,
        *,
        rule: AssignmentRule,
        ticket: Ticket,
        trigger: str,
        status: str,
        reason: str,
        previous_agent_id: Optional[int],
        previous_team_id: Optional[int],
        assigned_agent_id: Optional[int],
        assigned_team_id: Optional[int],
        matched_conditions: List[Dict[str, Any]],
    ) -> AssignmentRuleExecution:
        execution = AssignmentRuleExecution(
            company_id=self.company_id,
            rule_id=rule.id,
            rule_name=rule.name,
            ticket_id=ticket.id,
            trigger=trigger,
            previous_agent_id=previous_agent_id,
            previous_team_id=previous_team_id,
            assigned_agent_id=assigned_agent_id,
            assigned_team_id=assigned_team_id,
            execution_status=status,
            execution_reason=reason,
            matched_conditions=matched_conditions,
        )
        self.db.add(execution)
        logger.info(
            "assignment rule=%s ticket=%s trigger=%s status=%s reason=%s",
            rule.id, ticket.ticket_number, trigger, status, reason,
        )
        return execution

    def record_change(
        self, *, rule: AssignmentRule, action: str, actor_id: Optional[int], snapshot: Dict[str, Any]
    ) -> None:
        self.db.add(
            AssignmentRuleChangeLog(
                company_id=self.company_id,
                rule_id=rule.id,
                action=action,
                actor_id=actor_id,
                snapshot=snapshot,
            )
        )
        logger.info("assignment rule %s %s by user %s", rule.id, action, actor_id)

    # -- reads --------------------------------------------------------------
    def list_executions(
        self,
        *,
        rule_id: Optional[int] = None,
        ticket_ref: Optional[str] = None,
        agent_id: Optional[int] = None,
        status: Optional[str] = None,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Tuple[List[Tuple[AssignmentRuleExecution, Optional[str]]], int]:
        stmt = (
            select(AssignmentRuleExecution, Ticket.ticket_number)
            .join(Ticket, Ticket.id == AssignmentRuleExecution.ticket_id)
            .where(company_scope(AssignmentRuleExecution.company_id, self.company_id))
        )
        if rule_id is not None:
            stmt = stmt.where(AssignmentRuleExecution.rule_id == rule_id)
        if ticket_ref:
            stmt = stmt.where(Ticket.ticket_number.ilike(f"%{ticket_ref.strip()}%"))
        if agent_id is not None:
            stmt = stmt.where(
                (AssignmentRuleExecution.assigned_agent_id == agent_id)
                | (AssignmentRuleExecution.previous_agent_id == agent_id)
            )
        if status:
            stmt = stmt.where(AssignmentRuleExecution.execution_status == status)
        if date_from is not None:
            stmt = stmt.where(AssignmentRuleExecution.executed_at >= date_from)
        if date_to is not None:
            stmt = stmt.where(AssignmentRuleExecution.executed_at <= date_to)

        total = self.db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = self.db.execute(
            stmt.order_by(AssignmentRuleExecution.executed_at.desc(), AssignmentRuleExecution.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        return [(row[0], row[1]) for row in rows], int(total)

    def auto_assigned_ticket_count(self) -> int:
        return int(
            self.db.scalar(
                select(func.count(func.distinct(AssignmentRuleExecution.ticket_id))).where(
                    company_scope(AssignmentRuleExecution.company_id, self.company_id),
                    AssignmentRuleExecution.execution_status == EXEC_ASSIGNED,
                )
            )
            or 0
        )
