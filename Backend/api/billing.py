"""Stripe billing integration for plan upgrades.

Endpoints:
  POST /v1/billing/checkout   → Create a Stripe Checkout session
  POST /v1/billing/portal     → Create a Stripe Customer Portal session
  POST /v1/billing/webhook    → Handle Stripe webhook events

Plan upgrades are applied via Stripe webhooks when payment succeeds.
The organization's plan, limits, and Stripe customer ID are updated
in the database.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from api.rbac import require_org_role, _get_pool
from db.multi_tenant import PLAN_LIMITS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1/billing", tags=["billing"])

# Maps Stripe price IDs → plan names (configured via env)
_PRICE_TO_PLAN: dict[str, str] = {}


def _get_stripe(request: Request):
    """Lazy-import and configure Stripe."""
    import stripe

    settings = request.app.state.services.settings
    api_key = getattr(settings, "stripe_secret_key", "")
    if not api_key:
        raise HTTPException(status_code=501, detail="Stripe not configured")
    stripe.api_key = api_key
    return stripe


def _load_price_map(request: Request) -> dict[str, str]:
    """Load price → plan mapping from settings."""
    if _PRICE_TO_PLAN:
        return _PRICE_TO_PLAN
    settings = request.app.state.services.settings
    mapping = getattr(settings, "stripe_price_plan_map", "")
    if mapping:
        for pair in mapping.split(","):
            if ":" in pair:
                price_id, plan = pair.strip().split(":", 1)
                _PRICE_TO_PLAN[price_id.strip()] = plan.strip()
    return _PRICE_TO_PLAN


class CheckoutRequest(BaseModel):
    slug: str
    plan: str


class CheckoutResponse(BaseModel):
    checkout_url: str


class PortalRequest(BaseModel):
    slug: str


class PortalResponse(BaseModel):
    portal_url: str


@router.post("/checkout", response_model=CheckoutResponse)
async def create_checkout(payload: CheckoutRequest, request: Request) -> CheckoutResponse:
    """Create a Stripe Checkout session for plan upgrade."""
    org, _membership = await require_org_role(request, payload.slug, "owner")
    stripe = _get_stripe(request)
    settings = request.app.state.services.settings

    if payload.plan not in PLAN_LIMITS or payload.plan == "free":
        raise HTTPException(status_code=400, detail=f"Invalid target plan: {payload.plan}")

    price_map = _load_price_map(request)
    price_id = None
    for pid, plan_name in price_map.items():
        if plan_name == payload.plan:
            price_id = pid
            break

    if not price_id:
        raise HTTPException(status_code=400, detail=f"No price configured for plan: {payload.plan}")

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        customer_id = await conn.fetchval(
            "SELECT metadata->>'stripe_customer_id' FROM organizations WHERE id = $1",
            org["id"],
        )

    checkout_params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{settings.frontend_url}/orgs/{payload.slug}/settings?upgraded=true",
        "cancel_url": f"{settings.frontend_url}/orgs/{payload.slug}/settings",
        "metadata": {"org_id": str(org["id"]), "plan": payload.plan},
    }

    if customer_id:
        checkout_params["customer"] = customer_id
    else:
        checkout_params["customer_creation"] = "always"

    session = stripe.checkout.Session.create(**checkout_params)
    return CheckoutResponse(checkout_url=session.url)


@router.post("/portal", response_model=PortalResponse)
async def create_portal(payload: PortalRequest, request: Request) -> PortalResponse:
    """Create a Stripe Customer Portal session for subscription management."""
    org, _membership = await require_org_role(request, payload.slug, "owner")
    stripe = _get_stripe(request)
    settings = request.app.state.services.settings

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        customer_id = await conn.fetchval(
            "SELECT metadata->>'stripe_customer_id' FROM organizations WHERE id = $1",
            org["id"],
        )

    if not customer_id:
        raise HTTPException(status_code=400, detail="No billing account. Start a checkout first.")

    session = stripe.billing_portal.Session.create(
        customer=customer_id,
        return_url=f"{settings.frontend_url}/orgs/{payload.slug}/settings",
    )
    return PortalResponse(portal_url=session.url)


@router.post("/webhook")
async def stripe_webhook(request: Request) -> Response:
    """Handle Stripe webhook events (checkout.session.completed, subscription updates)."""
    stripe = _get_stripe(request)
    settings = request.app.state.services.settings
    webhook_secret = getattr(settings, "stripe_webhook_secret", "")

    body = await request.body()
    sig_header = request.headers.get("stripe-signature", "")

    if webhook_secret:
        try:
            event = stripe.Webhook.construct_event(body, sig_header, webhook_secret)
        except (stripe.error.SignatureVerificationError, ValueError) as e:
            logger.warning("Stripe webhook signature failed: %s", e)
            raise HTTPException(status_code=400, detail="Invalid signature")
    else:
        import json
        event = json.loads(body)

    event_type = event.get("type", "") if isinstance(event, dict) else event.type
    data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else event.data.object

    if event_type == "checkout.session.completed":
        await _handle_checkout_completed(request, data_obj)
    elif event_type in ("customer.subscription.updated", "customer.subscription.deleted"):
        await _handle_subscription_change(request, data_obj)

    return Response(status_code=200)


async def _handle_checkout_completed(request: Request, session: Any) -> None:
    """Apply plan upgrade after successful checkout."""
    metadata = session.get("metadata", {}) if isinstance(session, dict) else getattr(session, "metadata", {})
    org_id = metadata.get("org_id")
    plan = metadata.get("plan")
    customer_id = session.get("customer") if isinstance(session, dict) else getattr(session, "customer", None)

    if not org_id or not plan:
        logger.warning("Checkout completed without org_id/plan metadata")
        return

    limits = PLAN_LIMITS.get(plan)
    if not limits:
        logger.warning("Unknown plan in checkout metadata: %s", plan)
        return

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE organizations
               SET plan = $1, max_members = $2, max_api_keys = $3, max_trajectories = $4,
                   metadata = metadata || jsonb_build_object('stripe_customer_id', $5::text)
               WHERE id = $6""",
            plan, limits["max_members"], limits["max_api_keys"],
            limits["max_trajectories"], customer_id, org_id,
        )
    logger.info("Org %s upgraded to plan %s", org_id, plan)


async def _handle_subscription_change(request: Request, subscription: Any) -> None:
    """Handle subscription changes (downgrade, cancellation)."""
    customer_id = subscription.get("customer") if isinstance(subscription, dict) else getattr(subscription, "customer", None)
    status = subscription.get("status") if isinstance(subscription, dict) else getattr(subscription, "status", None)

    if not customer_id:
        return

    pool = await _get_pool(request)
    async with pool.acquire() as conn:
        org = await conn.fetchrow(
            "SELECT id FROM organizations WHERE metadata->>'stripe_customer_id' = $1",
            customer_id,
        )
        if not org:
            return

        if status in ("canceled", "unpaid", "past_due"):
            limits = PLAN_LIMITS["free"]
            await conn.execute(
                """UPDATE organizations
                   SET plan = 'free', max_members = $1, max_api_keys = $2, max_trajectories = $3
                   WHERE id = $4""",
                limits["max_members"], limits["max_api_keys"],
                limits["max_trajectories"], org["id"],
            )
            logger.info("Org %s downgraded to free (subscription %s)", org["id"], status)
