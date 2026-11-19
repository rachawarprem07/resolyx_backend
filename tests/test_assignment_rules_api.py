"""HTTP-level tests for /api/v1/assignment-rules, /tickets and /teams."""

from typing import Any, Dict

import pytest
from sqlalchemy import func, select

from app.models.assignment_rule import AssignmentRuleChangeLog, AssignmentRuleExecution
from app.models.ticket import Ticket
from tests.conftest import auth, cond, group

BASE = "/api/v1/assignment-rules"


def rule_body(**kw) -> Dict[str, Any]:
    body = {
        "name": "High priority to Alice",
        "description": "Escalations",
        "priority": 10,
        "triggers": ["ticket_created"],
        "conditions": group("AND", cond("priority", "is", "High")),
        "assignment_type": "agent",
        "assignment_config": {"agent_id": None},
    }
    body.update(kw)
    return body


@pytest.fixture()
def h(admin):
    return auth(admin)


@pytest.fixture()
def created(client, h, agents):
    r = client.post(BASE, json=rule_body(assignment_config={"agent_id": agents[0].id}), headers=h)
    assert r.status_code == 201, r.text
    return r.json()


# ----------------------------------------------------------------- auth
def test_requires_authentication(client):
    assert client.get(BASE).status_code == 401
    assert client.get(BASE, headers={"Authorization": "Bearer garbage"}).status_code == 401


def test_requires_admin(client, agents, customer):
    assert client.get(BASE, headers=auth(agents[0])).status_code == 403
    assert client.get(BASE, headers=auth(customer)).status_code == 403


# ----------------------------------------------------------------- CRUD
def test_create_and_get(client, h, created, agents, db):
    assert created["assigned_to_label"] == "Alice Agent"
    assert created["conditions_summary"] == "Priority is High"
    assert created["condition_count"] == 1
    r = client.get(f"{BASE}/{created['id']}", headers=h)
    assert r.status_code == 200 and r.json()["name"] == created["name"]
    assert db.scalar(select(func.count(AssignmentRuleChangeLog.id))) == 1


def test_update(client, h, created, team):
    body = rule_body(name="Renamed", assignment_type="team", assignment_config={"team_id": team.id},
                     triggers=["ticket_created", "ticket_updated"])
    r = client.put(f"{BASE}/{created['id']}", json=body, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "Renamed" and data["assigned_to_label"] == "Tier 1"
    assert data["assignment_config"]["team_id"] == team.id
    assert "agent_id" not in data["assignment_config"]  # stale keys from the old type are dropped
    assert data["triggers"] == ["ticket_created", "ticket_updated"]


def test_duplicate_name_conflicts(client, h, created, agents):
    r = client.post(BASE, json=rule_body(name=created["name"].upper(),
                                         assignment_config={"agent_id": agents[0].id}), headers=h)
    assert r.status_code == 409


def test_status_toggle_and_stats(client, h, created):
    r = client.patch(f"{BASE}/{created['id']}/status", json={"is_active": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    stats = client.get(BASE, headers=h).json()["stats"]
    assert stats == {"total_rules": 1, "active_rules": 0, "inactive_rules": 1,
                     "tickets_assigned_automatically": 0}


def test_duplicate_creates_inactive_copy(client, h, created):
    r = client.post(f"{BASE}/{created['id']}/duplicate", headers=h)
    assert r.status_code == 201
    assert r.json()["name"] == f"Copy of {created['name']}" and r.json()["is_active"] is False
    r2 = client.post(f"{BASE}/{created['id']}/duplicate", headers=h)
    assert r2.json()["name"] == f"Copy of {created['name']} (2)"


def test_delete(client, h, created):
    assert client.delete(f"{BASE}/{created['id']}", headers=h).status_code == 204
    assert client.get(f"{BASE}/{created['id']}", headers=h).status_code == 404


def test_list_search_filter_sort(client, h, agents, team):
    client.post(BASE, json=rule_body(name="Zeta", priority=1, assignment_config={"agent_id": agents[0].id}), headers=h)
    client.post(BASE, json=rule_body(name="Alpha", priority=50, assignment_type="team",
                                     assignment_config={"team_id": team.id}, is_active=False), headers=h)
    names = lambda q: [r["name"] for r in client.get(f"{BASE}{q}", headers=h).json()["items"]]  # noqa: E731
    assert names("") == ["Zeta", "Alpha"]
    assert names("?sort=priority_desc") == ["Alpha", "Zeta"]
    assert names("?status=inactive") == ["Alpha"]
    assert names("?assignment_type=agent") == ["Zeta"]
    assert names("?search=alp") == ["Alpha"]


# ------------------------------------------------------------- validation
def test_invalid_conditions_return_structured_errors(client, h, agents):
    body = rule_body(conditions=group("AND", cond("priority", "contains", "x"), cond("bogus", "is", 1)),
                     assignment_config={"agent_id": agents[0].id})
    r = client.post(BASE, json=body, headers=h)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert {e["path"] for e in detail["errors"]} == {"root.0", "root.1"}


@pytest.mark.parametrize("body_kw", [
    {"triggers": []},
    {"name": "   "},
    {"assignment_type": "agent", "assignment_config": {}},
    {"assignment_type": "round_robin", "assignment_config": {}},
    {"assignment_type": "magic", "assignment_config": {"agent_id": 1}},
    {"unexpected": True},
])
def test_schema_validation(client, h, body_kw):
    assert client.post(BASE, json=rule_body(**body_kw), headers=h).status_code == 422


def test_rejects_inactive_unknown_and_foreign_agents(client, h, agents, make_user, db):
    from app.models.company import Company

    agents[1].is_active = False
    db.commit()
    other = Company(name="Other")
    db.add(other)
    db.commit()
    foreign = make_user("Foreign Agent", company_id=other.id)
    for agent_id in (agents[1].id, 99999, foreign.id):
        r = client.post(BASE, json=rule_body(assignment_config={"agent_id": agent_id}), headers=h)
        assert r.status_code == 422, agent_id
        assert r.json()["detail"]["errors"][0]["path"] == "assignment_config.agent_id"


def test_tenant_isolation(client, created, make_user, db):
    from app.models.company import Company

    other = Company(name="Other")
    db.add(other)
    db.commit()
    other_admin = make_user("Olga", role="admin", company_id=other.id)
    assert client.get(f"{BASE}/{created['id']}", headers=auth(other_admin)).status_code == 404
    assert client.get(BASE, headers=auth(other_admin)).json()["total"] == 0


# ------------------------------------------------------------ test / preview
def test_test_rule_with_sample_ticket(client, h, created):
    r = client.post(f"{BASE}/{created['id']}/test",
                    json={"sample_ticket": {"priority": "High", "subject": "x"}}, headers=h)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["matched"] is True and data["status"] == "assigned"
    assert data["expected_assignment"]["agent_name"] == "Alice Agent"
    assert len(data["passed_conditions"]) == 1 and data["failed_conditions"] == []

    r = client.post(f"{BASE}/{created['id']}/test", json={"sample_ticket": {"priority": "Low"}}, headers=h)
    data = r.json()
    assert data["matched"] is False and data["failed_conditions"][0]["actual"] == "Low"


def test_test_rule_on_existing_ticket_does_not_modify_it(client, h, admin, agents, db):
    t = client.post("/api/v1/tickets", json={"subject": "s", "priority": "High"}, headers=h).json()
    r = client.post(BASE, json=rule_body(assignment_config={"agent_id": agents[0].id}), headers=h).json()
    res = client.post(f"{BASE}/{r['id']}/test", json={"ticket_id": t["ticket_number"]}, headers=h).json()
    assert res["matched"] and res["would_change_assignment"] is True
    db.expire_all()
    ticket = db.get(Ticket, t["id"])
    assert ticket.assigned_agent_id is None
    assert db.scalar(select(func.count(AssignmentRuleExecution.id))) == 0


def test_test_rule_with_unsaved_draft(client, h, created, agents):
    draft = rule_body(conditions=group("AND", cond("priority", "is", "Low")),
                      assignment_config={"agent_id": agents[2].id})
    res = client.post(f"{BASE}/{created['id']}/test",
                      json={"sample_ticket": {"priority": "Low"}, "rule": draft}, headers=h).json()
    assert res["matched"] and res["expected_assignment"]["agent_id"] == agents[2].id


def test_preview_reports_winning_rule(client, h, agents):
    client.post(BASE, json=rule_body(name="Low prio", priority=50, conditions=group("AND", cond("status", "is", "Open")),
                                     assignment_config={"agent_id": agents[1].id}), headers=h)
    client.post(BASE, json=rule_body(name="High", priority=1, assignment_config={"agent_id": agents[0].id}), headers=h)
    res = client.post(f"{BASE}/preview", json={"sample_ticket": {"priority": "High"}}, headers=h).json()
    assert res["winning_rule_name"] == "High"
    assert res["final_assignment"]["agent_id"] == agents[0].id
    assert res["rules_considered"] == 1  # stopped after the first match

    res = client.post(f"{BASE}/preview", json={"sample_ticket": {"priority": "Low"}}, headers=h).json()
    assert res["winning_rule_name"] == "Low prio" and res["rules_considered"] == 2


def test_preview_requires_exactly_one_target(client, h):
    assert client.post(f"{BASE}/preview", json={}, headers=h).status_code == 422
    assert client.post(f"{BASE}/preview", json={"ticket_id": "TICK-1", "sample_ticket": {}}, headers=h).status_code == 422
    assert client.post(f"{BASE}/preview", json={"ticket_id": "TICK-9999"}, headers=h).status_code == 404


# ------------------------------------------------------------------ history
def test_execution_history_and_filters(client, h, created, agents):
    t1 = client.post("/api/v1/tickets", json={"subject": "a", "priority": "High"}, headers=h).json()
    client.post("/api/v1/tickets", json={"subject": "b", "priority": "Low"}, headers=h)
    assert t1["assigned_agent_name"] == "Alice Agent" and t1["assignment_source"] == "rule"

    res = client.get(f"{BASE}/{created['id']}/executions", headers=h).json()
    assert res["total"] == 1
    item = res["items"][0]
    assert item["ticket_number"] == t1["ticket_number"]
    assert item["assigned_agent_name"] == "Alice Agent" and item["previous_agent_name"] is None
    assert item["execution_status"] == "assigned" and item["matched_conditions"]

    q = lambda s: client.get(f"{BASE}/executions{s}", headers=h).json()["total"]  # noqa: E731
    assert q("") == 1
    assert q(f"?agent_id={agents[0].id}") == 1
    assert q(f"?agent_id={agents[1].id}") == 0
    assert q("?status=failed") == 0
    assert q(f"?ticket_id={t1['ticket_number']}") == 1
    assert q("?date_from=2099-01-01T00:00:00Z") == 0
    assert client.get(BASE, headers=h).json()["stats"]["tickets_assigned_automatically"] == 1


# ------------------------------------------------------------------ lookups
def test_fields_metadata(client, h, agents, team):
    data = client.get(f"{BASE}/fields", headers=h).json()
    by_key = {f["key"]: f for f in data["fields"]}
    assert by_key["priority"]["data_type"] == "select"
    assert {o["label"] for o in by_key["assigned_agent"]["options"]} >= {"Alice Agent", "Ada Admin"}
    assert by_key["assigned_team"]["options"] == [{"value": team.id, "label": "Tier 1"}]
    assert {o["key"] for o in data["operators"]["tags"]} == {"contains_any", "contains_all", "not_contains"}


def test_agents_and_availability(client, h, agents, team, customer):
    data = client.get(f"{BASE}/agents", headers=h).json()
    names = {a["name"] for a in data}
    assert "Carl Customer" not in names and "Alice Agent" in names
    r = client.patch(f"{BASE}/agents/{agents[0].id}/availability", json={"is_available": False}, headers=h)
    assert r.status_code == 200 and r.json()["is_available"] is False
    teams = client.get(f"{BASE}/teams", headers=h).json()
    assert teams[0]["member_count"] == 3


# ------------------------------------------------------------------ tickets
def test_customer_ticket_scope(client, customer, admin, agents):
    hc = auth(customer)
    t = client.post("/api/v1/tickets", json={"subject": "Mine", "customer_email": "spoof@x.com"}, headers=hc)
    assert t.status_code == 201 and t.json()["customer_email"] == customer.email
    client.post("/api/v1/tickets", json={"subject": "Other"}, headers=auth(admin))
    assert [x["subject"] for x in client.get("/api/v1/tickets", headers=hc).json()] == ["Mine"]
    num = t.json()["ticket_number"]
    assert client.patch(f"/api/v1/tickets/{num}", json={"priority": "Urgent"}, headers=hc).status_code == 403
    assert client.post("/api/v1/tickets", json={"subject": "x", "assigned_agent_id": agents[0].id},
                       headers=hc).status_code == 403


def test_manual_ticket_assignment_via_api(client, h, agents):
    t = client.post("/api/v1/tickets", json={"subject": "x"}, headers=h).json()
    r = client.patch(f"/api/v1/tickets/{t['ticket_number']}", json={"assigned_agent_id": agents[1].id}, headers=h)
    assert r.json()["assignment_source"] == "manual" and r.json()["assigned_agent_id"] == agents[1].id
    r = client.patch(f"/api/v1/tickets/{t['ticket_number']}", json={"assigned_agent_id": 0}, headers=h)
    assert r.json()["assigned_agent_id"] is None and r.json()["assignment_source"] is None


def test_team_crud(client, h, agents):
    body = {"name": "Billing", "member_ids": [agents[0].id, agents[1].id], "lead_user_id": agents[0].id}
    r = client.post("/api/v1/teams", json=body, headers=h)
    assert r.status_code == 201, r.text
    team = r.json()
    assert [m["name"] for m in team["members"]] == ["Alice Agent", "Bob Agent"]
    r = client.put(f"/api/v1/teams/{team['id']}", json={**body, "member_ids": [agents[2].id]}, headers=h)
    assert [m["id"] for m in r.json()["members"]] == [agents[2].id]
    assert client.post("/api/v1/teams", json=body, headers=h).status_code == 409
    assert client.post("/api/v1/teams", json={**body, "name": "X", "member_ids": [9999]}, headers=h).status_code == 400
    assert client.delete(f"/api/v1/teams/{team['id']}", headers=h).status_code == 204
    assert client.post("/api/v1/teams", json={**body, "name": "Y"}, headers=auth(agents[0])).status_code == 403
