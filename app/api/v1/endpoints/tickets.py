from typing import List

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import get_current_user
from app.models.user import User
from app.schemas.ticket import TicketCreate, TicketResponse, TicketUpdate
from app.services.ticket_service import TicketService, ticket_to_response

router = APIRouter()


@router.get("", response_model=List[TicketResponse], summary="List tickets visible to the caller")
def list_tickets(
    limit: int = Query(500, ge=1, le=2000),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> List[TicketResponse]:
    """Staff see every ticket in their workspace; customers see their own."""
    return [ticket_to_response(t) for t in TicketService(db, user).list(limit=limit)]


@router.post("", response_model=TicketResponse, status_code=status.HTTP_201_CREATED, summary="Create ticket")
def create_ticket(
    payload: TicketCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> TicketResponse:
    """Creates a ticket and runs assignment rules with the ``ticket_created`` trigger."""
    return ticket_to_response(TicketService(db, user).create(payload))


@router.get("/{ticket_ref}", response_model=TicketResponse, summary="Get ticket by number or id")
def get_ticket(ticket_ref: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)) -> TicketResponse:
    return ticket_to_response(TicketService(db, user).get(ticket_ref))


@router.patch("/{ticket_ref}", response_model=TicketResponse, summary="Update ticket")
def update_ticket(
    ticket_ref: str, payload: TicketUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)
) -> TicketResponse:
    """Partial update. Changing a tracked field runs rules with the ``ticket_updated``
    trigger; setting ``assigned_agent_id`` / ``assigned_team_id`` records a manual assignment."""
    return ticket_to_response(TicketService(db, user).update(ticket_ref, payload))
