"""Organization and team management API routes.

Mounted at /v1/orgs in the main app.  All endpoints require
authentication (OAuth session or API key) and enforce role-based
access control via the rbac module.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from api.rbac import require_org_role, _current_user_id, _get_pool, ROLE_RANK
from db.multi_tenant import PLAN_LIMITS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/orgs", tags=["organizations"])

_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,38}[a-z0-9])?$")


class CreateOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    slug: str = Field(min_length=2, max_length=40)

class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str
    plan: str
    member_count: int = 0
    trajectory_count: int = 0
    created_at: str | None = None

class AddMemberRequest(BaseModel):
    email: str
    role: str = "member"

class MemberResponse(BaseModel):
    user_id: str
    email: str
    display_name: str
    role: str

class LeaderboardEntry(BaseModel):
    user_id: str
    display_name: str
    trajectories_contributed: int
    helpful_votes_received: int
    tokens_saved_estimate: int


@router.post("", response_model=OrgResponse)
async def create_org(payload: CreateOrgRequest, request: Request) -> OrgResponse:
    """Create a new organization. The caller becomes the owner."""
    if not _SLUG_RE.match(payload.slug):
        raise HTTPException(status_code=422, detail="Slug must be lowercase alphanumeric with hyphens")

    user_id = _current_user_id(request)
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        existing = await conn.fetchrow("SELECT id FROM organizations WHERE slug = $1", payload.slug)
        if existing:
            raise HTTPException(status_code=409, detail="Organization slug already taken")

        limits = PLAN_LIMITS["free"]
        row = await conn.fetchrow(
            """INSERT INTO organizations (name, slug, max_members, max_api_keys, max_trajectories)
               VALUES ($1, $2, $3, $4, $5) RETURNING id, created_at""",
            payload.name, payload.slug, limits["max_members"],
            limits["max_api_keys"], limits["max_trajectories"],
        )
        await conn.execute(
            """INSERT INTO org_memberships (org_id, user_id, role)
               VALUES ($1, $2, 'owner')""",
            row["id"], user_id,
        )
        await conn.execute(
            """INSERT INTO user_stats (user_id, org_id)
               VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING""",
            user_id, row["id"],
        )

    return OrgResponse(
        id=str(row["id"]), name=payload.name, slug=payload.slug,
        plan="free", member_count=1, created_at=str(row["created_at"]),
    )


@router.get("/{slug}", response_model=OrgResponse)
async def get_org(slug: str, request: Request) -> OrgResponse:
    """Get org details. Requires viewer role or above."""
    org, _membership = await require_org_role(request, slug, "viewer")
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        member_count = await conn.fetchval(
            "SELECT COUNT(*) FROM org_memberships WHERE org_id = $1", org["id"]
        )
        trajectory_count = await conn.fetchval(
            "SELECT COUNT(*) FROM trajectories WHERE org_id = $1", org["id"]
        )

    return OrgResponse(
        id=str(org["id"]), name=org["name"], slug=org["slug"],
        plan=org["plan"], member_count=member_count,
        trajectory_count=trajectory_count, created_at=str(org["created_at"]),
    )


@router.post("/{slug}/members", response_model=MemberResponse)
async def add_member(slug: str, payload: AddMemberRequest, request: Request) -> MemberResponse:
    """Add a member. Requires admin role or above."""
    if payload.role not in ROLE_RANK:
        raise HTTPException(status_code=422, detail="Invalid role")

    org, caller_membership = await require_org_role(request, slug, "admin")

    if ROLE_RANK.get(payload.role, 0) >= ROLE_RANK.get(caller_membership["role"], 0):
        raise HTTPException(status_code=403, detail="Cannot assign a role equal to or above your own")

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        member_count = await conn.fetchval(
            "SELECT COUNT(*) FROM org_memberships WHERE org_id = $1", org["id"]
        )
        if member_count >= org["max_members"]:
            raise HTTPException(status_code=403, detail="Organization member limit reached")

        user = await conn.fetchrow("SELECT id, display_name FROM users WHERE email = $1", payload.email)
        if not user:
            user = await conn.fetchrow(
                """INSERT INTO users (email, display_name) VALUES ($1, $2)
                   RETURNING id, display_name""",
                payload.email, payload.email.split("@")[0],
            )
        try:
            await conn.execute(
                """INSERT INTO org_memberships (org_id, user_id, role)
                   VALUES ($1, $2, $3)""",
                org["id"], user["id"], payload.role,
            )
        except Exception:
            raise HTTPException(status_code=409, detail="User is already a member")
        await conn.execute(
            """INSERT INTO user_stats (user_id, org_id)
               VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING""",
            user["id"], org["id"],
        )

    return MemberResponse(
        user_id=str(user["id"]), email=payload.email,
        display_name=user["display_name"], role=payload.role,
    )


@router.get("/{slug}/members")
async def list_members(slug: str, request: Request) -> list[MemberResponse]:
    """List members. Requires viewer role or above."""
    org, _membership = await require_org_role(request, slug, "viewer")
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT u.id, u.email, u.display_name, m.role
               FROM org_memberships m JOIN users u ON u.id = m.user_id
               WHERE m.org_id = $1 ORDER BY m.created_at""",
            org["id"],
        )

    return [
        MemberResponse(
            user_id=str(r["id"]), email=r["email"],
            display_name=r["display_name"], role=r["role"],
        )
        for r in rows
    ]


@router.delete("/{slug}/members/{user_id}")
async def remove_member(slug: str, user_id: str, request: Request) -> dict[str, str]:
    """Remove a member. Requires admin role or above."""
    org, caller_membership = await require_org_role(request, slug, "admin")

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        target = await conn.fetchrow(
            "SELECT role FROM org_memberships WHERE org_id = $1 AND user_id = $2",
            org["id"], user_id,
        )
        if not target:
            raise HTTPException(status_code=404, detail="Member not found")
        if ROLE_RANK.get(target["role"], 0) >= ROLE_RANK.get(caller_membership["role"], 0):
            raise HTTPException(status_code=403, detail="Cannot remove a member with equal or higher role")

        await conn.execute(
            "DELETE FROM org_memberships WHERE org_id = $1 AND user_id = $2",
            org["id"], user_id,
        )

    return {"status": "removed"}


@router.get("/{slug}/leaderboard")
async def leaderboard(slug: str, request: Request, limit: int = 20) -> list[LeaderboardEntry]:
    """Get the leaderboard. Requires viewer role or above."""
    org, _membership = await require_org_role(request, slug, "viewer")
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT s.user_id, u.display_name,
                      s.trajectories_contributed, s.helpful_votes_received,
                      s.tokens_saved_estimate
               FROM user_stats s JOIN users u ON u.id = s.user_id
               WHERE s.org_id = $1
               ORDER BY s.helpful_votes_received DESC, s.trajectories_contributed DESC
               LIMIT $2""",
            org["id"], limit,
        )

    return [
        LeaderboardEntry(
            user_id=str(r["user_id"]), display_name=r["display_name"],
            trajectories_contributed=r["trajectories_contributed"],
            helpful_votes_received=r["helpful_votes_received"],
            tokens_saved_estimate=r["tokens_saved_estimate"],
        )
        for r in rows
    ]
