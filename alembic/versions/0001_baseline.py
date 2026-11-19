"""Baseline: companies, users, email_accounts.

These tables pre-date Alembic (they were created by ``create_all`` at startup),
so every step is guarded and the migration is a no-op on existing databases.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def _has(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if not _has("companies"):
        op.create_table(
            "companies",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("currency", sa.String(10), nullable=False, server_default="USD"),
            sa.Column("timezone", sa.String(50), nullable=False, server_default="UTC"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_companies_id", "companies", ["id"])

    if not _has("users"):
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="SET NULL")),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("hashed_password", sa.String(255), nullable=False),
            sa.Column("full_name", sa.String(255)),
            sa.Column("role", sa.String(50), nullable=False, server_default="admin"),
            sa.Column("department", sa.String(100)),
            sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("is_superuser", sa.Boolean, nullable=False, server_default=sa.false()),
            sa.Column("invite_token", sa.String(255)),
            sa.Column("invite_expires_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_users_id", "users", ["id"])
        op.create_index("ix_users_email", "users", ["email"], unique=True)
        op.create_index("ix_users_company_id", "users", ["company_id"])
        op.create_index("ix_users_invite_token", "users", ["invite_token"], unique=True)

    if not _has("email_accounts"):
        op.create_table(
            "email_accounts",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("company_id", sa.Integer, sa.ForeignKey("companies.id", ondelete="SET NULL")),
            sa.Column("provider", sa.String(50), nullable=False, server_default="gmail"),
            sa.Column("account_name", sa.String(255), nullable=False),
            sa.Column("email", sa.String(255), nullable=False),
            sa.Column("password", sa.String(255)),
            sa.Column("enable_incoming", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("enable_outgoing", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("default_incoming", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("default_outgoing", sa.Boolean, nullable=False, server_default=sa.true()),
            sa.Column("smtp_host", sa.String(255), nullable=False, server_default="smtp.gmail.com"),
            sa.Column("smtp_port", sa.Integer, nullable=False, server_default="587"),
            sa.Column("smtp_encryption", sa.String(20), nullable=False, server_default="tls"),
            sa.Column("imap_host", sa.String(255), nullable=False, server_default="imap.gmail.com"),
            sa.Column("imap_port", sa.Integer, nullable=False, server_default="993"),
            sa.Column("status", sa.String(50), nullable=False, server_default="connected"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
        op.create_index("ix_email_accounts_id", "email_accounts", ["id"])
        op.create_index("ix_email_accounts_company_id", "email_accounts", ["company_id"])
        op.create_index("ix_email_accounts_email", "email_accounts", ["email"])


def downgrade() -> None:
    # The baseline is never dropped automatically: it holds pre-existing production data.
    pass
