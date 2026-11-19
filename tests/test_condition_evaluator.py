"""Unit tests for condition validation and evaluation (no database)."""

from datetime import datetime, timedelta, timezone

import pytest

from app.services.assignment.conditions import (
    MAX_CONDITIONS,
    ConditionEvaluator,
    ConditionValidationError,
    summarize_tree,
    validate_condition_tree,
)
from app.services.assignment.context import compute_sla_status
from tests.conftest import cond, group

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)

CTX = {
    "subject": "VPN keeps disconnecting",
    "description": "",
    "ticket_id": "TICK-1042",
    "status": "Open",
    "priority": "High",
    "category": "Technical Support",
    "customer_type": "Enterprise",
    "customer_email": "ops@bigcorp.com",
    "assigned_agent": None,
    "assigned_team": 7,
    "is_unassigned": False,
    "message_count": 3,
    "tags": ["vpn", "Network"],
    "created_at": NOW - timedelta(hours=2),
    "due_date": None,
}


def run(tree, ctx=CTX):
    return ConditionEvaluator(NOW).evaluate(validate_condition_tree(tree), ctx)


def single(c, ctx=CTX) -> bool:
    return run(group("AND", c), ctx).matched


# ------------------------------------------------------------------ operators
@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("is", "vpn keeps disconnecting", True),  # case-insensitive
        ("is", "VPN", False),
        ("is_not", "VPN", True),
        ("contains", "disconnect", True),
        ("contains", "printer", False),
        ("not_contains", "printer", True),
        ("starts_with", "vpn", True),
        ("ends_with", "connecting", True),
        ("ends_with", "vpn", False),
    ],
)
def test_text_operators(operator, value, expected):
    assert single(cond("subject", operator, value)) is expected


def test_text_empty_operators():
    assert single(cond("description", "is_empty")) is True
    assert single(cond("subject", "is_empty")) is False
    assert single(cond("subject", "is_not_empty")) is True
    assert single(cond("subcategory", "is_empty")) is True  # missing == empty


@pytest.mark.parametrize(
    "operator,value,expected",
    [("eq", 3, True), ("neq", 3, False), ("gt", 2, True), ("lt", 3, False),
     ("gte", 3, True), ("lte", 2.5, False), ("eq", "3", True)],
)
def test_number_operators(operator, value, expected):
    assert single(cond("message_count", operator, value)) is expected


@pytest.mark.parametrize(
    "operator,value,expected",
    [("is", "high", True), ("is_not", "High", False), ("in", ["Low", "High"], True),
     ("in", ["Low"], False), ("not_in", ["Low", "Urgent"], True), ("not_in", ["High"], False)],
)
def test_select_operators(operator, value, expected):
    assert single(cond("priority", operator, value)) is expected


def test_select_id_valued_and_empty():
    assert single(cond("assigned_team", "is", 7)) is True
    assert single(cond("assigned_team", "in", [1, 7])) is True
    assert single(cond("assigned_agent", "is_empty")) is True
    assert single(cond("assigned_agent", "is", 7)) is False
    assert single(cond("assigned_agent", "is_not", 7)) is True


@pytest.mark.parametrize(
    "operator,value,expected",
    [
        ("is", "2026-11-19", True),
        ("before", "2026-11-20", True),
        ("before", "2026-11-19", False),
        ("after", "2026-11-18", True),
        ("between", ["2026-11-01", "2026-11-19"], True),
        ("between", ["2026-11-20", "2026-11-30"], False),
        ("within_last", {"amount": 3, "unit": "hours"}, True),
        ("within_last", {"amount": 30, "unit": "minutes"}, False),
        ("older_than", {"amount": 1, "unit": "hours"}, True),
        ("older_than", {"amount": 1, "unit": "days"}, False),
    ],
)
def test_date_operators(operator, value, expected):
    assert single(cond("created_at", operator, value)) is expected


def test_date_missing_value_never_matches_comparisons():
    assert single(cond("due_date", "before", "2030-01-01")) is False
    assert single(cond("due_date", "is_empty")) is True


def test_boolean_operators():
    assert single(cond("is_unassigned", "is_false")) is True
    assert single(cond("is_unassigned", "is_true")) is False


@pytest.mark.parametrize(
    "operator,value,expected",
    [("contains_any", ["network", "billing"], True), ("contains_any", ["billing"], False),
     ("contains_all", ["VPN", "network"], True), ("contains_all", ["vpn", "billing"], False),
     ("not_contains", ["billing"], True), ("not_contains", ["vpn"], False)],
)
def test_tag_operators(operator, value, expected):
    assert single(cond("tags", operator, value)) is expected


# ------------------------------------------------------------------- logic
def test_and_requires_all():
    tree = group("AND", cond("priority", "is", "High"), cond("status", "is", "Closed"))
    result = run(tree)
    assert result.matched is False
    assert [c.passed for c in result.conditions] == [True, False]


def test_or_requires_any():
    tree = group("OR", cond("priority", "is", "Low"), cond("status", "is", "Open"))
    assert run(tree).matched is True


def test_nested_groups():
    # (Priority = High AND Category = Technical) OR (Priority = Urgent AND Customer Type = Enterprise)
    tree = group(
        "OR",
        group("AND", cond("priority", "is", "High"), cond("category", "is", "Technical Support")),
        group("AND", cond("priority", "is", "Urgent"), cond("customer_type", "is", "Enterprise")),
    )
    assert run(tree).matched is True
    assert run(tree, {**CTX, "priority": "Urgent"}).matched is True
    assert run(tree, {**CTX, "priority": "Urgent", "customer_type": "Trial"}).matched is False


def test_trace_reports_every_condition_with_paths():
    tree = group("OR", cond("priority", "is", "High"), group("AND", cond("status", "is", "Closed")))
    result = run(tree)
    assert [c.path for c in result.conditions] == ["root.0", "root.1.0"]
    assert len(result.passed) == 1 and len(result.failed) == 1
    assert result.conditions[0].field_label == "Priority"


def test_summary():
    tree = group("OR", group("AND", cond("priority", "is", "High"), cond("tags", "contains_any", ["vpn"])),
                 cond("assigned_team", "is", 7))
    text = summarize_tree(validate_condition_tree(tree), {"assigned_team": {7: "Tier 1"}})
    assert text == "(Priority is High AND Tags contains any vpn) OR Assigned Team is Tier 1"


# --------------------------------------------------------------- validation
@pytest.mark.parametrize(
    "tree,fragment",
    [
        (group("AND", cond("nope", "is", "x")), "unknown field"),
        (group("AND", cond("priority", "contains", "x")), "not supported"),
        (group("AND", cond("subject", "is", "  ")), "value is required"),
        (group("AND", cond("message_count", "gt", "abc")), "must be a number"),
        (group("AND", cond("created_at", "is", "19/11/2026")), "YYYY-MM-DD"),
        (group("AND", cond("created_at", "between", ["2026-12-01", "2026-11-01"])), "on or before"),
        (group("AND", cond("created_at", "within_last", {"amount": 0, "unit": "days"})), "between 1"),
        (group("AND", cond("created_at", "within_last", {"amount": 2, "unit": "years"})), "unit must be"),
        (group("AND", cond("priority", "in", [])), "at least one"),
        (group("AND", cond("assigned_agent", "is", "bob")), "valid id"),
        (group("XOR", cond("priority", "is", "High")), "AND or OR"),
        (group("AND"), "at least one condition"),
        (group("AND", group("AND", group("AND", group("AND", group("AND", cond("priority", "is", "High")))))), "nested"),
        ({"type": "condition", "field": "priority"}, "must be a group"),
        (group("AND", {"type": "sql", "query": "DROP TABLE"}), "type must be"),
    ],
)
def test_invalid_trees_are_rejected(tree, fragment):
    with pytest.raises(ConditionValidationError) as exc:
        validate_condition_tree(tree)
    assert fragment in str(exc.value)
    assert exc.value.errors and "path" in exc.value.errors[0]


def test_condition_limit():
    tree = group("OR", *[group("AND", *[cond("priority", "is", "High")] * 20) for _ in range(3)])
    with pytest.raises(ConditionValidationError, match=str(MAX_CONDITIONS)):
        validate_condition_tree(tree)


def test_normalization_strips_and_dedupes():
    tree = validate_condition_tree(group("and", cond("priority", "in", [" High", "High", "Low"])))
    assert tree["logic"] == "AND"
    assert tree["children"][0]["value"] == ["High", "Low"]
    no_value = validate_condition_tree(group("AND", cond("subject", "is_empty", "ignored")))
    assert no_value["children"][0]["value"] is None


def test_sla_status_is_derived_from_due_date():
    assert compute_sla_status("Open", NOW - timedelta(minutes=1), None, NOW) == "Breached"
    assert compute_sla_status("Open", NOW + timedelta(minutes=30), None, NOW) == "At Risk"
    assert compute_sla_status("Open", NOW + timedelta(hours=5), None, NOW) == "On Track"
    assert compute_sla_status("Resolved", NOW - timedelta(hours=5), "On Track", NOW) == "On Track"
    assert compute_sla_status("Open", None, "Paused", NOW) == "Paused"
