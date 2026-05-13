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

from api.auth import api_keys_ddl, extract_api_key, generate_api_key, validate_api_key
from api.observability import RequestLoggingMiddleware, register_observability_routes
from api.rate_limit import RateLimiter
from config import Settings, get_settings
from db.base import GraphStore, VectorStore
from db.demo_store import DemoGraphStore, DemoVectorStore, DeterministicEmbeddingService
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
    graph_store: GraphStore
    vector_store: VectorStore
    embedding_service: Any
    idempotency_store: Any | None
    _closeable: Any | None = None


class DemoCompareRequest(BaseModel):
    prompt: str
    api_key: str
    model: str | None = None


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


def _resolve_db_backend(settings: Settings) -> str:
    """Determine which database backend to use."""
    if settings.db_backend.strip():
        return settings.db_backend.strip().lower()
    if settings.database_url.strip():
        return "postgres"
    if settings.supabase_url and settings.supabase_service_role_key:
        return "supabase"
    return "demo"


def build_services(settings: Settings | None = None) -> CloudBrainServices:
    """Construct the production dependency graph."""
    resolved_settings = settings or get_settings()
    backend = _resolve_db_backend(resolved_settings)

    if resolved_settings.demo_mode:
        backend = "demo"

    if backend == "demo":
        demo_path = Path(resolved_settings.demo_store_path).expanduser().resolve()
        return CloudBrainServices(
            settings=resolved_settings,
            graph_store=DemoGraphStore(demo_path),
            vector_store=DemoVectorStore(demo_path),
            embedding_service=DeterministicEmbeddingService(),
            idempotency_store=FileBackedIdempotencyStore(
                demo_path.with_name(f"{demo_path.stem}_idempotency.json")
            ),
        )

    if backend == "postgres":
        from db.postgres import PostgresConnectionProvider, PostgresGraphStore, PostgresVectorStore

        provider = PostgresConnectionProvider(resolved_settings.database_url)
        return CloudBrainServices(
            settings=resolved_settings,
            graph_store=PostgresGraphStore(provider),
            vector_store=PostgresVectorStore(provider),
            embedding_service=EmbeddingService(resolved_settings),
            idempotency_store=MemoryIdempotencyStore(),
            _closeable=provider,
        )

    if backend == "supabase":
        from db.graph import SupabaseGraphStore
        from db.supabase_client import SupabaseClientProvider
        from db.vector import SupabaseVectorStore

        provider = SupabaseClientProvider(resolved_settings)
        return CloudBrainServices(
            settings=resolved_settings,
            graph_store=SupabaseGraphStore(provider),
            vector_store=SupabaseVectorStore(provider),
            embedding_service=EmbeddingService(resolved_settings),
            idempotency_store=MemoryIdempotencyStore(),
            _closeable=provider,
        )

    raise ValueError(f"Unknown db_backend: {backend!r}. Use 'postgres', 'supabase', or 'demo'.")


def create_app(
    settings: Settings | None = None,
    services: CloudBrainServices | None = None,
) -> FastAPI:
    """Create the FastAPI application."""
    resolved_services = services or build_services(settings)
    logging.basicConfig(level=getattr(logging, resolved_services.settings.log_level.upper(), logging.INFO))

    rate_limiter = RateLimiter(
        requests_per_minute=resolved_services.settings.rate_limit_rpm,
        burst=resolved_services.settings.rate_limit_burst,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("AuraBook Cloud Brain starting (backend=%s)", _resolve_db_backend(resolved_services.settings))
        closeable = resolved_services._closeable
        if hasattr(closeable, "ensure_schema"):
            await closeable.ensure_schema()
            pool = await closeable.get_pool()
            async with pool.acquire() as conn:
                await conn.execute(api_keys_ddl())
        try:
            yield
        finally:
            if closeable is not None:
                await closeable.close()
            close = getattr(resolved_services.embedding_service, "close", None)
            if callable(close):
                await close()
            logger.info("AuraBook Cloud Brain stopped")

    app = FastAPI(
        title="AuraBook Cloud Brain",
        version="0.1.0",
        lifespan=lifespan,
    )
    cors_origins = resolved_services.settings.cors_allowed_origins.strip()
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in cors_origins.split(",") if o.strip()],
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    else:
        app.add_middleware(
            CORSMiddleware,
            allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?$",
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestLoggingMiddleware)
    app.state.services = resolved_services
    app.state.rate_limiter = rate_limiter
    register_observability_routes(app, resolved_services)
    app.state.dashboard_service = DashboardService(
        graph_store=resolved_services.graph_store,
        vector_store=resolved_services.vector_store,
    )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        backend = _resolve_db_backend(resolved_services.settings)
        note_count = 0
        try:
            note_count = await resolved_services.vector_store.count()
        except Exception:
            pass
        return {
            "status": "ok",
            "backend": backend,
            "note_count": note_count,
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

    async def _authenticate(request: Request) -> None:
        """Run rate limiting, then API key or HMAC auth."""
        svc: CloudBrainServices = request.app.state.services
        rate_limiter.check(request)
        pool_getter = None
        closeable = svc._closeable
        if hasattr(closeable, "get_pool"):
            pool_getter = closeable.get_pool
        key_info = await validate_api_key(
            request,
            require=svc.settings.require_api_key,
            pool_getter=pool_getter,
        )
        if key_info is not None:
            request.state.api_key_info = key_info
            return
        await _validate_signature(request, svc.settings)

    @app.post("/v1/feedback", response_model=FeedbackResponse)
    async def feedback(payload: FeedbackRequest, request: Request) -> FeedbackResponse:
        svc: CloudBrainServices = request.app.state.services
        await _authenticate(request)
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
        await _authenticate(request)
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
        await _authenticate(request)
        match = await svc.graph_store.get_prefetch_match(payload.cdc_hash)
        return PrefetchResponse(found=match is not None, match=match)

    @app.post("/v1/search", response_model=SearchResponse)
    async def search(payload: SearchRequest, request: Request) -> SearchResponse:
        svc: CloudBrainServices = request.app.state.services
        await _authenticate(request)
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
            if _detect_live_demo_provider(payload.api_key) in {"openai", "gemini"}:
                retrieval_provider_call_count = 1
            query_embedding = await _embed_live_demo_prompt(payload.prompt, payload.api_key)
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

    class CreateApiKeyRequest(BaseModel):
        name: str

    class CreateApiKeyResponse(BaseModel):
        api_key: str
        key_id: str
        name: str

    @app.post("/v1/admin/api-keys", response_model=CreateApiKeyResponse)
    async def create_api_key(payload: CreateApiKeyRequest, request: Request) -> CreateApiKeyResponse:
        svc: CloudBrainServices = request.app.state.services
        await _authenticate(request)
        closeable = svc._closeable
        if not hasattr(closeable, "get_pool"):
            raise HTTPException(status_code=501, detail="API key management requires a database backend")
        raw_key, key_hash = generate_api_key()
        pool = await closeable.get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO api_keys (name, key_hash, key_prefix)
                   VALUES ($1, $2, $3) RETURNING id""",
                payload.name, key_hash, raw_key[:12],
            )
        return CreateApiKeyResponse(
            api_key=raw_key,
            key_id=str(row["id"]),
            name=payload.name,
        )

    @app.post("/v1/admin/prune")
    async def prune(request: Request) -> dict[str, int]:
        svc: CloudBrainServices = request.app.state.services
        await _authenticate(request)
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
