"""Role-based access control helpers for org-scoped endpoints.

Every org endpoint must verify that the caller is a member of the
target organization and holds a sufficient role.  The role hierarchy
(most to least privileged) is:

    owner > admin > member > viewer

Usage in a route::

    @router.post("/{slug}/members")
    async def add_member(slug: str, request: Request):
        org, membership = await require_org_role(request, slug, "admin")
        ...
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

ROLE_RANK = {"owner": 40, "admin": 30, "member": 20, "viewer": 10}


def _current_user_id(request: Request) -> str:
    """Extract the authenticated user ID from the request.

    Checks (in order):
      1. OAuth session (set by the /auth flow)
      2. API key with a linked user_id
    """
    session = getattr(request.state, "session", None)
    if session and session.get("user_id"):
        return session["user_id"]

    key_info = getattr(request.state, "api_key_info", None)
    if key_info and key_info.get("user_id"):
        return key_info["user_id"]

    raise HTTPException(status_code=401, detail="Authentication required for org operations")


async def _get_pool(request: Request):
    svc = request.app.state.services
    closeable = svc._closeable
    if not hasattr(closeable, "get_pool"):
        raise HTTPException(status_code=501, detail="Org management requires a database backend")
    return await closeable.get_pool()


async def require_org_role(
    request: Request,
    slug: str,
    minimum_role: str = "viewer",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the caller is a member of the org with at least *minimum_role*.

    Returns (org_row, membership_row) on success.
    Raises 401/403/404 on failure.
    """
    user_id = _current_user_id(request)
    min_rank = ROLE_RANK.get(minimum_role, 0)
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        org = await conn.fetchrow("SELECT * FROM organizations WHERE slug = $1", slug)
        if not org:
            raise HTTPException(status_code=404, detail="Organization not found")

        membership = await conn.fetchrow(
            "SELECT * FROM org_memberships WHERE org_id = $1 AND user_id = $2",
            org["id"], user_id,
        )

    if not membership:
        raise HTTPException(status_code=403, detail="Not a member of this organization")

    actual_rank = ROLE_RANK.get(membership["role"], 0)
    if actual_rank < min_rank:
        raise HTTPException(
            status_code=403,
            detail=f"Requires {minimum_role} role or higher",
        )

    return dict(org), dict(membership)


async def get_user_orgs(request: Request) -> list[dict[str, Any]]:
    """Return all organizations the current user belongs to."""
    user_id = _current_user_id(request)
    pool = await _get_pool(request)

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT o.id, o.name, o.slug, o.plan, m.role
               FROM org_memberships m
               JOIN organizations o ON o.id = m.org_id
               WHERE m.user_id = $1
               ORDER BY o.name""",
            user_id,
        )
    return [dict(r) for r in rows]
