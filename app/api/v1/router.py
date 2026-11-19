from fastapi import APIRouter
from app.api.v1.endpoints import (
    assignment_rules,
    auth,
    email_accounts,
    health,
    kb,
    setup,
    storage,
    teams,
    tickets,
    users,
)

api_router = APIRouter()

# Health check endpoint
api_router.include_router(
    health.router,
    prefix="",
    tags=["Health"],
)

# Authentication endpoints
api_router.include_router(
    auth.router,
    prefix="/auth",
    tags=["Authentication"],
)

# Initial Workspace & Super Admin Setup endpoints
api_router.include_router(
    setup.router,
    prefix="/setup",
    tags=["Setup & Onboarding"],
)

# Users & Agent Invitations
api_router.include_router(
    users.router,
    prefix="/users",
    tags=["Users & Invitations"],
)

# Email Accounts Configuration (GMail & SMTP)
api_router.include_router(
    email_accounts.router,
    prefix="/email-accounts",
    tags=["Email Accounts"],
)

# Cloud Storage & S3
api_router.include_router(
    storage.router,
    prefix="/storage",
    tags=["Cloud Storage & S3"],
)

# Knowledge Base & Articles (Cross-Role Persistence)
api_router.include_router(
    kb.router,
    prefix="/kb",
    tags=["Knowledge Base"],
)

# Tickets (create/update fire assignment rules)
api_router.include_router(
    tickets.router,
    prefix="/tickets",
    tags=["Tickets"],
)

# Support Teams
api_router.include_router(
    teams.router,
    prefix="/teams",
    tags=["Teams"],
)

# Assignment Rules (admin only)
api_router.include_router(
    assignment_rules.router,
    prefix="/assignment-rules",
    tags=["Assignment Rules"],
)
