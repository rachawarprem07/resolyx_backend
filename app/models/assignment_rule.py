from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


TRIGGER_TICKET_CREATED = "ticket_created"
TRIGGER_TICKET_UPDATED = "ticket_updated"
VALID_TRIGGERS = (TRIGGER_TICKET_CREATED, TRIGGER_TICKET_UPDATED)

ASSIGN_AGENT = "agent"
ASSIGN_TEAM = "team"
ASSIGN_ROUND_ROBIN = "round_robin"
ASSIGN_LEAST_ASSIGNED = "least_assigned"
VALID_ASSIGNMENT_TYPES = (ASSIGN_AGENT, ASSIGN_TEAM, ASSIGN_ROUND_ROBIN, ASSIGN_LEAST_ASSIGNED)
POOL_ASSIGNMENT_TYPES = (ASSIGN_ROUND_ROBIN, ASSIGN_LEAST_ASSIGNED)

EXEC_ASSIGNED = "assigned"
EXEC_NO_CHANGE = "no_change"
EXEC_SKIPPED = "skipped"
EXEC_FAILED = "failed"
VALID_EXECUTION_STATUSES = (EXEC_ASSIGNED, EXEC_NO_CHANGE, EXEC_SKIPPED, EXEC_FAILED)


class AssignmentRule(Base):
    """An automated ticket assignment rule.

    ``condition_json`` holds a structured condition tree (never SQL or code)::

        {"type": "group", "logic": "AND"|"OR", "children": [<group|condition>, ...]}
        {"type": "condition", "field": "priority", "operator": "is", "value": "High"}
    """

    __tablename__ = "assignment_rules"
    __table_args__ = (
        UniqueConstraint("company_id", "name", name="uq_assignment_rules_company_name"),
        Index("ix_assignment_rules_company_active_priority", "company_id", "is_active", "priority"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=10, nullable=False)
    triggers: Mapped[List[str]] = mapped_column(JSON, default=list, nullable=False)
    condition_json: Mapped[Dict[str, Any]] = mapped_column(JSON, nullable=False)
    assignment_type: Mapped[str] = mapped_column(String(30), nullable=False)
    assignment_config: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    # When False (default) a manual assignment made by a person is never replaced.
    overwrite_existing_assignment: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When True, tickets already assigned by an earlier automated run are reassigned.
    reassign_existing: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # When True, agents flagged unavailable are excluded from selection.
    skip_unavailable_agents: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # When True, evaluation continues with lower-priority rules after this one matches.
    continue_to_next_rule: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    created_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    updated_by: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<AssignmentRule id={self.id} name={self.name} priority={self.priority}>"


class AssignmentRuleExecution(Base):
    """Audit record of a rule acting (or declining to act) on a ticket."""

    __tablename__ = "assignment_rule_executions"
    __table_args__ = (
        Index("ix_assignment_exec_company_executed", "company_id", "executed_at"),
        Index("ix_assignment_exec_rule_executed", "rule_id", "executed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("assignment_rules.id", ondelete="SET NULL"), nullable=True
    )
    rule_name: Mapped[str] = mapped_column(String(120), nullable=False)
    ticket_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    trigger: Mapped[str] = mapped_column(String(30), nullable=False)
    previous_agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    previous_team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    assigned_agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    assigned_team_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True
    )
    execution_status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    execution_reason: Mapped[str] = mapped_column(Text, nullable=False)
    matched_conditions: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)


class AssignmentRotation(Base):
    """Per-rule rotation state. The row doubles as the per-rule lock that
    serializes round-robin and least-assigned selection across concurrent requests."""

    __tablename__ = "assignment_rotations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    rule_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("assignment_rules.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    last_assigned_agent_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    rotation_index: Mapped[int] = mapped_column(Integer, default=-1, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class AgentAssignmentProfile(Base):
    """Assignment availability for an agent (absent row == available)."""

    __tablename__ = "agent_assignment_profiles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    is_available: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


class AssignmentRuleChangeLog(Base):
    """Audit trail of administrative changes to assignment rules."""

    __tablename__ = "assignment_rule_change_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    company_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("companies.id", ondelete="CASCADE"), nullable=True
    )
    rule_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("assignment_rules.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(30), nullable=False)
    actor_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    snapshot: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, nullable=False)
