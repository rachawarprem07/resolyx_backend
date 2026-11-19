"""Support team management with real agent membership."""

from typing import Dict, List

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestException, ConflictException, NotFoundException
from app.models.assignment_rule import AssignmentRule
from app.models.team import Team, TeamMember
from app.models.ticket import CLOSED_TICKET_STATUSES, Ticket
from app.models.user import User
from app.schemas.team import TeamBase, TeamMemberResponse, TeamResponse
from app.services.assignment.agent_selection import AgentSelectionService, company_scope


class TeamService:
    def __init__(self, db: Session, actor: User):
        self.db = db
        self.actor = actor
        self.company_id = actor.company_id
        self.selection = AgentSelectionService(db, self.company_id)

    def list(self) -> List[Team]:
        return self.selection.list_teams(active_only=False)

    def get(self, team_id: int) -> Team:
        team = self.selection.get_team(team_id)
        if team is None:
            raise NotFoundException("Team not found.")
        return team

    def _validate_members(self, payload: TeamBase) -> None:
        ids = set(payload.member_ids)
        if payload.lead_user_id:
            ids.add(payload.lead_user_id)
        if not ids:
            return
        found = set(self.db.scalars(self.selection.agent_query().where(User.id.in_(ids)).with_only_columns(User.id)))
        missing = sorted(ids - found)
        if missing:
            raise BadRequestException(f"Unknown agent ids for this workspace: {missing}.")

    def _apply(self, team: Team, payload: TeamBase) -> None:
        team.name = payload.name
        team.description = payload.description
        team.routing_strategy = payload.routing_strategy
        team.sla_target_hours = payload.sla_target_hours
        team.lead_user_id = payload.lead_user_id
        team.is_active = payload.is_active
        wanted = set(payload.member_ids)
        team.members = [m for m in team.members if m.user_id in wanted] + [
            TeamMember(user_id=uid) for uid in sorted(wanted - set(team.member_ids))
        ]

    def _commit(self, team: Team) -> Team:
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            raise ConflictException("A team with this name already exists.") from None
        self.db.refresh(team)
        return team

    def create(self, payload: TeamBase) -> Team:
        self._validate_members(payload)
        team = Team(company_id=self.company_id)
        self._apply(team, payload)
        self.db.add(team)
        return self._commit(team)

    def update(self, team_id: int, payload: TeamBase) -> Team:
        team = self.get(team_id)
        self._validate_members(payload)
        self._apply(team, payload)
        return self._commit(team)

    def delete(self, team_id: int) -> None:
        team = self.get(team_id)
        rules = self.db.scalars(
            select(AssignmentRule).where(company_scope(AssignmentRule.company_id, self.company_id))
        ).all()
        using = [r.name for r in rules if (r.assignment_config or {}).get("team_id") == team.id]
        if using:
            raise ConflictException(
                f"Team is used by assignment rule(s): {', '.join(using)}. Update those rules first."
            )
        self.db.delete(team)
        self.db.commit()

    def open_ticket_counts(self) -> Dict[int, int]:
        rows = self.db.execute(
            select(Ticket.assigned_team_id, func.count(Ticket.id))
            .where(
                company_scope(Ticket.company_id, self.company_id),
                Ticket.assigned_team_id.is_not(None),
                Ticket.status.not_in(list(CLOSED_TICKET_STATUSES)),
            )
            .group_by(Ticket.assigned_team_id)
        ).all()
        return {tid: int(c) for tid, c in rows}

    def to_response(self, team: Team, open_counts: Dict[int, int]) -> TeamResponse:
        ids = team.member_ids + ([team.lead_user_id] if team.lead_user_id else [])
        users = {u.id: u for u in self.db.scalars(select(User).where(User.id.in_(ids)))} if ids else {}
        lead = users.get(team.lead_user_id) if team.lead_user_id else None
        return TeamResponse(
            id=team.id,
            name=team.name,
            description=team.description,
            routing_strategy=team.routing_strategy,
            sla_target_hours=team.sla_target_hours,
            lead_user_id=team.lead_user_id,
            lead_name=(lead.full_name or lead.email) if lead else None,
            lead_email=lead.email if lead else None,
            is_active=team.is_active,
            members=[
                TeamMemberResponse(
                    id=u.id, name=u.full_name or u.email, email=u.email, is_active=u.is_active
                )
                for uid in team.member_ids
                if (u := users.get(uid)) is not None
            ],
            open_tickets=open_counts.get(team.id, 0),
            created_at=team.created_at,
            updated_at=team.updated_at,
        )
