from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROUTING_STRATEGIES = ("Round-Robin", "Least Loaded", "Skill-Based", "Manual Broadcast")


class TeamBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    routing_strategy: str = Field("Round-Robin", max_length=40)
    sla_target_hours: int = Field(4, ge=1, le=720)
    lead_user_id: Optional[int] = Field(None, gt=0)
    member_ids: List[int] = Field(default_factory=list, max_length=500)
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Team name is required")
        return v

    @field_validator("routing_strategy")
    @classmethod
    def _strategy(cls, v: str) -> str:
        if v not in ROUTING_STRATEGIES:
            raise ValueError(f"routing_strategy must be one of {', '.join(ROUTING_STRATEGIES)}")
        return v

    @field_validator("member_ids")
    @classmethod
    def _members(cls, v: List[int]) -> List[int]:
        if any(i <= 0 for i in v):
            raise ValueError("member ids must be positive integers")
        return sorted(set(v))


class TeamCreate(TeamBase):
    pass


class TeamUpdate(TeamBase):
    pass


class TeamMemberResponse(BaseModel):
    id: int
    name: str
    email: str
    is_active: bool


class TeamResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    routing_strategy: str
    sla_target_hours: int
    lead_user_id: Optional[int]
    lead_name: Optional[str]
    lead_email: Optional[str]
    is_active: bool
    members: List[TeamMemberResponse]
    open_tickets: int
    created_at: datetime
    updated_at: datetime
