from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

TriggerLiteral = Literal["ticket_created", "ticket_updated"]
AssignmentTypeLiteral = Literal["agent", "team", "round_robin", "least_assigned"]
ExecutionStatusLiteral = Literal["assigned", "no_change", "skipped", "failed"]


class AssignmentConfig(BaseModel):
    """Action target. ``agent`` needs agent_id, ``team`` needs team_id, and
    ``round_robin`` / ``least_assigned`` need team_id and/or agent_ids (the pool)."""

    model_config = ConfigDict(extra="forbid")

    agent_id: Optional[int] = Field(None, gt=0)
    team_id: Optional[int] = Field(None, gt=0)
    agent_ids: List[int] = Field(default_factory=list, max_length=500)

    @field_validator("agent_ids")
    @classmethod
    def _unique_positive(cls, v: List[int]) -> List[int]:
        if any(i <= 0 for i in v):
            raise ValueError("agent ids must be positive integers")
        return sorted(set(v))


class AssignmentRuleBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    is_active: bool = True
    priority: int = Field(10, ge=0, le=100000, description="Lower numbers run first.")
    triggers: List[TriggerLiteral] = Field(..., min_length=1)
    conditions: Dict[str, Any] = Field(..., description="Condition tree (see API docs).")
    assignment_type: AssignmentTypeLiteral
    assignment_config: AssignmentConfig = Field(default_factory=AssignmentConfig)
    overwrite_existing_assignment: bool = False
    reassign_existing: bool = False
    skip_unavailable_agents: bool = True
    continue_to_next_rule: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Rule name is required")
        return v

    @field_validator("description")
    @classmethod
    def _strip_description(cls, v: Optional[str]) -> Optional[str]:
        return v.strip() or None if v is not None else None

    @field_validator("triggers")
    @classmethod
    def _dedupe_triggers(cls, v: List[str]) -> List[str]:
        return [t for t in ("ticket_created", "ticket_updated") if t in v]

    @model_validator(mode="after")
    def _check_config(self) -> "AssignmentRuleBase":
        cfg = self.assignment_config
        if self.assignment_type == "agent" and not cfg.agent_id:
            raise ValueError("Select an agent for 'Assign to Agent'.")
        if self.assignment_type == "team" and not cfg.team_id:
            raise ValueError("Select a team for 'Assign to Team'.")
        if self.assignment_type in ("round_robin", "least_assigned") and not (
            cfg.team_id or cfg.agent_ids
        ):
            raise ValueError("Select a team or at least one agent for the assignment pool.")
        # Drop config keys irrelevant to the chosen type.
        if self.assignment_type == "agent":
            self.assignment_config = AssignmentConfig(agent_id=cfg.agent_id)
        elif self.assignment_type == "team":
            self.assignment_config = AssignmentConfig(team_id=cfg.team_id)
        else:
            self.assignment_config = AssignmentConfig(team_id=cfg.team_id, agent_ids=cfg.agent_ids)
        return self


class AssignmentRuleCreate(AssignmentRuleBase):
    pass


class AssignmentRuleUpdate(AssignmentRuleBase):
    pass


class AssignmentRuleStatusUpdate(BaseModel):
    is_active: bool


class AssignmentRuleResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    is_active: bool
    priority: int
    triggers: List[str]
    conditions: Dict[str, Any]
    conditions_summary: str
    condition_count: int
    assignment_type: str
    assignment_config: Dict[str, Any]
    assigned_to_label: str
    overwrite_existing_assignment: bool
    reassign_existing: bool
    skip_unavailable_agents: bool
    continue_to_next_rule: bool
    created_by: Optional[int]
    created_by_name: Optional[str]
    created_at: datetime
    updated_at: datetime


class AssignmentRuleStats(BaseModel):
    total_rules: int
    active_rules: int
    inactive_rules: int
    tickets_assigned_automatically: int


class AssignmentRuleListResponse(BaseModel):
    items: List[AssignmentRuleResponse]
    total: int
    stats: AssignmentRuleStats


# ---------------------------------------------------------------- test / preview
class SampleTicket(BaseModel):
    """Hypothetical ticket data for testing rules without a real ticket."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: Optional[str] = Field(None, max_length=32)
    subject: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None, max_length=10000)
    status: str = Field("Open", max_length=40)
    priority: Optional[str] = Field("Medium", max_length=20)
    category: Optional[str] = Field(None, max_length=120)
    subcategory: Optional[str] = Field(None, max_length=120)
    ticket_type: Optional[str] = Field(None, max_length=60)
    source: Optional[str] = Field(None, max_length=60)
    customer_name: Optional[str] = Field(None, max_length=255)
    customer_email: Optional[str] = Field(None, max_length=255)
    customer_company: Optional[str] = Field(None, max_length=255)
    customer_type: Optional[str] = Field(None, max_length=60)
    tags: List[str] = Field(default_factory=list, max_length=100)
    sla_status: Optional[str] = Field(None, max_length=40)
    due_date: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    message_count: int = Field(0, ge=0, le=100000)
    assigned_agent_id: Optional[int] = Field(None, gt=0)
    assigned_team_id: Optional[int] = Field(None, gt=0)
    assignment_source: Optional[Literal["manual", "rule"]] = None


class TicketTarget(BaseModel):
    """Identifies the ticket to evaluate: an existing ticket or sample data."""

    ticket_id: Optional[str] = Field(None, max_length=32, description="Ticket number (TICK-1001) or numeric id")
    sample_ticket: Optional[SampleTicket] = None

    @model_validator(mode="after")
    def _one_source(self) -> "TicketTarget":
        if bool(self.ticket_id) == bool(self.sample_ticket):
            raise ValueError("Provide either ticket_id or sample_ticket.")
        return self


class RuleTestRequest(TicketTarget):
    rule: Optional[AssignmentRuleBase] = Field(
        None, description="Unsaved edits to test instead of the stored rule."
    )


class PreviewRequest(TicketTarget):
    trigger: Optional[TriggerLiteral] = Field(
        None, description="Only consider rules with this trigger (default: all active rules)."
    )
    rule: Optional[AssignmentRuleBase] = Field(
        None, description="Test this unsaved rule draft alone instead of the active rule set."
    )


class ConditionTrace(BaseModel):
    path: str
    field: str
    field_label: str
    operator: str
    operator_label: str
    expected: Any
    actual: Any
    passed: bool


class AssignmentTarget(BaseModel):
    assignment_type: Optional[str] = None
    agent_id: Optional[int] = None
    agent_name: Optional[str] = None
    team_id: Optional[int] = None
    team_name: Optional[str] = None


class RuleEvaluation(BaseModel):
    rule_id: Optional[int]
    rule_name: str
    priority: int
    matched: bool
    status: Optional[str]
    reason: str
    passed_conditions: List[ConditionTrace]
    failed_conditions: List[ConditionTrace]
    expected_assignment: Optional[AssignmentTarget]


class RuleTestResponse(RuleEvaluation):
    ticket_ref: str
    would_change_assignment: bool


class PreviewResponse(BaseModel):
    ticket_ref: str
    trigger: Optional[str]
    rules_considered: int
    winning_rule_id: Optional[int]
    winning_rule_name: Optional[str]
    current_assignment: AssignmentTarget
    final_assignment: AssignmentTarget
    would_change_assignment: bool
    summary: str
    evaluations: List[RuleEvaluation]


# ------------------------------------------------------------------- history
class ExecutionResponse(BaseModel):
    id: int
    rule_id: Optional[int]
    rule_name: str
    ticket_id: int
    ticket_number: Optional[str]
    trigger: str
    previous_agent_id: Optional[int]
    previous_agent_name: Optional[str]
    previous_team_id: Optional[int]
    previous_team_name: Optional[str]
    assigned_agent_id: Optional[int]
    assigned_agent_name: Optional[str]
    assigned_team_id: Optional[int]
    assigned_team_name: Optional[str]
    execution_status: str
    execution_reason: str
    matched_conditions: List[Dict[str, Any]]
    executed_at: datetime


class ExecutionListResponse(BaseModel):
    items: List[ExecutionResponse]
    total: int


# ------------------------------------------------------------- lookups / meta
class AgentOption(BaseModel):
    id: int
    name: str
    email: str
    role: str
    is_available: bool
    open_tickets: int
    team_ids: List[int]


class AgentAvailabilityUpdate(BaseModel):
    is_available: bool


class TeamOption(BaseModel):
    id: int
    name: str
    member_count: int
    member_ids: List[int]


class FieldOption(BaseModel):
    value: Any
    label: str


class FieldMetadata(BaseModel):
    key: str
    label: str
    group: str
    data_type: str
    id_valued: bool
    options: List[FieldOption]


class OperatorMetadata(BaseModel):
    key: str
    label: str
    value_shape: str


class FieldsMetadataResponse(BaseModel):
    fields: List[FieldMetadata]
    operators: Dict[str, List[OperatorMetadata]]
    period_units: List[str]
    limits: Dict[str, int]
