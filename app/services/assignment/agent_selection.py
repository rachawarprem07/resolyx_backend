"""Agent eligibility and selection strategies (round robin, least assigned)."""

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.dependencies.auth import STAFF_ROLES
from app.models.assignment_rule import AgentAssignmentProfile, AssignmentRotation, AssignmentRule
from app.models.team import Team, TeamMember
from app.models.ticket import CLOSED_TICKET_STATUSES, Ticket
from app.models.user import User


def company_scope(column, company_id: Optional[int]):
    """WHERE clause restricting a company_id column to the caller's tenant."""
    return column.is_(None) if company_id is None else column == company_id


@dataclass
class PoolResult:
    eligible: List[int]
    excluded: Dict[int, str]  # agent id -> reason


class AgentSelectionService:
    """Answers "who may receive this assignment?" for a tenant."""

    def __init__(self, db: Session, company_id: Optional[int]):
        self.db = db
        self.company_id = company_id

    # -- lookups -----------------------------------------------------------
    def agent_query(self):
        return select(User).where(
            company_scope(User.company_id, self.company_id),
            func.lower(User.role).in_(STAFF_ROLES),
        )

    def list_agents(self, active_only: bool = True) -> List[User]:
        stmt = self.agent_query()
        if active_only:
            stmt = stmt.where(User.is_active.is_(True))
        return list(self.db.scalars(stmt.order_by(User.full_name, User.id)))

    def get_agent(self, agent_id: int) -> Optional[User]:
        return self.db.scalar(self.agent_query().where(User.id == agent_id))

    def list_teams(self, active_only: bool = True) -> List[Team]:
        stmt = select(Team).where(company_scope(Team.company_id, self.company_id))
        if active_only:
            stmt = stmt.where(Team.is_active.is_(True))
        return list(self.db.scalars(stmt.order_by(Team.name)))

    def get_team(self, team_id: int) -> Optional[Team]:
        return self.db.scalar(
            select(Team).where(Team.id == team_id, company_scope(Team.company_id, self.company_id))
        )

    def availability(self, user_ids: Iterable[int]) -> Dict[int, bool]:
        ids = list(set(user_ids))
        if not ids:
            return {}
        rows = self.db.execute(
            select(AgentAssignmentProfile.user_id, AgentAssignmentProfile.is_available).where(
                AgentAssignmentProfile.user_id.in_(ids)
            )
        ).all()
        found = {uid: avail for uid, avail in rows}
        return {uid: found.get(uid, True) for uid in ids}

    def set_availability(self, user_id: int, available: bool) -> None:
        profile = self.db.scalar(
            select(AgentAssignmentProfile).where(AgentAssignmentProfile.user_id == user_id)
        )
        if profile is None:
            self.db.add(AgentAssignmentProfile(user_id=user_id, is_available=available))
        else:
            profile.is_available = available

    def open_ticket_counts(
        self, agent_ids: Sequence[int], exclude_ticket_id: Optional[int] = None
    ) -> Dict[int, int]:
        if not agent_ids:
            return {}
        stmt = (
            select(Ticket.assigned_agent_id, func.count(Ticket.id))
            .where(
                Ticket.assigned_agent_id.in_(list(agent_ids)),
                Ticket.status.not_in(list(CLOSED_TICKET_STATUSES)),
                company_scope(Ticket.company_id, self.company_id),
            )
            .group_by(Ticket.assigned_agent_id)
        )
        if exclude_ticket_id is not None:
            stmt = stmt.where(Ticket.id != exclude_ticket_id)
        counts = {aid: 0 for aid in agent_ids}
        for aid, cnt in self.db.execute(stmt).all():
            counts[aid] = int(cnt)
        return counts

    # -- eligibility --------------------------------------------------------
    def check_agent(self, agent_id: int, skip_unavailable: bool) -> Tuple[Optional[User], Optional[str]]:
        """Returns (agent, None) when assignable, else (agent_or_None, reason)."""
        agent = self.get_agent(agent_id)
        if agent is None:
            return None, f"Agent #{agent_id} no longer exists in this workspace."
        label = agent.full_name or agent.email
        # Inactive / deleted agents can never receive assignments.
        if not agent.is_active:
            return agent, f"Agent {label} is inactive."
        if skip_unavailable and not self.availability([agent.id]).get(agent.id, True):
            return agent, f"Agent {label} is unavailable for assignment."
        return agent, None

    def candidate_ids(self, config: Dict[str, Any]) -> List[int]:
        """Pool members from the configured team and/or explicit agent list."""
        ids: set[int] = set(int(a) for a in (config.get("agent_ids") or []))
        team_id = config.get("team_id")
        if team_id:
            team = self.get_team(int(team_id))
            if team is not None and team.is_active:
                ids.update(m.user_id for m in team.members)
        return sorted(ids)

    def eligible_pool(self, config: Dict[str, Any], skip_unavailable: bool) -> PoolResult:
        candidates = self.candidate_ids(config)
        if not candidates:
            return PoolResult([], {})
        users = {
            u.id: u
            for u in self.db.scalars(self.agent_query().where(User.id.in_(candidates)))
        }
        avail = self.availability(candidates)
        eligible: List[int] = []
        excluded: Dict[int, str] = {}
        for uid in candidates:
            user = users.get(uid)
            if user is None:
                excluded[uid] = "not an agent in this workspace"
            elif not user.is_active:
                excluded[uid] = "inactive"
            elif skip_unavailable and not avail.get(uid, True):
                excluded[uid] = "unavailable"
            else:
                eligible.append(uid)
        return PoolResult(sorted(eligible), excluded)


# ---------------------------------------------------------------------------
# Rotation state / per-rule lock
# ---------------------------------------------------------------------------
def get_rotation(db: Session, rule_id: int, lock: bool) -> Optional[AssignmentRotation]:
    """Fetches (and when ``lock`` is set, row-locks) the rotation row, creating it if missing.

    With ``lock=False`` (dry runs) a missing row is not created.
    """
    stmt = select(AssignmentRotation).where(AssignmentRotation.rule_id == rule_id)
    if lock:
        stmt = stmt.with_for_update()
    rotation = db.scalar(stmt)
    if rotation is not None or not lock:
        return rotation
    try:
        with db.begin_nested():
            db.add(AssignmentRotation(rule_id=rule_id, rotation_index=-1))
    except IntegrityError:
        pass  # created concurrently by another transaction
    return db.scalar(stmt)


def ensure_rotation(db: Session, rule: AssignmentRule) -> None:
    """Eagerly creates the rotation row so concurrent first assignments lock the same row."""
    if rule.id is None:
        db.flush()
    exists = db.scalar(select(AssignmentRotation.id).where(AssignmentRotation.rule_id == rule.id))
    if exists is None:
        db.add(AssignmentRotation(rule_id=rule.id, rotation_index=-1))


class RoundRobinService:
    """Cycles through eligible agents in ascending id order.

    The next agent is the first eligible id greater than the last assigned agent,
    wrapping around. Anchoring on the last agent (not a bare index) keeps rotation
    fair when agents join, leave, or become unavailable.
    """

    @staticmethod
    def pick(eligible: Sequence[int], last_agent_id: Optional[int]) -> Tuple[int, int]:
        ordered = sorted(eligible)
        if last_agent_id is not None:
            for idx, uid in enumerate(ordered):
                if uid > last_agent_id:
                    return uid, idx
        return ordered[0], 0

    @staticmethod
    def advance(rotation: AssignmentRotation, agent_id: int, index: int) -> None:
        rotation.last_assigned_agent_id = agent_id
        rotation.rotation_index = index


class LeastAssignedService:
    """Picks the eligible agent with the fewest open tickets.

    Ties are broken by the lowest user id, so the result is deterministic.
    """

    @staticmethod
    def pick(counts: Dict[int, int]) -> Tuple[int, int]:
        agent_id = min(counts, key=lambda uid: (counts[uid], uid))
        return agent_id, counts[agent_id]
