from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TAGS = 50


def _clean_tags(v: Optional[List[str]]) -> Optional[List[str]]:
    if v is None:
        return None
    seen: List[str] = []
    for tag in v:
        t = " ".join(str(tag).split())[:60]
        if t and t not in seen:
            seen.append(t)
    return seen[:MAX_TAGS]


class TicketBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=20000)
    status: Optional[str] = Field(None, max_length=40)
    priority: Optional[str] = Field(None, max_length=20)
    category: Optional[str] = Field(None, max_length=120)
    subcategory: Optional[str] = Field(None, max_length=120)
    ticket_type: Optional[str] = Field(None, max_length=60)
    source: Optional[str] = Field(None, max_length=60)
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_email: Optional[str] = Field(None, max_length=255)
    customer_company: Optional[str] = Field(None, max_length=255)
    customer_type: Optional[str] = Field(None, max_length=60)
    tags: Optional[List[str]] = Field(None, max_length=MAX_TAGS)
    sla_status: Optional[str] = Field(None, max_length=40)
    due_date: Optional[datetime] = None
    meta: Optional[Dict[str, Any]] = Field(
        None, description="Presentation data (conversation thread, AI suggestion, CSAT)."
    )

    @field_validator("tags")
    @classmethod
    def _tags(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        return _clean_tags(v)


class TicketCreate(TicketBase):
    subject: str = Field(..., min_length=1, max_length=500)
    assigned_agent_id: Optional[int] = Field(None, gt=0)
    assigned_team_id: Optional[int] = Field(None, gt=0)


class TicketUpdate(TicketBase):
    """Partial update. Setting assignment fields here records a *manual* assignment."""

    assigned_agent_id: Optional[int] = Field(None, ge=0, description="0 clears the agent")
    assigned_team_id: Optional[int] = Field(None, ge=0, description="0 clears the team")


class TicketResponse(BaseModel):
    id: int
    ticket_number: str
    subject: str
    description: Optional[str]
    status: str
    priority: str
    category: Optional[str]
    subcategory: Optional[str]
    ticket_type: Optional[str]
    source: Optional[str]
    customer_name: Optional[str]
    customer_email: Optional[str]
    customer_company: Optional[str]
    customer_type: Optional[str]
    tags: List[str]
    sla_status: Optional[str]
    due_date: Optional[datetime]
    assigned_agent_id: Optional[int]
    assigned_agent_name: Optional[str]
    assigned_team_id: Optional[int]
    assigned_team_name: Optional[str]
    assignment_source: Optional[str]
    assigned_by_rule_id: Optional[int]
    meta: Dict[str, Any]
    created_at: datetime
    updated_at: datetime
