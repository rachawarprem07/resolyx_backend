"""Teams, tickets and the Assignment Rules module.

Guarded with has_table checks because application startup also runs
``create_all``; running this after the app has started is a no-op.

Revision ID: 0002_assignment_rules
Revises: 0001_baseline
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_assignment_rules"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

NOW = sa.func.now()


def _has(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def _ts(name: str) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=False, server_default=NOW)


def upgrade() -> None:
    if not _has("teams"):
        op.create_table(
            "teams",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("routing_strategy", sa.String(40), nullable=False, server_default="Round-Robin"),
            sa.Column("sla_target_hours", sa.Integer, nullable=False, server_default="4"),
            sa.Column("lead_user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            _ts("created_at"), _ts("updated_at"),
            sa.UniqueConstraint("company_id", "name", name="uq_teams_company_name"),
        )
        op.create_index("ix_teams_company_id", "teams", ["company_id"])
        op.create_index("ix_teams_is_active", "teams", ["is_active"])

    if not _has("team_members"):
        op.create_table(
            "team_members",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("team_id", sa.Integer, sa.ForeignKey("teams.id", ondelete="CASCADE"), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
            _ts("created_at"),
            sa.UniqueConstraint("team_id", "user_id", name="uq_team_members_team_user"),
        )
        op.create_index("ix_team_members_team_id", "team_members", ["team_id"])
        op.create_index("ix_team_members_user_id", "team_members", ["user_id"])

    if not _has("assignment_rules"):
        op.create_table(
            "assignment_rules",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("priority", sa.Integer, nullable=False, server_default="10"),
            sa.Column("triggers", sa.JSON, nullable=False),
            sa.Column("condition_json", sa.JSON, nullable=False),
            sa.Column("assignment_type", sa.String(30), nullable=False),
            sa.Column("assignment_config", sa.JSON, nullable=False),
            sa.Column("overwrite_existing_assignment", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("reassign_existing", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("skip_unavailable_agents", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("continue_to_next_rule", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("created_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("updated_by", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            _ts("created_at"), _ts("updated_at"),
            sa.UniqueConstraint("company_id", "name", name="uq_assignment_rules_company_name"),
        )
        op.create_index("ix_assignment_rules_company_id", "assignment_rules", ["company_id"])
        op.create_index("ix_assignment_rules_company_active_priority", "assignment_rules",
                        ["company_id", "is_active", "priority"])

    if not _has("tickets"):
        op.create_table(
            "tickets",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("ticket_number", sa.String(32)),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("subject", sa.String(500), nullable=False),
            sa.Column("description", sa.Text),
            sa.Column("status", sa.String(40), nullable=False, server_default="Open"),
            sa.Column("priority", sa.String(20), nullable=False, server_default="Medium"),
            sa.Column("category", sa.String(120)),
            sa.Column("subcategory", sa.String(120)),
            sa.Column("ticket_type", sa.String(60)),
            sa.Column("source", sa.String(60)),
            sa.Column("customer_name", sa.String(255)),
            sa.Column("customer_email", sa.String(255)),
            sa.Column("customer_company", sa.String(255)),
            sa.Column("customer_type", sa.String(60)),
            sa.Column("tags", sa.JSON, nullable=False),
            sa.Column("sla_status", sa.String(40)),
            sa.Column("due_date", sa.DateTime(timezone=True)),
            sa.Column("assigned_agent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("assigned_team_id", sa.Integer, sa.ForeignKey("teams.id", ondelete="SET NULL")),
            sa.Column("assignment_source", sa.String(20)),
            sa.Column("assigned_by_rule_id", sa.Integer, sa.ForeignKey("assignment_rules.id", ondelete="SET NULL")),
            sa.Column("created_by_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("meta", sa.JSON, nullable=False),
            _ts("created_at"), _ts("updated_at"),
        )
        op.create_index("ix_tickets_ticket_number", "tickets", ["ticket_number"], unique=True)
        op.create_index("ix_tickets_company_id", "tickets", ["company_id"])
        op.create_index("ix_tickets_customer_email", "tickets", ["customer_email"])
        op.create_index("ix_tickets_assigned_team_id", "tickets", ["assigned_team_id"])
        op.create_index("ix_tickets_company_status", "tickets", ["company_id", "status"])
        op.create_index("ix_tickets_agent_status", "tickets", ["assigned_agent_id", "status"])

    if not _has("assignment_rule_executions"):
        op.create_table(
            "assignment_rule_executions",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("rule_id", sa.Integer, sa.ForeignKey("assignment_rules.id", ondelete="SET NULL")),
            sa.Column("rule_name", sa.String(120), nullable=False),
            sa.Column("ticket_id", sa.Integer, sa.ForeignKey("tickets.id", ondelete="CASCADE"), nullable=False),
            sa.Column("trigger", sa.String(30), nullable=False),
            sa.Column("previous_agent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("previous_team_id", sa.Integer, sa.ForeignKey("teams.id", ondelete="SET NULL")),
            sa.Column("assigned_agent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("assigned_team_id", sa.Integer, sa.ForeignKey("teams.id", ondelete="SET NULL")),
            sa.Column("execution_status", sa.String(20), nullable=False),
            sa.Column("execution_reason", sa.Text, nullable=False),
            sa.Column("matched_conditions", sa.JSON, nullable=False),
            _ts("executed_at"),
        )
        op.create_index("ix_assignment_rule_executions_ticket_id", "assignment_rule_executions", ["ticket_id"])
        op.create_index("ix_assignment_rule_executions_assigned_agent_id", "assignment_rule_executions", ["assigned_agent_id"])
        op.create_index("ix_assignment_rule_executions_execution_status", "assignment_rule_executions", ["execution_status"])
        op.create_index("ix_assignment_exec_company_executed", "assignment_rule_executions", ["company_id", "executed_at"])
        op.create_index("ix_assignment_exec_rule_executed", "assignment_rule_executions", ["rule_id", "executed_at"])

    if not _has("assignment_rotations"):
        op.create_table(
            "assignment_rotations",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("rule_id", sa.Integer, sa.ForeignKey("assignment_rules.id", ondelete="CASCADE"),
                      nullable=False, unique=True),
            sa.Column("last_assigned_agent_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("rotation_index", sa.Integer, nullable=False, server_default="-1"),
            _ts("updated_at"),
        )

    if not _has("agent_assignment_profiles"):
        op.create_table(
            "agent_assignment_profiles",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True),
            sa.Column("is_available", sa.Boolean, nullable=False, server_default=sa.true()),
            _ts("updated_at"),
        )

    if not _has("assignment_rule_change_logs"):
        op.create_table(
            "assignment_rule_change_logs",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="CASCADE")),
            sa.Column("rule_id", sa.Integer, sa.ForeignKey("assignment_rules.id", ondelete="SET NULL")),
            sa.Column("action", sa.String(30), nullable=False),
            sa.Column("actor_id", sa.Integer, sa.ForeignKey("users.id", ondelete="SET NULL")),
            sa.Column("snapshot", sa.JSON, nullable=False),
            _ts("created_at"),
        )
        op.create_index("ix_assignment_rule_change_logs_rule_id", "assignment_rule_change_logs", ["rule_id"])


def downgrade() -> None:
    for table in (
        "assignment_rule_change_logs",
        "agent_assignment_profiles",
        "assignment_rotations",
        "assignment_rule_executions",
        "tickets",
        "assignment_rules",
        "team_members",
        "teams",
    ):
        if _has(table):
            op.drop_table(table)
