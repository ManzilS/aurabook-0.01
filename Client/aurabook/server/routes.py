"""FastAPI routes for the AuraBook proxy server."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.responses import HTMLResponse, StreamingResponse

from aurabook.core.models import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    CommunityFeedbackRequest,
    CommunityFeedbackResponse,
    Message,
    Role,
)
from aurabook.core.state import PipelineState
from aurabook.utils.config import Settings
from aurabook.utils.errors import (
    AdapterNotFoundError,
    AuraBookError,
    RequestValidationError,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def root() -> dict[str, str]:
    """Return a minimal service status response."""
    return {"status": "ok", "service": "aurabook-local-client"}


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    """Render a simple runtime dashboard."""
    runtime = request.app.state.runtime
    snapshot = runtime.status_store.snapshot()
    interventions = "".join(
        (
            "<tr>"
            f"<td>{entry.created_at}</td>"
            f"<td>{entry.gradient}</td>"
            f"<td>{entry.source}</td>"
            f"<td>{entry.note_id or ''}</td>"
            f"<td>{entry.trajectory_id or ''}</td>"
            f"<td>{entry.feedback or ''}</td>"
            "</tr>"
        )
        for entry in reversed(snapshot.recent_interventions)
    )
    html = f"""
    <html>
      <head><title>AuraBook Dashboard</title></head>
      <body>
        <h1>AuraBook Dashboard</h1>
        <p>Client ID: {snapshot.client_id}</p>
        <p>Cloud Enabled: {snapshot.cloud_enabled}</p>
        <p>Cloud Demo Mode: {snapshot.cloud_demo_mode}</p>
        <p>Max Gradient: {snapshot.max_gradient}</p>
        <p>Queue Depth: {snapshot.queue_depth}</p>
        <h2>Cache Metrics</h2>
        <pre>{json.dumps(snapshot.cache_metrics.model_dump(mode="json"), indent=2)}</pre>
        <h2>Recent Interventions</h2>
        <table border="1" cellpadding="6">
          <tr>
            <th>Time</th><th>Gradient</th><th>Source</th><th>Note</th><th>Trajectory</th><th>Feedback</th>
          </tr>
          {interventions}
        </table>
      </body>
    </html>
    """
    return HTMLResponse(content=html)


@router.get("/health")
async def health(request: Request) -> dict[str, Any]:
    """Return a detailed health payload for the daemon."""
    runtime = request.app.state.runtime
    return {
        "status": "healthy",
        "adapters": list(runtime.pipeline.adapters.keys()),
        "default_adapter": runtime.pipeline.default_adapter,
        "watch_root": str(runtime.settings.resolve_watch_root()),
        "runtime_status": runtime.status_store.snapshot().model_dump(mode="json"),
    }


@router.get("/v1/interventions")
async def interventions(request: Request) -> dict[str, Any]:
    """Return recent local intervention history."""
    runtime = request.app.state.runtime
    snapshot = runtime.status_store.snapshot()
    return {
        "repository": str(runtime.settings.resolve_watch_root()),
        "interventions": [entry.model_dump(mode="json") for entry in snapshot.recent_interventions],
    }


@router.post("/v1/feedback", response_model=CommunityFeedbackResponse)
async def feedback(
    payload: CommunityFeedbackRequest,
    request: Request,
) -> CommunityFeedbackResponse:
    """Submit feedback for a recent intervention and optionally suppress it locally."""
    runtime = request.app.state.runtime
    repo_root = runtime.settings.resolve_watch_root()
    response = CommunityFeedbackResponse(
        accepted=True,
        note_id=payload.note_id,
        trajectory_id=payload.trajectory_id,
        feedback=payload.feedback,
    )
    cloud_feedback = getattr(runtime.cloud_client, "submit_feedback", None)
    if callable(cloud_feedback):
        response = await cloud_feedback(payload)
    if runtime.settings.auto_suppress_negative_feedback and payload.feedback.value != "helpful":
        if payload.note_id is not None:
            runtime.preferences_store.suppress_note(repo_root, payload.note_id)
            runtime.community_service.suppress_note(payload.note_id)
        if payload.trajectory_id is not None:
            runtime.preferences_store.suppress_trajectory(repo_root, payload.trajectory_id)
            runtime.community_service.suppress_trajectory(payload.trajectory_id)
    runtime.community_service.record_intervention(
        gradient="feedback",  # type: ignore[arg-type]
        source="local_feedback",
        note_id=payload.note_id,
        trajectory_id=payload.trajectory_id,
        feedback=payload.feedback.value,
    )
    return response


@router.post("/v1/chat/completions", response_model=None)
async def chat_completions(request: Request) -> Response:
    """Handle an OpenAI-compatible chat completions request."""
    runtime = request.app.state.runtime
    settings: Settings = runtime.settings

    try:
        body: dict[str, Any] = await request.json()
    except json.JSONDecodeError as exc:
        raise RequestValidationError("Invalid JSON body", details=str(exc)) from exc
    except Exception as exc:
        raise RequestValidationError("Invalid JSON body", details=str(exc)) from exc

    pipeline_request = _parse_openai_request(body, request, settings)
    state = PipelineState(request=pipeline_request)

    if pipeline_request.stream:
        return await _handle_streaming(state, runtime)

    try:
        state = await runtime.orchestrator.run(state)
    except AuraBookError:
        raise
    except Exception as exc:
        logger.exception("Pipeline execution failed")
        raise AuraBookError(
            "Upstream AI request failed",
            details=str(exc) if settings.dev_mode else "",
        ) from exc

    if state.response is None:
        raise AuraBookError("No response from upstream AI")

    response = JSONResponse(content=_format_openai_response(state.response))
    _apply_aura_headers(response, state)
    return response


async def _handle_streaming(
    state: PipelineState,
    runtime,
) -> StreamingResponse:
    pipeline = runtime.pipeline

    for middleware in pipeline.pre_middleware:
        state = await middleware.process(state)
        if state.early_exit:
            response = StreamingResponse(
                _early_exit_stream(state),
                media_type="text/event-stream",
            )
            _apply_aura_headers(response, state)
            return response

    adapter_name = state.request.target_adapter or pipeline.default_adapter
    adapter = pipeline.adapters.get(adapter_name)
    if adapter is None:
        raise AdapterNotFoundError(
            f"No adapter registered for '{adapter_name}'",
            adapter=adapter_name,
        )

    async def stream_and_capture():
        full_text: list[str] = []
        async for chunk in adapter.stream(state.request):
            full_text.append(_extract_content_from_sse(chunk))
            yield chunk

        state.response = ChatCompletionResponse(
            model=state.request.model,
            choices=[
                Choice(
                    message=Message(role=Role.ASSISTANT, content="".join(full_text)),
                    finish_reason="stop",
                )
            ],
        )

        for middleware in pipeline.post_middleware:
            try:
                await middleware.process(state)
            except Exception as exc:
                logger.exception(
                    "Post-stream middleware %s failed: %s",
                    middleware.name,
                    exc,
                )

    response = StreamingResponse(
        stream_and_capture(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
    _apply_aura_headers(response, state)
    return response


async def _early_exit_stream(state: PipelineState):
    text = state.response.text if state.response else ""
    payload = {
        "id": state.request.id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": state.request.model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
    }
    yield f"data: {json.dumps(payload)}\n\n"
    yield "data: [DONE]\n\n"


def _extract_content_from_sse(chunk: str) -> str:
    stripped = chunk.strip()
    if not stripped.startswith("data:") or stripped == "data: [DONE]":
        return ""

    try:
        payload = json.loads(stripped[5:].strip())
    except json.JSONDecodeError:
        return ""

    choices = payload.get("choices", [])
    if not choices:
        return ""
    delta = choices[0].get("delta", {})
    content = delta.get("content", "")
    return content if isinstance(content, str) else ""


def _parse_openai_request(
    body: dict[str, Any],
    request: Request,
    settings: Settings,
) -> ChatCompletionRequest:
    payload = dict(body)
    model = str(payload.get("model", "gpt-4o-mini"))
    target_adapter = ""
    if "/" in model:
        target_adapter, model = model.split("/", 1)
        payload["model"] = model

    try:
        parsed = ChatCompletionRequest.model_validate(payload)
    except ValidationError as exc:
        raise RequestValidationError("Failed to parse request", details=str(exc)) from exc

    parsed.target_adapter = target_adapter

    incoming_auth = request.headers.get("Authorization", "")
    explicit_upstream_auth = request.headers.get("X-Upstream-Authorization", "")
    if explicit_upstream_auth:
        parsed.metadata["upstream_authorization"] = explicit_upstream_auth
    elif incoming_auth and (
        not settings.api_key or incoming_auth != f"Bearer {settings.api_key}"
    ):
        parsed.metadata["upstream_authorization"] = incoming_auth

    return parsed


def _format_openai_response(response: ChatCompletionResponse) -> dict[str, Any]:
    return response.model_dump(exclude_none=True, exclude={"metadata"})


def _apply_aura_headers(response: Response, state: PipelineState) -> None:
    response.headers["X-AuraBook-Gradient"] = str(
        state.extras.get("community_gradient", "pass_through")
    )
    response.headers["X-AuraBook-Source"] = str(state.extras.get("community_source", "none"))
    response.headers["X-AuraBook-Cache"] = str(state.extras.get("community_cache", "miss"))
    response.headers["X-AuraBook-PII-Redactions"] = str(state.extras.get("pii_redactions", 0))
    trajectory_id = (
        state.extras.get("community_trajectory_id")
        or (state.response.metadata.get("trajectory_id") if state.response is not None else None)
    )
    if trajectory_id:
        response.headers["X-AuraBook-Trajectory"] = str(trajectory_id)
    note_id = (
        state.extras.get("community_note_id")
        or (state.response.metadata.get("note_id") if state.response is not None else None)
    )
    if note_id:
        response.headers["X-AuraBook-Note"] = str(note_id)
