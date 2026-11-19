"""Field & operator registry for assignment rule conditions.

This module is the single source of truth for which ticket fields can be used in
conditions, their data types, and which operators each type supports. The
frontend condition builder renders itself from ``GET /assignment-rules/fields``.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------
TEXT = "text"
NUMBER = "number"
SELECT = "select"
DATE = "date"
BOOLEAN = "boolean"
TAGS = "tags"

# ---------------------------------------------------------------------------
# Value shapes expected by operators
#   none   – no value
#   single – one scalar
#   list   – non-empty list of scalars
#   range  – [start, end]
#   period – {"amount": int, "unit": "minutes"|"hours"|"days"|"weeks"}
# ---------------------------------------------------------------------------
V_NONE = "none"
V_SINGLE = "single"
V_LIST = "list"
V_RANGE = "range"
V_PERIOD = "period"

PERIOD_UNITS = ("minutes", "hours", "days", "weeks")


@dataclass(frozen=True)
class OperatorDef:
    key: str
    label: str
    value_shape: str


OPERATORS_BY_TYPE: Dict[str, Tuple[OperatorDef, ...]] = {
    TEXT: (
        OperatorDef("is", "Is", V_SINGLE),
        OperatorDef("is_not", "Is Not", V_SINGLE),
        OperatorDef("contains", "Contains", V_SINGLE),
        OperatorDef("not_contains", "Does Not Contain", V_SINGLE),
        OperatorDef("starts_with", "Starts With", V_SINGLE),
        OperatorDef("ends_with", "Ends With", V_SINGLE),
        OperatorDef("is_empty", "Is Empty", V_NONE),
        OperatorDef("is_not_empty", "Is Not Empty", V_NONE),
    ),
    NUMBER: (
        OperatorDef("eq", "Equals", V_SINGLE),
        OperatorDef("neq", "Not Equals", V_SINGLE),
        OperatorDef("gt", "Greater Than", V_SINGLE),
        OperatorDef("lt", "Less Than", V_SINGLE),
        OperatorDef("gte", "Greater Than or Equal", V_SINGLE),
        OperatorDef("lte", "Less Than or Equal", V_SINGLE),
    ),
    SELECT: (
        OperatorDef("is", "Is", V_SINGLE),
        OperatorDef("is_not", "Is Not", V_SINGLE),
        OperatorDef("in", "Is Any Of", V_LIST),
        OperatorDef("not_in", "Is None Of", V_LIST),
        OperatorDef("is_empty", "Is Empty", V_NONE),
        OperatorDef("is_not_empty", "Is Not Empty", V_NONE),
    ),
    DATE: (
        OperatorDef("is", "Is", V_SINGLE),
        OperatorDef("before", "Is Before", V_SINGLE),
        OperatorDef("after", "Is After", V_SINGLE),
        OperatorDef("between", "Is Between", V_RANGE),
        OperatorDef("within_last", "Is Within Last", V_PERIOD),
        OperatorDef("older_than", "Is Older Than", V_PERIOD),
        OperatorDef("is_empty", "Is Empty", V_NONE),
        OperatorDef("is_not_empty", "Is Not Empty", V_NONE),
    ),
    BOOLEAN: (
        OperatorDef("is_true", "Is True", V_NONE),
        OperatorDef("is_false", "Is False", V_NONE),
    ),
    TAGS: (
        OperatorDef("contains_any", "Contains Any", V_LIST),
        OperatorDef("contains_all", "Contains All", V_LIST),
        OperatorDef("not_contains", "Does Not Contain", V_LIST),
    ),
}


def get_operator(data_type: str, key: str) -> Optional[OperatorDef]:
    for op in OPERATORS_BY_TYPE.get(data_type, ()):
        if op.key == key:
            return op
    return None


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------
TICKET_STATUSES = ["Open", "Investigating", "Suggested", "Escalated", "Resolved", "Closed"]
TICKET_PRIORITIES = ["Low", "Medium", "High", "Urgent"]
TICKET_TYPES = ["Question", "Incident", "Problem", "Feature Request"]
TICKET_SOURCES = ["Web Portal", "Email", "AI Support Chat", "Phone", "API"]
CUSTOMER_TYPES = ["Enterprise", "Growth", "Startup", "Trial"]
SLA_STATUSES = ["On Track", "At Risk", "Breached", "Paused"]
DEFAULT_CATEGORIES = [
    "Technical Support",
    "Billing & Invoices",
    "Account & Access",
    "Feature Requests",
    "General Inquiries",
]

# Option sources resolved at request time from the database
DYNAMIC_AGENTS = "agents"
DYNAMIC_TEAMS = "teams"
DYNAMIC_CATEGORIES = "categories"


@dataclass(frozen=True)
class FieldDef:
    key: str
    label: str
    group: str
    data_type: str
    options: List[str] = field(default_factory=list)
    dynamic_options: Optional[str] = None
    # Select fields whose values are numeric ids (agent / team) rather than labels
    id_valued: bool = False


FIELD_GROUPS = ("Ticket", "Customer", "Assignment", "Other")

FIELDS: Tuple[FieldDef, ...] = (
    # Ticket
    FieldDef("subject", "Subject", "Ticket", TEXT),
    FieldDef("description", "Description", "Ticket", TEXT),
    FieldDef("ticket_id", "Ticket ID", "Ticket", TEXT),
    FieldDef("status", "Status", "Ticket", SELECT, TICKET_STATUSES),
    FieldDef("priority", "Priority", "Ticket", SELECT, TICKET_PRIORITIES),
    FieldDef("category", "Category", "Ticket", SELECT, DEFAULT_CATEGORIES, DYNAMIC_CATEGORIES),
    FieldDef("subcategory", "Subcategory", "Ticket", TEXT),
    FieldDef("ticket_type", "Ticket Type", "Ticket", SELECT, TICKET_TYPES),
    FieldDef("source", "Source", "Ticket", SELECT, TICKET_SOURCES),
    FieldDef("created_at", "Created Date", "Ticket", DATE),
    FieldDef("updated_at", "Updated Date", "Ticket", DATE),
    FieldDef("message_count", "Message Count", "Ticket", NUMBER),
    # Customer
    FieldDef("customer_name", "Customer Name", "Customer", TEXT),
    FieldDef("customer_email", "Customer Email", "Customer", TEXT),
    FieldDef("customer_company", "Customer Company", "Customer", TEXT),
    FieldDef("customer_type", "Customer Type", "Customer", SELECT, CUSTOMER_TYPES),
    # Assignment
    FieldDef("assigned_agent", "Assigned Agent", "Assignment", SELECT, [], DYNAMIC_AGENTS, True),
    FieldDef("assigned_team", "Assigned Team", "Assignment", SELECT, [], DYNAMIC_TEAMS, True),
    FieldDef("is_unassigned", "Is Unassigned", "Assignment", BOOLEAN),
    # Other
    FieldDef("tags", "Tags", "Other", TAGS),
    FieldDef("sla_status", "SLA Status", "Other", SELECT, SLA_STATUSES),
    FieldDef("due_date", "Due Date", "Other", DATE),
)

FIELDS_BY_KEY: Dict[str, FieldDef] = {f.key: f for f in FIELDS}

# Ticket model attributes whose change constitutes a meaningful "update" for the
# ticket_updated trigger. Assignment columns are intentionally excluded so the
# engine's own writes can never re-trigger evaluation.
TRACKED_TICKET_COLUMNS = frozenset(
    {
        "subject",
        "description",
        "status",
        "priority",
        "category",
        "subcategory",
        "ticket_type",
        "source",
        "customer_name",
        "customer_email",
        "customer_company",
        "customer_type",
        "tags",
        "sla_status",
        "due_date",
    }
)
