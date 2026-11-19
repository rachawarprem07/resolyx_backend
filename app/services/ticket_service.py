"""Ticket workflows. Creating or updating a ticket fires the assignment engine."""

from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException, ForbiddenException, NotFoundException
from app.core.logging import logger
from app.dependencies.auth import is_staff
from app.models.assignment_rule import TRIGGER_TICKET_CREATED, TRIGGER_TICKET_UPDATED
from app.models.ticket import ASSIGNMENT_SOURCE_MANUAL, Ticket
from app.models.user import User
from app.schemas.ticket import TicketCreate, TicketResponse, TicketUpdate
from app.services.assignment.agent_selection import AgentSelectionService, company_scope
from app.services.assignment.engine import AssignmentEngine, EngineResult
from app.services.assignment.fields import TRACKED_TICKET_COLUMNS

CUSTOMER_EDITABLE = frozenset({"status", "meta", "description"})
TICKET_NUMBER_OFFSET = 1000


def ticket_to_response(ticket: Ticket) -> TicketResponse:
    agent, team = ticket.assigned_agent, ticket.assigned_team
    return TicketResponse(
        id=ticket.id,
        ticket_number=ticket.ticket_number or f"TICK-{ticket.id}",
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status,
        priority=ticket.priority,
        category=ticket.category,
        subcategory=ticket.subcategory,
        ticket_type=ticket.ticket_type,
        source=ticket.source,
        customer_name=ticket.customer_name,
        customer_email=ticket.customer_email,
        customer_company=ticket.customer_company,
        customer_type=ticket.customer_type,
        tags=list(ticket.tags or []),
        sla_status=ticket.sla_status,
        due_date=ticket.due_date,
        assigned_agent_id=ticket.assigned_agent_id,
        assigned_agent_name=(agent.full_name or agent.email) if agent else None,
        assigned_team_id=ticket.assigned_team_id,
        assigned_team_name=team.name if team else None,
        assignment_source=ticket.assignment_source,
        assigned_by_rule_id=ticket.assigned_by_rule_id,
        meta=dict(ticket.meta or {}),
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
    )


def find_ticket(db: Session, company_id: Optional[int], ref: str, lock: bool = False) -> Optional[Ticket]:
    """Looks a ticket up by ticket number (TICK-1001) or numeric id within a tenant."""
    ref = (ref or "").strip()
    stmt = select(Ticket).where(company_scope(Ticket.company_id, company_id))
    if ref.isdigit():
        stmt = stmt.where((Ticket.id == int(ref)) | (Ticket.ticket_number == ref))
    else:
        stmt = stmt.where(Ticket.ticket_number.ilike(ref))
    if lock:
        # Ticket eager-loads agent/team via LEFT OUTER JOIN; PostgreSQL rejects
        # FOR UPDATE on the nullable side of an outer join, so lock tickets only.
        stmt = stmt.with_for_update(of=Ticket)
    return db.scalar(stmt)


class TicketService:
    def __init__(self, db: Session, actor: User):
        self.db = db
        self.actor = actor
        self.company_id = actor.company_id
        self.is_staff = is_staff(actor)
        self.selection = AgentSelectionService(db, self.company_id)

    # ------------------------------------------------------------- queries
    def _visible(self):
        stmt = select(Ticket).where(company_scope(Ticket.company_id, self.company_id))
        if not self.is_staff:
            stmt = stmt.where(Ticket.customer_email.ilike(self.actor.email))
        return stmt

    def list(self, limit: int = 500) -> List[Ticket]:
        return list(
            self.db.scalars(self._visible().order_by(Ticket.created_at.desc(), Ticket.id.desc()).limit(limit))
        )

    def get(self, ref: str, lock: bool = False) -> Ticket:
        ticket = find_ticket(self.db, self.company_id, ref, lock=lock)
        if ticket is None or (
            not self.is_staff and (ticket.customer_email or "").lower() != self.actor.email.lower()
        ):
            raise NotFoundException("Ticket not found.")
        return ticket

    # -------------------------------------------------------------- engine
    def _run_assignment(self, ticket: Ticket, trigger: str) -> Optional[EngineResult]:
        """Runs the engine in a SAVEPOINT: a failure rolls back only the assignment."""
        engine = AssignmentEngine(self.db, self.company_id)
        try:
            with self.db.begin_nested():
                return engine.run_for_ticket(ticket, trigger)
        except Exception:  # noqa: BLE001 - assignment must never block ticket saves
            logger.exception("Assignment engine failed for ticket %s (%s)", ticket.id, trigger)
            return None

    # ------------------------------------------------------------ helpers
    def _validate_agent(self, agent_id: int) -> int:
        agent, problem = self.selection.check_agent(agent_id, skip_unavailable=False)
        if problem:
            raise BadRequestException(problem)
        return agent.id  # type: ignore[union-attr]

    def _validate_team(self, team_id: int) -> int:
        team = self.selection.get_team(team_id)
        if team is None or not team.is_active:
            raise BadRequestException("Team not found or inactive.")
        return team.id

    # ----------------------------------------------------------- mutations
    def create(self, payload: TicketCreate) -> Ticket:
        data = payload.model_dump(exclude_unset=True)
        agent_id = data.pop("assigned_agent_id", None)
        team_id = data.pop("assigned_team_id", None)

        if not self.is_staff:
            if agent_id or team_id:
                raise ForbiddenException("Customers cannot assign tickets.")
            data["customer_email"] = self.actor.email
            data.setdefault("customer_name", self.actor.full_name or self.actor.email)

        ticket = Ticket(
            company_id=self.company_id,
            created_by_id=self.actor.id,
            status=data.pop("status", None) or "Open",
            priority=data.pop("priority", None) or "Medium",
            tags=data.pop("tags", None) or [],
            meta=data.pop("meta", None) or {},
            **data,
        )
        if agent_id:
            ticket.assigned_agent_id = self._validate_agent(agent_id)
        if team_id:
            ticket.assigned_team_id = self._validate_team(team_id)
        if agent_id or team_id:
            ticket.assignment_source = ASSIGNMENT_SOURCE_MANUAL

        self.db.add(ticket)
        self.db.flush()
        ticket.ticket_number = f"TICK-{TICKET_NUMBER_OFFSET + ticket.id}"
        self.db.flush()

        self._run_assignment(ticket, TRIGGER_TICKET_CREATED)
        self.db.commit()
        self.db.refresh(ticket)
        return ticket

    def update(self, ref: str, payload: TicketUpdate) -> Ticket:
        ticket = self.get(ref, lock=True)
        changes: Dict[str, Any] = payload.model_dump(exclude_unset=True)

        if not self.is_staff:
            forbidden = set(changes) - CUSTOMER_EDITABLE
            if forbidden:
                raise ForbiddenException(f"Customers cannot change: {', '.join(sorted(forbidden))}.")

        tracked_changed = False
        assignment_changed = False

        for key in ("assigned_agent_id", "assigned_team_id"):
            if key not in changes:
                continue
            raw = changes.pop(key)
            new_val = None if not raw else (
                self._validate_agent(raw) if key == "assigned_agent_id" else self._validate_team(raw)
            )
            if getattr(ticket, key) != new_val:
                setattr(ticket, key, new_val)
                assignment_changed = True

        for key, value in changes.items():
            if key in ("subject", "status", "priority") and value is None:
                continue  # required columns cannot be nulled
            if key == "tags":
                value = value or []
            if key == "meta":
                value = value or {}
            if getattr(ticket, key) != value:
                setattr(ticket, key, value)
                if key in TRACKED_TICKET_COLUMNS:
                    tracked_changed = True

        if assignment_changed:
            has_any = ticket.assigned_agent_id is not None or ticket.assigned_team_id is not None
            ticket.assignment_source = ASSIGNMENT_SOURCE_MANUAL if has_any else None
            ticket.assigned_by_rule_id = None

        self.db.flush()
        # Only meaningful field edits fire ticket_updated; assignment-only edits
        # (including the engine's own writes) never do, which prevents loops.
        if tracked_changed:
            self._run_assignment(ticket, TRIGGER_TICKET_UPDATED)
        self.db.commit()
        self.db.refresh(ticket)
        return ticket
