"""FastAPI API Gateway for the AuraBook Cloud Brain."""

from __future__ import annotations

import asyncio
import logging
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import Settings, get_settings
from db.demo_store import DemoGraphStore, DemoVectorStore, DeterministicEmbeddingService
from db.graph import SupabaseGraphStore
from db.supabase_client import SupabaseClientProvider
from db.vector import SupabaseVectorStore
from models.schema import (
    DashboardSnapshot,
    FeedbackRequest,
    FeedbackResponse,
    IngestResponse,
    PrefetchRequest,
    PrefetchResponse,
    SearchRequest,
    SearchResponse,
    TrajectoryIngestRequest,
)
from services.embeddings import EmbeddingService
from services.dashboard import DashboardService
from services.idempotency import FileBackedIdempotencyStore, MemoryIdempotencyStore
from services.live_compare import run_live_compare
from services.model_catalog import list_models_for_key, warm_models
from services.model_providers import GeminiEmbeddingProvider, OpenAICompatibleEmbeddingProvider
from services.privacy import sanitize_ingest_payload
from worker.janitor import NotesJanitorJob
from worker.summarizer import build_summarizer

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CloudBrainServices:
    """Dependency container for request handlers."""

    settings: Settings
    supabase_provider: SupabaseClientProvider | None
    graph_store: Any
    vector_store: Any
    embedding_service: Any
    idempotency_store: Any | None


class DemoCompareRequest(BaseModel):
    prompt: str
    api_key: str
    model: str | None = None
    provider: str | None = None


class DemoModelsRequest(BaseModel):
    api_key: str
    provider: str | None = None


class DemoModelsResponse(BaseModel):
    provider: str
    valid: bool
    models: list[str]
    default_model: str
    cache_status: str
    error: str | None = None


class DemoEventOut(BaseModel):
    at: int
    kind: str
    text: str
    token_count: int
    status_label: str


class DemoCompareResponse(BaseModel):
    injected_note: str | None
    left_events: list[DemoEventOut]
    right_events: list[DemoEventOut]
    baseline_tokens: int
    aurabook_tokens: int
    total_tokens: int
    provider_used: str
    model_used: str
    retrieval_provider_call_count: int
    provider_call_count: int


def _detect_live_demo_provider(api_key: str) -> str:
    key = api_key.strip()
    if key.startswith("sk-ant-"):
        return "claude"
    if key.startswith("AIza"):
        return "gemini"
    return "openai"


async def _embed_live_demo_prompt(prompt: str, api_key: str) -> list[float] | None:
    provider_name = _detect_live_demo_provider(api_key)
    if provider_name == "openai":
        provider = OpenAICompatibleEmbeddingProvider(
            provider_name="openai",
            base_url="https://api.openai.com/v1",
            api_key=api_key,
            model_name="text-embedding-3-small",
            timeout=30.0,
        )
    elif provider_name == "gemini":
        provider = GeminiEmbeddingProvider(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            api_key=api_key,
            model_name="gemini-embedding-001",
            timeout=30.0,
        )
    else:
        return None

    try:
        return await provider.embed_text(prompt)
    finally:
        await provider.close()


def build_services(settings: Settings | None = None) -> CloudBrainServices:
    """Construct the production dependency graph."""
    resolved_settings = settings or get_settings()
    use_demo_mode = resolved_settings.demo_mode or not (
        resolved_settings.supabase_url
        and resolved_settings.supabase_service_role_key
        and resolved_settings.embedding_provider_ready()
    )
    if use_demo_mode:
        demo_path = Path(resolved_settings.demo_store_path).expanduser().resolve()
        return CloudBrainServices(
            settings=resolved_settings,
            supabase_provider=None,
            graph_store=DemoGraphStore(demo_path),
            vector_store=DemoVectorStore(demo_path),
            embedding_service=DeterministicEmbeddingService(),
            idempotency_store=FileBackedIdempotencyStore(
                demo_path.with_name(f"{demo_path.stem}_idempotency.json")
            ),
        )

    provider = SupabaseClientProvider(resolved_settings)
    return CloudBrainServices(
        settings=resolved_settings,
        supabase_provider=provider,
        graph_store=SupabaseGraphStore(provider),
        vector_store=SupabaseVectorStore(provider),
        embedding_service=EmbeddingService(resolved_settings),
        idempotency_store=MemoryIdempotencyStore(),
    )


def create_app(
    settings: Settings | None = None,
    services: CloudBrainServices | None = None,
) -> FastAPI:
    """Create the FastAPI application."""
    resolved_services = services or build_services(settings)
    logging.basicConfig(level=getattr(logging, resolved_services.settings.log_level.upper(), logging.INFO))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("AuraBook Cloud Brain starting")
        try:
            yield
        finally:
            provider = resolved_services.supabase_provider
            if provider is not None:
                await provider.close()
            close = getattr(resolved_services.embedding_service, "close", None)
            if callable(close):
                await close()
            logger.info("AuraBook Cloud Brain stopped")

    app = FastAPI(
        title="AuraBook Cloud Brain",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$|https://.*\.vercel\.app$",
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.services = resolved_services
    app.state.dashboard_service = DashboardService(
        graph_store=resolved_services.graph_store,
        vector_store=resolved_services.vector_store,
    )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "demo"
            if resolved_services.supabase_provider is None
            else "supabase",
        }

    @app.get("/v1/dashboard/snapshot", response_model=DashboardSnapshot)
    async def dashboard_snapshot(request: Request) -> DashboardSnapshot:
        dashboard_service: DashboardService = request.app.state.dashboard_service
        return await dashboard_service.build_snapshot()

    @app.websocket("/v1/dashboard/ws")
    async def dashboard_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        dashboard_service: DashboardService = websocket.app.state.dashboard_service
        try:
            while True:
                snapshot = await dashboard_service.build_snapshot()
                await websocket.send_json(
                    {
                        "type": "snapshot",
                        "snapshot": snapshot.model_dump(mode="json", by_alias=True),
                    }
                )
                await asyncio.sleep(8)
        except WebSocketDisconnect:
            logger.info("Dashboard websocket disconnected")

    @app.post("/v1/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackRequest, request: Request) -> FeedbackResponse:
        svc: CloudBrainServices = request.app.state.services
        await _validate_signature(request, svc.settings)
        cached = _load_idempotent_response(request, svc, FeedbackResponse)
        if cached is not None:
            return cached
        submit_feedback = getattr(svc.vector_store, "submit_feedback", None)
        if callable(submit_feedback) and payload.note_id is not None:
            await submit_feedback(payload)
        response = FeedbackResponse(
            accepted=True,
            note_id=payload.note_id,
            trajectory_id=payload.trajectory_id,
            feedback=payload.feedback,
        )
        _store_idempotent_response(request, svc, response.model_dump(mode="json"))
        return response

    async def _run_janitor_for_new_trajectory(svc: CloudBrainServices) -> None:
        """Process any freshly ingested unprocessed trajectories in the background."""
        try:
            summarizer = build_summarizer(svc.settings)
            job = NotesJanitorJob(
                graph_store=svc.graph_store,
                vector_store=svc.vector_store,
                summarizer=summarizer,
                embedding_service=svc.embedding_service,
                summary_model=svc.settings.resolve_summary_model(),
                batch_size=1,
            )
            result = await job.run_once()
            if result.processed:
                logger.info("Auto-janitor processed %d trajectory into a note", result.processed)
        except Exception:
            logger.exception("Auto-janitor background task failed — trajectory stored but not yet summarised")

    @app.post("/v1/ingest", response_model=IngestResponse)
    async def ingest(
        payload: TrajectoryIngestRequest,
        request: Request,
        background_tasks: BackgroundTasks,
    ) -> IngestResponse:
        svc: CloudBrainServices = request.app.state.services
        await _validate_signature(request, svc.settings)
        cached = _load_idempotent_response(request, svc, IngestResponse)
        if cached is not None:
            return cached
        response = await svc.graph_store.insert_trajectory(sanitize_ingest_payload(payload))
        _store_idempotent_response(request, svc, response.model_dump(mode="json"))
        # Automatically convert the new trajectory into a searchable note.
        # Runs after the response is returned so the ingest latency is unaffected.
        if response.created:
            background_tasks.add_task(_run_janitor_for_new_trajectory, svc)
        return response

    @app.post("/v1/prefetch", response_model=PrefetchResponse)
    async def prefetch(payload: PrefetchRequest, request: Request) -> PrefetchResponse:
        svc: CloudBrainServices = request.app.state.services
        await _validate_signature(request, svc.settings)
        match = await svc.graph_store.get_prefetch_match(payload.cdc_hash)
        return PrefetchResponse(found=match is not None, match=match)

    @app.post("/v1/search", response_model=SearchResponse)
    async def search(payload: SearchRequest, request: Request) -> SearchResponse:
        svc: CloudBrainServices = request.app.state.services
        await _validate_signature(request, svc.settings)
        query_text = payload.searchable_text()
        query_embedding = await svc.embedding_service.embed_text(payload.searchable_text())
        matches = await svc.vector_store.search(
            query_embedding,
            query_text=query_text,
            settings=svc.settings,
            limit=max(payload.limit * svc.settings.search_candidate_multiplier, payload.limit),
            match_threshold=(
                payload.match_threshold
                if payload.match_threshold is not None
                else svc.settings.search_match_threshold
            ),
        )
        matches = matches[: payload.limit]
        record_matches = getattr(svc.vector_store, "record_matches", None)
        if callable(record_matches):
            await record_matches([match.note_id for match in matches])
        return SearchResponse(matches=matches)

    @app.post("/v1/demo/compare", response_model=DemoCompareResponse)
    async def demo_compare(payload: DemoCompareRequest, request: Request) -> DemoCompareResponse:
        svc: CloudBrainServices = request.app.state.services

        injected_note: str | None = None
        retrieval_provider_call_count = 0
        try:
            provider = payload.provider or _detect_live_demo_provider(payload.api_key)
            if provider in {"openai", "gemini"}:
                retrieval_provider_call_count = 1
            query_embedding = (
                await _embed_live_demo_prompt(payload.prompt, payload.api_key)
                if provider in {"openai", "gemini"} else None
            )
            if query_embedding is None:
                logger.info("Skipping live demo note search because provider has no supported embedding path")
            else:
                matches = await svc.vector_store.search(
                    query_embedding,
                    query_text=payload.prompt,
                    settings=svc.settings,
                    limit=1,
                    match_threshold=0.25,
                )
                if matches:
                    injected_note = matches[0].note_text
        except Exception:
            logger.exception("Note search failed during demo compare — continuing without note")

        result = await run_live_compare(
            prompt=payload.prompt,
            api_key=payload.api_key,
            injected_note=injected_note,
            model_name=payload.model,
            provider=payload.provider,
        )

        return DemoCompareResponse(
            injected_note=result.injected_note,
            left_events=[
                DemoEventOut(
                    at=e.at,
                    kind=e.kind,
                    text=e.text,
                    token_count=e.token_count,
                    status_label=e.status_label,
                )
                for e in result.left_events
            ],
            right_events=[
                DemoEventOut(
                    at=e.at,
                    kind=e.kind,
                    text=e.text,
                    token_count=e.token_count,
                    status_label=e.status_label,
                )
                for e in result.right_events
            ],
            baseline_tokens=result.baseline_tokens,
            aurabook_tokens=result.aurabook_tokens,
            total_tokens=result.total_tokens,
            provider_used=result.provider_used,
            model_used=result.model_used,
            retrieval_provider_call_count=retrieval_provider_call_count,
            provider_call_count=result.provider_call_count + retrieval_provider_call_count,
        )

    @app.get("/v1/demo/models/warm", response_model=DemoModelsResponse)
    async def demo_models_warm(provider: str = "openai") -> DemoModelsResponse:
        result = warm_models(provider)
        return DemoModelsResponse(
            provider=result.provider,
            valid=result.valid,
            models=result.models,
            default_model=result.default_model,
            cache_status=result.cache_status,
            error=result.error,
        )

    @app.post("/v1/demo/models", response_model=DemoModelsResponse)
    async def demo_models(payload: DemoModelsRequest) -> DemoModelsResponse:
        result = await list_models_for_key(payload.api_key, provider=payload.provider)
        return DemoModelsResponse(
            provider=result.provider,
            valid=result.valid,
            models=result.models,
            default_model=result.default_model,
            cache_status=result.cache_status,
            error=result.error,
        )

    @app.post("/v1/admin/prune")
    async def prune(request: Request) -> dict[str, int]:
        svc: CloudBrainServices = request.app.state.services
        await _validate_signature(request, svc.settings)
        delete_older_than = getattr(svc.graph_store, "delete_older_than", None)
        if not callable(delete_older_than):
            return {"deleted": 0}
        deleted = await delete_older_than(retention_days=svc.settings.retention_days)
        return {"deleted": deleted}

    return app


def main() -> None:
    """Run the API with uvicorn."""
    settings = get_settings()
    app = create_app(settings=settings)
    uvicorn.run(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


app = create_app()


async def _validate_signature(request: Request, settings: Settings) -> None:
    """Validate signed AuraBook client requests when a shared secret is configured."""
    if not settings.api_shared_secret:
        return
    timestamp = request.headers.get("X-AuraBook-Timestamp", "")
    signature = request.headers.get("X-AuraBook-Signature", "")
    if not timestamp or not signature:
        raise HTTPException(status_code=401, detail="Missing AuraBook signature headers")
    try:
        timestamp_value = int(timestamp)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid AuraBook timestamp") from exc
    if abs(time.time() - timestamp_value) > settings.api_signature_max_age_s:
        raise HTTPException(status_code=401, detail="Expired AuraBook signature timestamp")
    body = await request.body()
    expected = hmac.new(
        settings.api_shared_secret.encode("utf-8"),
        f"{timestamp}.{body.decode('utf-8')}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid AuraBook signature")


def _load_idempotent_response(model_request: Request, services: CloudBrainServices, model_type):
    key = model_request.headers.get("X-AuraBook-Idempotency-Key", "").strip()
    if not key or services.idempotency_store is None:
        return None
    cached = services.idempotency_store.get(key)
    if cached is None:
        return None
    return model_type.model_validate(cached)


def _store_idempotent_response(
    model_request: Request,
    services: CloudBrainServices,
    payload: dict[str, Any],
) -> None:
    key = model_request.headers.get("X-AuraBook-Idempotency-Key", "").strip()
    if not key or services.idempotency_store is None:
        return
    services.idempotency_store.put(
        key,
        payload,
        ttl_s=services.settings.idempotency_ttl_s,
    )
