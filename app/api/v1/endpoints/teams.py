from typing import List

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.dependencies.auth import require_admin, require_staff
from app.models.user import User
from app.schemas.team import TeamCreate, TeamResponse, TeamUpdate
from app.services.team_service import TeamService

router = APIRouter()


@router.get("", response_model=List[TeamResponse], summary="List teams")
def list_teams(db: Session = Depends(get_db), user: User = Depends(require_staff)) -> List[TeamResponse]:
    service = TeamService(db, user)
    counts = service.open_ticket_counts()
    return [service.to_response(t, counts) for t in service.list()]


@router.post("", response_model=TeamResponse, status_code=status.HTTP_201_CREATED, summary="Create team")
def create_team(payload: TeamCreate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> TeamResponse:
    service = TeamService(db, admin)
    return service.to_response(service.create(payload), service.open_ticket_counts())


@router.put("/{team_id}", response_model=TeamResponse, summary="Update team")
def update_team(team_id: int, payload: TeamUpdate, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> TeamResponse:
    service = TeamService(db, admin)
    return service.to_response(service.update(team_id, payload), service.open_ticket_counts())


@router.delete("/{team_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete team")
def delete_team(team_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)) -> Response:
    TeamService(db, admin).delete(team_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
