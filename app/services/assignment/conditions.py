"""Condition tree validation and evaluation.

Conditions are stored as structured JSON and interpreted here; they are never
turned into SQL or executable code.

Tree shape::

    group     := {"type": "group", "logic": "AND"|"OR", "children": [node, ...]}
    condition := {"type": "condition", "field": str, "operator": str, "value": Any}
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.services.assignment import fields as F

MAX_DEPTH = 4  # root group counts as depth 1
MAX_CONDITIONS = 50
MAX_CHILDREN_PER_GROUP = 25
MAX_STRING_LENGTH = 500
MAX_LIST_ITEMS = 100
MAX_PERIOD_AMOUNT = 3650


class ConditionValidationError(ValueError):
    """Raised when a condition tree is malformed. ``errors`` lists each problem."""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = errors
        super().__init__("; ".join(f"{e['path']}: {e['message']}" for e in errors))


# ---------------------------------------------------------------------------
# Validation / normalization
# ---------------------------------------------------------------------------
def _clean_str(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError("must be a text value")
    text = str(value).strip()
    if not text:
        raise ValueError("value is required")
    if len(text) > MAX_STRING_LENGTH:
        raise ValueError(f"must be at most {MAX_STRING_LENGTH} characters")
    # Strip control characters (keep ordinary whitespace)
    return "".join(ch for ch in text if ch.isprintable() or ch in " \t")


def _clean_number(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("must be a number")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise ValueError("must be a number") from None
    if math.isnan(num) or math.isinf(num):
        raise ValueError("must be a finite number")
    return num


def _clean_date(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("must be a date in YYYY-MM-DD format")
    try:
        return date.fromisoformat(value.strip()[:10]).isoformat()
    except ValueError:
        raise ValueError("must be a date in YYYY-MM-DD format") from None


def _clean_select(fdef: F.FieldDef, value: Any) -> Any:
    if fdef.id_valued:
        if isinstance(value, bool):
            raise ValueError("must be a valid id")
        try:
            ident = int(value)
        except (TypeError, ValueError):
            raise ValueError("must be a valid id") from None
        if ident <= 0:
            raise ValueError("must be a valid id")
        return ident
    return _clean_str(value)


def _clean_list(value: Any, item_cleaner) -> List[Any]:
    if not isinstance(value, list) or not value:
        raise ValueError("select at least one value")
    if len(value) > MAX_LIST_ITEMS:
        raise ValueError(f"at most {MAX_LIST_ITEMS} values are allowed")
    cleaned: List[Any] = []
    for item in value:
        c = item_cleaner(item)
        if c not in cleaned:
            cleaned.append(c)
    return cleaned


def _normalize_value(fdef: F.FieldDef, op: F.OperatorDef, value: Any) -> Any:
    shape = op.value_shape
    if shape == F.V_NONE:
        return None

    if fdef.data_type == F.DATE:
        if shape == F.V_SINGLE:
            return _clean_date(value)
        if shape == F.V_RANGE:
            if not isinstance(value, list) or len(value) != 2:
                raise ValueError("provide a start and end date")
            start, end = _clean_date(value[0]), _clean_date(value[1])
            if start > end:
                raise ValueError("start date must be on or before end date")
            return [start, end]
        if shape == F.V_PERIOD:
            if not isinstance(value, dict):
                raise ValueError("provide an amount and unit")
            try:
                amount = int(value.get("amount"))  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError("amount must be a whole number") from None
            if amount < 1 or amount > MAX_PERIOD_AMOUNT:
                raise ValueError(f"amount must be between 1 and {MAX_PERIOD_AMOUNT}")
            unit = value.get("unit")
            if unit not in F.PERIOD_UNITS:
                raise ValueError(f"unit must be one of {', '.join(F.PERIOD_UNITS)}")
            return {"amount": amount, "unit": unit}

    if fdef.data_type == F.NUMBER:
        return _clean_number(value)

    if fdef.data_type == F.SELECT:
        if shape == F.V_LIST:
            return _clean_list(value, lambda v: _clean_select(fdef, v))
        return _clean_select(fdef, value)

    if fdef.data_type == F.TAGS:
        return _clean_list(value, _clean_str)

    return _clean_str(value)


def validate_condition_tree(raw: Any) -> Dict[str, Any]:
    """Validates and normalizes a condition tree, raising ConditionValidationError."""
    errors: List[Dict[str, str]] = []
    counter = {"conditions": 0}

    def err(path: str, message: str) -> None:
        errors.append({"path": path, "message": message})

    def walk(node: Any, path: str, depth: int) -> Optional[Dict[str, Any]]:
        if not isinstance(node, dict):
            err(path, "must be an object")
            return None
        node_type = node.get("type")

        if node_type == "group":
            if depth > MAX_DEPTH:
                err(path, f"groups can be nested at most {MAX_DEPTH} levels deep")
                return None
            logic = str(node.get("logic", "")).upper()
            if logic not in ("AND", "OR"):
                err(path, "logic must be AND or OR")
            children = node.get("children")
            if not isinstance(children, list) or not children:
                err(path, "a group must contain at least one condition")
                return None
            if len(children) > MAX_CHILDREN_PER_GROUP:
                err(path, f"a group can contain at most {MAX_CHILDREN_PER_GROUP} items")
                return None
            out_children = []
            for idx, child in enumerate(children):
                c = walk(child, f"{path}.{idx}", depth + 1)
                if c is not None:
                    out_children.append(c)
            out: Dict[str, Any] = {"type": "group", "logic": logic, "children": out_children}
            if isinstance(node.get("id"), str):
                out["id"] = node["id"][:64]
            return out

        if node_type == "condition":
            counter["conditions"] += 1
            field_key = node.get("field")
            fdef = F.FIELDS_BY_KEY.get(field_key) if isinstance(field_key, str) else None
            if fdef is None:
                err(path, f"unknown field '{field_key}'")
                return None
            op_key = node.get("operator")
            op = F.get_operator(fdef.data_type, op_key) if isinstance(op_key, str) else None
            if op is None:
                err(path, f"operator '{op_key}' is not supported for {fdef.label}")
                return None
            try:
                value = _normalize_value(fdef, op, node.get("value"))
            except ValueError as exc:
                err(path, f"{fdef.label} {op.label.lower()}: {exc}")
                return None
            out = {"type": "condition", "field": fdef.key, "operator": op.key, "value": value}
            if isinstance(node.get("id"), str):
                out["id"] = node["id"][:64]
            return out

        err(path, "type must be 'group' or 'condition'")
        return None

    if not isinstance(raw, dict) or raw.get("type") != "group":
        raise ConditionValidationError([{"path": "root", "message": "conditions must be a group"}])

    result = walk(raw, "root", 1)
    if counter["conditions"] == 0 and not errors:
        err("root", "add at least one condition")
    if counter["conditions"] > MAX_CONDITIONS:
        err("root", f"a rule can contain at most {MAX_CONDITIONS} conditions")
    if errors or result is None:
        raise ConditionValidationError(errors)
    return result


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
@dataclass
class ConditionResult:
    path: str
    field: str
    field_label: str
    operator: str
    operator_label: str
    expected: Any
    actual: Any
    passed: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "field": self.field,
            "field_label": self.field_label,
            "operator": self.operator,
            "operator_label": self.operator_label,
            "expected": self.expected,
            "actual": _jsonable(self.actual),
            "passed": self.passed,
        }


@dataclass
class EvaluationResult:
    matched: bool
    conditions: List[ConditionResult] = field(default_factory=list)

    @property
    def passed(self) -> List[ConditionResult]:
        return [c for c in self.conditions if c.passed]

    @property
    def failed(self) -> List[ConditionResult]:
        return [c for c in self.conditions if not c.passed]


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _period_delta(period: Dict[str, Any]) -> timedelta:
    return timedelta(**{period["unit"]: int(period["amount"])})


def _norm_text(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _compare(fdef: F.FieldDef, op: str, expected: Any, actual: Any, now: datetime) -> bool:
    dt = fdef.data_type

    if op == "is_empty":
        return actual is None or actual == "" or actual == []
    if op == "is_not_empty":
        return not (actual is None or actual == "" or actual == [])

    if dt == F.TEXT:
        a, e = _norm_text(actual), _norm_text(expected)
        return {
            "is": lambda: a == e,
            "is_not": lambda: a != e,
            "contains": lambda: e in a,
            "not_contains": lambda: e not in a,
            "starts_with": lambda: a.startswith(e),
            "ends_with": lambda: a.endswith(e),
        }[op]()

    if dt == F.NUMBER:
        if actual is None:
            return op == "neq"
        a, e = float(actual), float(expected)
        return {
            "eq": a == e,
            "neq": a != e,
            "gt": a > e,
            "lt": a < e,
            "gte": a >= e,
            "lte": a <= e,
        }[op]

    if dt == F.SELECT:
        a = _norm_text(actual)
        if op == "is":
            return actual is not None and a == _norm_text(expected)
        if op == "is_not":
            return a != _norm_text(expected)
        values = {_norm_text(v) for v in expected}
        if op == "in":
            return actual is not None and a in values
        if op == "not_in":
            return a not in values

    if dt == F.BOOLEAN:
        return bool(actual) if op == "is_true" else not bool(actual)

    if dt == F.TAGS:
        have = {_norm_text(t) for t in (actual or [])}
        want = {_norm_text(t) for t in expected}
        if op == "contains_any":
            return bool(have & want)
        if op == "contains_all":
            return want.issubset(have)
        if op == "not_contains":
            return not (have & want)

    if dt == F.DATE:
        if actual is None:
            return False
        a_dt = _as_aware(actual) if isinstance(actual, datetime) else datetime.combine(
            actual, time.min, tzinfo=timezone.utc
        )
        a_day = a_dt.astimezone(timezone.utc).date()
        if op == "is":
            return a_day == date.fromisoformat(expected)
        if op == "before":
            return a_day < date.fromisoformat(expected)
        if op == "after":
            return a_day > date.fromisoformat(expected)
        if op == "between":
            return date.fromisoformat(expected[0]) <= a_day <= date.fromisoformat(expected[1])
        if op == "within_last":
            return now - _period_delta(expected) <= a_dt <= now
        if op == "older_than":
            return a_dt < now - _period_delta(expected)

    return False


class ConditionEvaluator:
    """Evaluates a validated condition tree against a ticket context dict."""

    def __init__(self, now: Optional[datetime] = None):
        self.now = _as_aware(now) if now else datetime.now(timezone.utc)

    def evaluate(self, tree: Dict[str, Any], context: Dict[str, Any]) -> EvaluationResult:
        results: List[ConditionResult] = []
        matched = self._eval_node(tree, context, "root", results)
        return EvaluationResult(matched=matched, conditions=results)

    def _eval_node(
        self, node: Dict[str, Any], ctx: Dict[str, Any], path: str, out: List[ConditionResult]
    ) -> bool:
        if node.get("type") == "group":
            # Evaluate every child (no short-circuit) so the trace explains each condition.
            outcomes = [
                self._eval_node(child, ctx, f"{path}.{i}", out)
                for i, child in enumerate(node.get("children", []))
            ]
            if not outcomes:
                return False
            return all(outcomes) if node.get("logic") == "AND" else any(outcomes)

        fdef = F.FIELDS_BY_KEY.get(node.get("field", ""))
        op = F.get_operator(fdef.data_type, node.get("operator", "")) if fdef else None
        if fdef is None or op is None:
            # Stored data that no longer validates never matches.
            out.append(
                ConditionResult(path, str(node.get("field")), str(node.get("field")),
                                str(node.get("operator")), str(node.get("operator")),
                                node.get("value"), None, False)
            )
            return False

        actual = ctx.get(fdef.key)
        try:
            passed = _compare(fdef, op.key, node.get("value"), actual, self.now)
        except (KeyError, TypeError, ValueError):
            passed = False
        out.append(
            ConditionResult(path, fdef.key, fdef.label, op.key, op.label, node.get("value"), actual, passed)
        )
        return passed


def summarize_tree(tree: Dict[str, Any], labels: Optional[Dict[str, Dict[Any, str]]] = None) -> str:
    """Human readable one-line summary, e.g. ``(Priority is High AND Category is X) OR …``."""
    labels = labels or {}

    def fmt_value(field_key: str, value: Any) -> str:
        mapping = labels.get(field_key, {})
        if isinstance(value, list):
            return ", ".join(mapping.get(v, str(v)) for v in value)
        if isinstance(value, dict):
            return f"{value.get('amount')} {value.get('unit')}"
        return mapping.get(value, str(value))

    def walk(node: Dict[str, Any], top: bool) -> str:
        if node.get("type") == "group":
            parts = [walk(c, False) for c in node.get("children", [])]
            joined = f" {node.get('logic', 'AND')} ".join(parts)
            return joined if top or len(parts) == 1 else f"({joined})"
        fdef = F.FIELDS_BY_KEY.get(node.get("field", ""))
        op = F.get_operator(fdef.data_type, node.get("operator", "")) if fdef else None
        if not fdef or not op:
            return "invalid condition"
        if op.value_shape == F.V_NONE:
            return f"{fdef.label} {op.label.lower()}"
        return f"{fdef.label} {op.label.lower()} {fmt_value(fdef.key, node.get('value'))}"

    return walk(tree, True)
