"""GitHub OAuth2 authentication flow for the AuraBook Cloud Brain.

Implements the authorization code flow:
  1. GET  /auth/github        → redirect to GitHub's authorize URL
  2. GET  /auth/github/callback → exchange code for token, upsert user, set session
  3. GET  /auth/me             → return current user from session
  4. POST /auth/logout         → clear session

Sessions are stored in signed, HTTP-only cookies using itsdangerous.
"""

from __future__ import annotations

import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

SESSION_COOKIE = "aurabook_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


class UserInfo(BaseModel):
    user_id: str
    email: str
    display_name: str
    avatar_url: str | None = None
    auth_provider: str = "github"


def _get_oauth_settings(request: Request) -> dict[str, str]:
    settings = request.app.state.services.settings
    client_id = getattr(settings, "github_client_id", "")
    client_secret = getattr(settings, "github_client_secret", "")
    if not client_id or not client_secret:
        raise HTTPException(status_code=501, detail="GitHub OAuth not configured")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": getattr(settings, "github_redirect_uri", ""),
    }


def _get_signer(request: Request):
    from itsdangerous import URLSafeTimedSerializer
    secret = request.app.state.services.settings.session_secret
    if not secret:
        raise HTTPException(status_code=500, detail="Session secret not configured")
    return URLSafeTimedSerializer(secret)


def sign_session(request: Request, data: dict[str, Any]) -> str:
    return _get_signer(request).dumps(data)


def load_session(request: Request) -> dict[str, Any] | None:
    raw = request.cookies.get(SESSION_COOKIE)
    if not raw:
        return None
    secret = getattr(request.app.state.services.settings, "session_secret", "")
    if not secret:
        return None
    try:
        from itsdangerous import URLSafeTimedSerializer
        signer = URLSafeTimedSerializer(secret)
        return signer.loads(raw, max_age=SESSION_MAX_AGE)
    except Exception:
        return None


def require_session(request: Request) -> dict[str, Any]:
    session = load_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return session


@router.get("/github")
async def github_login(request: Request) -> Response:
    """Redirect the user to GitHub's OAuth authorization page."""
    oauth = _get_oauth_settings(request)
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": oauth["client_id"],
        "redirect_uri": oauth["redirect_uri"],
        "scope": "user:email",
        "state": state,
    }
    url = f"https://github.com/login/oauth/authorize?{urlencode(params)}"
    response = Response(status_code=307, headers={"Location": url})
    response.set_cookie("oauth_state", state, httponly=True, max_age=600, samesite="lax")
    return response


@router.get("/github/callback")
async def github_callback(request: Request, code: str, state: str) -> Response:
    """Exchange the GitHub authorization code for an access token and create a session."""
    stored_state = request.cookies.get("oauth_state")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    oauth = _get_oauth_settings(request)

    async with httpx.AsyncClient(timeout=15) as client:
        token_resp = await client.post(
            "https://github.com/login/oauth/access_token",
            json={
                "client_id": oauth["client_id"],
                "client_secret": oauth["client_secret"],
                "code": code,
                "redirect_uri": oauth["redirect_uri"],
            },
            headers={"Accept": "application/json"},
        )
        token_data = token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise HTTPException(status_code=400, detail="GitHub OAuth token exchange failed")

        user_resp = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
        )
        gh_user = user_resp.json()

        email = gh_user.get("email")
        if not email:
            emails_resp = await client.get(
                "https://api.github.com/user/emails",
                headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
            )
            for e in emails_resp.json():
                if e.get("primary") and e.get("verified"):
                    email = e["email"]
                    break
        if not email:
            raise HTTPException(status_code=400, detail="Could not retrieve email from GitHub")

    user_row = await _upsert_user(
        request,
        email=email,
        display_name=gh_user.get("name") or gh_user.get("login", ""),
        avatar_url=gh_user.get("avatar_url"),
        auth_provider="github",
        auth_provider_id=str(gh_user.get("id", "")),
    )

    session_data = {
        "user_id": str(user_row["id"]),
        "email": email,
        "display_name": user_row["display_name"],
    }
    token = sign_session(request, session_data)

    frontend_url = getattr(request.app.state.services.settings, "frontend_url", "") or "/"
    response = Response(status_code=307, headers={"Location": frontend_url})
    response.set_cookie(
        SESSION_COOKIE, token,
        httponly=True, max_age=SESSION_MAX_AGE, samesite="lax", secure=True,
    )
    response.delete_cookie("oauth_state")
    return response


async def _upsert_user(
    request: Request,
    *,
    email: str,
    display_name: str,
    avatar_url: str | None,
    auth_provider: str,
    auth_provider_id: str,
) -> dict[str, Any]:
    svc = request.app.state.services
    closeable = svc._closeable
    if not hasattr(closeable, "get_pool"):
        raise HTTPException(status_code=501, detail="OAuth requires a database backend")
    pool = await closeable.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO users (email, display_name, avatar_url, auth_provider, auth_provider_id)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (email) DO UPDATE SET
                 display_name = EXCLUDED.display_name,
                 avatar_url = EXCLUDED.avatar_url,
                 auth_provider = EXCLUDED.auth_provider,
                 auth_provider_id = EXCLUDED.auth_provider_id,
                 updated_at = (NOW() AT TIME ZONE 'utc')
               RETURNING id, email, display_name, avatar_url""",
            email, display_name, avatar_url, auth_provider, auth_provider_id,
        )
    return dict(row)


@router.get("/me", response_model=UserInfo)
async def get_current_user(request: Request) -> UserInfo:
    """Return the currently authenticated user."""
    session = require_session(request)
    return UserInfo(
        user_id=session["user_id"],
        email=session["email"],
        display_name=session["display_name"],
        avatar_url=session.get("avatar_url"),
    )


@router.post("/logout")
async def logout() -> Response:
    """Clear the session cookie."""
    response = Response(content='{"status":"logged_out"}', media_type="application/json")
    response.delete_cookie(SESSION_COOKIE)
    return response
