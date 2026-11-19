# Assignment Rules API

Automated ticket assignment for Resolyx. Administrators define rules; the backend
evaluates them whenever a ticket is created or updated and assigns the ticket to an
agent and/or team.

Interactive docs: `http://localhost:8000/api/v1/docs` (tags **Assignment Rules**, **Tickets**, **Teams**).

---

## 1. Authentication & authorization

Every endpoint requires `Authorization: Bearer <token>` (the token returned by
`POST /api/v1/auth/login`).

| Area | Who |
| --- | --- |
| `/assignment-rules/**` | Administrators only (`admin`, `super_admin`, or `is_superuser`) |
| `GET /teams` | Support staff (admin, team lead, agent) |
| `POST/PUT/DELETE /teams` | Administrators |
| `/tickets` | Any signed-in user. Customers only see and edit their own tickets (`status`, `description`, `meta`) |

Every query is scoped to the caller's `company_id`. Rules, agents, teams, tickets
and history from another workspace are invisible and are rejected as targets.

Errors use FastAPI's usual shape `{"detail": ...}`: `401` (missing or invalid token),
`403` (role), `404`, `409` (duplicate name, team in use), `422` (validation).
Rule validation failures return structured details:

```json
{ "detail": { "message": "Some conditions are invalid.",
              "errors": [ { "path": "root.1.0", "message": "Priority contains: operator ... not supported" } ] } }
```

`path` points at a node of the condition tree (`root.<childIndex>.<childIndex>…`) or
at a config key (`assignment_config.agent_id`), so the UI can highlight the offending
row.

---

## 2. The condition tree

Conditions are stored as structured JSON in `assignment_rules.condition_json`. They
are interpreted in Python by `ConditionEvaluator` and are never turned into SQL or code.

```json
{
  "type": "group", "logic": "OR",
  "children": [
    { "type": "group", "logic": "AND", "children": [
      { "type": "condition", "field": "priority", "operator": "is", "value": "High" },
      { "type": "condition", "field": "category", "operator": "is", "value": "Technical Support" } ] },
    { "type": "group", "logic": "AND", "children": [
      { "type": "condition", "field": "priority", "operator": "is", "value": "Urgent" },
      { "type": "condition", "field": "customer_type", "operator": "is", "value": "Enterprise" } ] }
  ]
}
```

Limits: groups nest at most **4** levels (counting the root), each group holds at most
**25** items, a rule holds at most **50** conditions, and text values are at most 500
characters. Empty groups are rejected. A rule needs at least one condition.

### Fields

`GET /assignment-rules/fields` returns these fields with live options (agents, teams,
categories in use), so the frontend never hard-codes them.

| Group | Field key | Type |
| --- | --- | --- |
| Ticket | `subject`, `description`, `ticket_id`, `subcategory` | text |
| Ticket | `status`, `priority`, `category`, `ticket_type`, `source` | select |
| Ticket | `created_at`, `updated_at` | date |
| Ticket | `message_count` (messages in the conversation) | number |
| Customer | `customer_name`, `customer_email`, `customer_company` | text |
| Customer | `customer_type` | select |
| Assignment | `assigned_agent`, `assigned_team` (values are ids) | select |
| Assignment | `is_unassigned` | boolean |
| Other | `tags` | tags |
| Other | `sla_status` (derived from `due_date` while the ticket is open: Breached / At Risk ≤1h / On Track) | select |
| Other | `due_date` | date |

### Operators and value shapes

| Type | Operators | `value` |
| --- | --- | --- |
| text | `is`, `is_not`, `contains`, `not_contains`, `starts_with`, `ends_with` | string |
| text | `is_empty`, `is_not_empty` | omitted |
| number | `eq`, `neq`, `gt`, `lt`, `gte`, `lte` | number |
| select | `is`, `is_not` | string (id for agent/team) |
| select | `in` (Is Any Of), `not_in` (Is None Of) | non-empty list |
| select | `is_empty`, `is_not_empty` | omitted |
| date | `is`, `before`, `after` | `"YYYY-MM-DD"` |
| date | `between` | `["YYYY-MM-DD", "YYYY-MM-DD"]` (inclusive) |
| date | `within_last`, `older_than` | `{"amount": 1-3650, "unit": "minutes"\|"hours"\|"days"\|"weeks"}` |
| date | `is_empty`, `is_not_empty` | omitted |
| boolean | `is_true`, `is_false` | omitted |
| tags | `contains_any`, `contains_all`, `not_contains` | non-empty list |

Text, select and tag comparisons ignore case and surrounding whitespace. Calendar-day
date comparisons use UTC. A missing date never matches `is`, `before`, `after`,
`between`, `within_last` or `older_than`.

---

## 3. Execution semantics

When a ticket is **created** (`POST /tickets`) or **updated** (`PATCH /tickets/{ref}`):

1. Load the workspace's **active** rules whose `triggers` include the event.
2. Sort by `priority` ascending (**lower number runs first**), then by id.
3. Evaluate each rule's condition tree against the ticket.
4. On the first match, plan and apply its action, then **stop**. Evaluation stops on
   that rule even if its action was skipped or failed.
5. Write an `AssignmentRuleExecution` row for every rule that matched (`assigned`,
   `no_change`, `skipped` or `failed`, with a reason and the condition trace). Rules
   that did not match are not recorded.

The engine runs inside a **SAVEPOINT** of the request transaction. If it raises, only
the assignment is rolled back: the ticket is still saved and the error is logged.

### What counts as an update

`ticket_updated` fires only when a tracked field changes: subject, description,
status, priority, category, subcategory, type, source, customer fields, tags, SLA
status or due date. Changes that touch only assignment fields or only `meta`
(conversation, CSAT) do **not** fire it.

### Loop prevention

* The engine writes assignment columns directly and never calls the ticket service, so
  its own writes cannot raise a new update event.
* Assignment-only edits are not "updates" (see above).
* A context-variable guard makes a re-entrant `run_for_ticket` call a no-op.
* If the computed target equals the current assignment, the run is recorded as
  `no_change` and nothing is written. The round-robin pointer does not advance.

### Existing assignments

| Ticket state before the run | Rule setting | Result |
| --- | --- | --- |
| Assigned **manually** (by a person, or legacy data with no source) | `overwrite_existing_assignment = false` (default) | `skipped` |
| Assigned manually | `overwrite_existing_assignment = true` | reassigned |
| Assigned by an earlier **rule** run | `reassign_existing = false` (default) | `skipped` |
| Assigned by an earlier rule run | `reassign_existing = true` | reassigned |
| Unassigned | — | assigned |

Setting `assigned_agent_id` / `assigned_team_id` through `PATCH /tickets/{ref}`
records a manual assignment (`assignment_source = "manual"`). Sending `0` clears it.

### Continue to Next Rule

With `continue_to_next_rule = true`, lower-priority rules keep being evaluated after
this one matches. To prevent conflicting assignments, a later rule may only fill a slot
(**agent** or **team**) that no earlier rule in the **same run** has already set. If a
later rule would overwrite a slot, it is recorded as `skipped` with a conflict reason.
Example: rule 1 assigns team *Tier 2* and continues, then rule 2 assigns agent *Bob*,
so the ticket gets both. If rule 2 had also targeted a team, it would be skipped.
Evaluation stops at the first matching rule that does not have the flag.

### Assignment types

| `assignment_type` | `assignment_config` | Behaviour |
| --- | --- | --- |
| `agent` | `{"agent_id": 12}` | Assigns that agent |
| `team` | `{"team_id": 3}` | Sets the team; the agent is left unchanged |
| `round_robin` | `{"team_id": 3}` and/or `{"agent_ids": [..]}` | Next eligible agent after the last one assigned, in ascending id order (wraps). If `team_id` is set, the team is also assigned |
| `least_assigned` | same as round robin | Eligible agent with the fewest **open** tickets (status not Resolved/Closed). Ties go to the lowest user id |

**Eligibility.** Inactive or deleted agents are **always** excluded. Agents marked
unavailable (`PATCH /assignment-rules/agents/{id}/availability`) are excluded while
`skip_unavailable_agents = true` (the default). If nobody is eligible, the run is
recorded as `failed` with the excluded agents and the reasons.

**Concurrency.** Each round-robin or least-assigned rule has an `assignment_rotations`
row. Selection takes `SELECT … FOR UPDATE` on it, so concurrent ticket creations for
the same rule are serialized: no two of them read the same rotation pointer or workload
snapshot. The ticket row is also locked for the duration of the run. This is verified
by `tests/test_concurrency_postgres.py`: 12 parallel creations split exactly 3/3/3/3
across 4 agents, and without the lock they all go to one agent.

---

## 4. Endpoints

Base path: `/api/v1/assignment-rules`.

| Method & path | Purpose |
| --- | --- |
| `GET ""` | List rules + summary stats. Query: `search`, `status=active\|inactive`, `assignment_type`, `sort=priority_asc\|priority_desc\|updated_desc\|name_asc` |
| `POST ""` | Create a rule (201) |
| `GET /{id}` | Get a rule |
| `PUT /{id}` | Replace a rule |
| `DELETE /{id}` | Delete a rule (204). History rows are kept with `rule_name` preserved |
| `PATCH /{id}/status` | `{"is_active": bool}` |
| `POST /{id}/duplicate` | Copy as "Copy of …" (created **inactive**) |
| `POST /{id}/test` | Dry-run one rule. Body: `{"ticket_id": "TICK-1001"}` **or** `{"sample_ticket": {...}}`, plus optional `"rule": {...}` to test unsaved edits |
| `GET /{id}/executions` | History for one rule (`limit`, `offset`) |
| `POST /preview` | Dry-run the whole active rule set for a ticket. Optional `trigger`, or `rule` to dry-run a single unsaved draft |
| `GET /executions` | History for all rules. Query: `rule_id`, `ticket_id` (partial number), `agent_id` (previous or new assignee), `status`, `date_from`, `date_to`, `limit`, `offset` |
| `GET /agents` | Active agents with availability, open-ticket count, team ids |
| `PATCH /agents/{id}/availability` | `{"is_available": bool}` |
| `GET /teams` | Active teams with members |
| `GET /fields` | Field and operator metadata for the condition builder |

Test and preview **never write**: they evaluate with `dry_run=True`, never create
rotation rows, and always roll back the session.

### Rule body (POST / PUT)

```json
{
  "name": "Enterprise urgent → Tier 2 (round robin)",
  "description": "Enterprise customers with urgent tickets",
  "is_active": true,
  "priority": 5,
  "triggers": ["ticket_created", "ticket_updated"],
  "conditions": { "type": "group", "logic": "AND", "children": [
      { "type": "condition", "field": "priority", "operator": "in", "value": ["High", "Urgent"] },
      { "type": "condition", "field": "customer_type", "operator": "is", "value": "Enterprise" } ] },
  "assignment_type": "round_robin",
  "assignment_config": { "team_id": 2 },
  "overwrite_existing_assignment": false,
  "reassign_existing": false,
  "skip_unavailable_agents": true,
  "continue_to_next_rule": false
}
```

Rule names are unique per workspace (case-insensitive). Unknown keys are rejected.

### Test response (abridged)

```json
{
  "rule_id": 4, "rule_name": "Enterprise urgent → Tier 2", "priority": 5,
  "ticket_ref": "TICK-1042",
  "matched": true, "status": "assigned",
  "reason": "Assigned to agent Bob Agent in team Tier 2 via round robin (round robin, position 2 of 3 eligible agents).",
  "passed_conditions": [ { "path": "root.0", "field_label": "Priority", "operator_label": "Is Any Of",
                           "expected": ["High","Urgent"], "actual": "Urgent", "passed": true } ],
  "failed_conditions": [],
  "expected_assignment": { "assignment_type": "round_robin", "agent_id": 7, "agent_name": "Bob Agent",
                           "team_id": 2, "team_name": "Tier 2" },
  "would_change_assignment": true
}
```

### Tickets & teams

| Method & path | Notes |
| --- | --- |
| `GET /api/v1/tickets` | Staff: the whole workspace. Customers: their own tickets |
| `POST /api/v1/tickets` | Fires `ticket_created`. Customers cannot set assignees, and their email is forced to their own |
| `GET /api/v1/tickets/{ref}` | `ref` = `TICK-1001` or numeric id |
| `PATCH /api/v1/tickets/{ref}` | Partial update; fires `ticket_updated` when a tracked field changes |
| `GET/POST /api/v1/teams`, `PUT/DELETE /api/v1/teams/{id}` | `member_ids` must be agents in the workspace. A team used by a rule cannot be deleted (409) |

---

## 5. Data model

| Table | Purpose |
| --- | --- |
| `assignment_rules` | Rule definition. Unique `(company_id, name)`, index `(company_id, is_active, priority)` |
| `assignment_rule_executions` | Audit of each matched rule per ticket: previous and new assignee, status, reason, condition trace. Kept when a rule is deleted (`rule_id` set to NULL, `rule_name` preserved) |
| `assignment_rotations` | One row per pool rule (unique `rule_id`): last agent, index. Also the selection lock |
| `agent_assignment_profiles` | Per-agent availability (a missing row means available) |
| `assignment_rule_change_logs` | Audit trail of rule create/update/activate/deactivate/duplicate/delete with actor and snapshot |
| `tickets` | Tickets. `assignment_source` = `manual` \| `rule` \| NULL, `assigned_by_rule_id` |
| `teams`, `team_members` | Teams with real user membership |

Migrations: `alembic upgrade head` (revisions `0001_baseline`, `0002_assignment_rules`).
Both are idempotent with the startup `create_all`.

## 6. Tests

```bash
cd backend
pytest                                   # unit + API tests on in-memory SQLite
TEST_DATABASE_URL=postgresql+psycopg2://user:pw@localhost:5432/resolyx_test pytest tests/test_concurrency_postgres.py
```

The concurrency tests **drop and recreate all tables** in `TEST_DATABASE_URL`, so
point them at a throwaway database.
