"""Builds the evaluation context (field key -> value) for a ticket."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.models.ticket import CLOSED_TICKET_STATUSES, Ticket

AT_RISK_WINDOW = timedelta(hours=1)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def compute_sla_status(
    status: str, due_date: Optional[datetime], stored: Optional[str], now: datetime
) -> Optional[str]:
    """Derives the SLA status from the due date when the ticket is still open."""
    due = _aware(due_date)
    if due is None or status in CLOSED_TICKET_STATUSES or stored == "Paused":
        return stored
    if now > due:
        return "Breached"
    if due - now <= AT_RISK_WINDOW:
        return "At Risk"
    return "On Track"


def build_context(
    *,
    ticket_number: Optional[str],
    subject: Optional[str],
    description: Optional[str],
    status: str,
    priority: Optional[str],
    category: Optional[str],
    subcategory: Optional[str],
    ticket_type: Optional[str],
    source: Optional[str],
    created_at: Optional[datetime],
    updated_at: Optional[datetime],
    customer_name: Optional[str],
    customer_email: Optional[str],
    customer_company: Optional[str],
    customer_type: Optional[str],
    assigned_agent_id: Optional[int],
    assigned_team_id: Optional[int],
    tags: Optional[List[str]],
    sla_status: Optional[str],
    due_date: Optional[datetime],
    message_count: int,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    return {
        "subject": subject,
        "description": description,
        "ticket_id": ticket_number,
        "status": status,
        "priority": priority,
        "category": category,
        "subcategory": subcategory,
        "ticket_type": ticket_type,
        "source": source,
        "created_at": _aware(created_at),
        "updated_at": _aware(updated_at),
        "message_count": message_count,
        "customer_name": customer_name,
        "customer_email": customer_email,
        "customer_company": customer_company,
        "customer_type": customer_type,
        "assigned_agent": assigned_agent_id,
        "assigned_team": assigned_team_id,
        "is_unassigned": assigned_agent_id is None and assigned_team_id is None,
        "tags": list(tags or []),
        "sla_status": compute_sla_status(status, due_date, sla_status, now),
        "due_date": _aware(due_date),
    }


def ticket_message_count(meta: Optional[Dict[str, Any]]) -> int:
    conversation = (meta or {}).get("conversation")
    return len(conversation) if isinstance(conversation, list) else 0


def context_from_ticket(ticket: Ticket, now: Optional[datetime] = None) -> Dict[str, Any]:
    return build_context(
        ticket_number=ticket.ticket_number,
        subject=ticket.subject,
        description=ticket.description,
        status=ticket.status,
        priority=ticket.priority,
        category=ticket.category,
        subcategory=ticket.subcategory,
        ticket_type=ticket.ticket_type,
        source=ticket.source,
        created_at=ticket.created_at,
        updated_at=ticket.updated_at,
        customer_name=ticket.customer_name,
        customer_email=ticket.customer_email,
        customer_company=ticket.customer_company,
        customer_type=ticket.customer_type,
        assigned_agent_id=ticket.assigned_agent_id,
        assigned_team_id=ticket.assigned_team_id,
        tags=ticket.tags,
        sla_status=ticket.sla_status,
        due_date=ticket.due_date,
        message_count=ticket_message_count(ticket.meta),
        now=now,
    )
