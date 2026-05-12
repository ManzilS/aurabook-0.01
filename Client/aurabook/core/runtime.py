"""Dependency wiring for the AuraBook local client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence

from aurabook.adapters.anthropic import AnthropicAdapter
from aurabook.adapters.base import AdapterBase
from aurabook.adapters.demo import DemoAdapter
from aurabook.adapters.gemini import GeminiAdapter
from aurabook.adapters.openai_proxy import OpenAIProxyAdapter
from aurabook.core.models import ResponseGradient
from aurabook.core.orchestrator import Orchestrator
from aurabook.core.pipeline import Pipeline
from aurabook.middleware.base import PostMiddleware, PreMiddleware
from aurabook.middleware.community import CommunityContextMiddleware
from aurabook.middleware.pii import PIIScrubberMiddleware
from aurabook.middleware.trajectory import TrajectoryCaptureMiddleware
from aurabook.services.cloud_client import CloudBrainClient
from aurabook.services.community import CommunityContextService
from aurabook.services.demo_cloud import DemoCloudBrainClient
from aurabook.services.ingest_queue import FailedTrajectoryQueue, QueuedUploadWorker
from aurabook.services.identity import ClientIdentityStore
from aurabook.services.memory import CommunityMemoryStore
from aurabook.services.preferences import RepoPolicy, UserPreferencesStore
from aurabook.services.status import RuntimeStatusStore
from aurabook.utils.config import Settings
from aurabook.utils.logging import setup_logging
from aurabook.watcher.prefetch import GhostPrefetchWatcher


class BackgroundService(Protocol):
    """Protocol implemented by startable and stoppable background services."""

    async def start(self) -> None:
        """Start the service."""

    async def stop(self) -> None:
        """Stop the service."""


@dataclass(slots=True)
class ApplicationRuntime:
    """Fully wired application runtime dependencies."""

    settings: Settings
    pipeline: Pipeline
    orchestrator: Orchestrator
    watcher: BackgroundService
    background_services: tuple[BackgroundService, ...]
    cloud_client: CloudBrainClient | DemoCloudBrainClient | None
    community_service: CommunityContextService
    identity_store: ClientIdentityStore
    memory_store: CommunityMemoryStore
    preferences_store: UserPreferencesStore
    status_store: RuntimeStatusStore
    upload_queue: FailedTrajectoryQueue
    repo_policy: RepoPolicy | None


def _build_adapter_registry(
    settings: Settings,
    *,
    override_default_adapter: AdapterBase | None = None,
) -> dict[str, AdapterBase]:
    """Create the named adapter registry for the local proxy."""
    if settings.demo_mode:
        demo = override_default_adapter or DemoAdapter()
        return {
            "openai_proxy": demo,
            "openai": demo,
            "ollama": demo,
            "lmstudio": demo,
            "llmstudio": demo,
            "qwen": demo,
            "claude": demo,
            "anthropic": demo,
            "gemini": demo,
        }

    adapters: dict[str, AdapterBase] = {
        "openai_proxy": override_default_adapter
        or OpenAIProxyAdapter(settings=settings, adapter_name="openai_proxy"),
        "openai": OpenAIProxyAdapter(settings=settings, adapter_name="openai"),
        "ollama": OpenAIProxyAdapter(settings=settings, adapter_name="ollama"),
        "lmstudio": OpenAIProxyAdapter(settings=settings, adapter_name="lmstudio"),
        "llmstudio": OpenAIProxyAdapter(settings=settings, adapter_name="llmstudio"),
        "qwen": OpenAIProxyAdapter(settings=settings, adapter_name="qwen"),
        "claude": AnthropicAdapter(settings=settings, adapter_name="claude"),
        "anthropic": AnthropicAdapter(settings=settings, adapter_name="anthropic"),
        "gemini": GeminiAdapter(settings=settings, adapter_name="gemini"),
    }
    return adapters


def build_runtime(
    settings: Settings | None = None,
    *,
    adapter: AdapterBase | None = None,
    pre_middleware: Sequence[PreMiddleware] | None = None,
    post_middleware: Sequence[PostMiddleware] | None = None,
    watcher: BackgroundService | None = None,
    cloud_client: CloudBrainClient | DemoCloudBrainClient | None = None,
    community_service: CommunityContextService | None = None,
    identity_store: ClientIdentityStore | None = None,
    memory_store: CommunityMemoryStore | None = None,
    emitter=print,
) -> ApplicationRuntime:
    """Construct the runtime dependency graph for the local client."""
    provided_settings = settings
    resolved_settings = settings or Settings()
    preferences_store = UserPreferencesStore(resolved_settings.resolve_preferences_path())
    preferences = preferences_store.load()
    if provided_settings is None or resolved_settings.max_gradient == ResponseGradient.FULL.value:
        resolved_settings.max_gradient = preferences.max_gradient.value
    repo_policy = preferences_store.get_repo_policy(resolved_settings.resolve_watch_root())
    if repo_policy is not None:
        resolved_settings.max_gradient = repo_policy.max_gradient.value
        resolved_settings.cloud_enabled = repo_policy.cloud_enabled
        resolved_settings.upload_enabled = repo_policy.upload_enabled
    setup_logging(
        log_level=resolved_settings.log_level,
        dev_mode=resolved_settings.dev_mode,
    )

    adapter_registry = _build_adapter_registry(
        resolved_settings,
        override_default_adapter=adapter,
    )
    resolved_identity_store = identity_store or ClientIdentityStore(
        resolved_settings.resolve_state_dir()
    )
    status_store = RuntimeStatusStore(resolved_settings.resolve_status_path())
    client_id = resolved_identity_store.get_client_id()
    status_store.initialize(
        client_id=client_id,
        cloud_enabled=resolved_settings.cloud_enabled,
        cloud_demo_mode=resolved_settings.cloud_demo_mode or resolved_settings.demo_mode,
        max_gradient=resolved_settings.max_gradient,
    )
    resolved_memory_store = memory_store or CommunityMemoryStore(
        max_file_hashes=resolved_settings.prefetch_history_max_entries,
        max_search_entries=resolved_settings.search_cache_max_entries,
        status_store=status_store,
        persist_path=(
            resolved_settings.resolve_cache_path()
            if resolved_settings.cache_persist_enabled
            else None
        ),
    )
    resolved_cloud_client = cloud_client
    if resolved_cloud_client is None and resolved_settings.cloud_enabled:
        if resolved_settings.cloud_demo_mode or resolved_settings.demo_mode:
            resolved_cloud_client = DemoCloudBrainClient(
                resolved_settings,
                client_id=client_id,
            )
        else:
            resolved_cloud_client = CloudBrainClient(
                resolved_settings,
                client_id=client_id,
            )

    resolved_community_service = community_service or CommunityContextService(
        resolved_settings,
        cloud_client=resolved_cloud_client,
        memory_store=resolved_memory_store,
        status_store=status_store,
        suppressed_note_ids=() if repo_policy is None else tuple(repo_policy.suppressed_note_ids),
        suppressed_trajectory_ids=(
            () if repo_policy is None else tuple(repo_policy.suppressed_trajectory_ids)
        ),
        repository_root=resolved_settings.resolve_watch_root(),
    )
    upload_queue = FailedTrajectoryQueue(
        resolved_settings.resolve_queue_dir(),
        status_store=status_store,
    )
    trajectory_cloud_client = resolved_cloud_client if resolved_settings.upload_enabled else None
    upload_worker = QueuedUploadWorker(
        queue=upload_queue,
        client=trajectory_cloud_client,
        interval_s=resolved_settings.upload_retry_interval_s,
        batch_size=resolved_settings.upload_retry_batch_size,
        status_store=status_store,
    )

    resolved_pre = (
        list(pre_middleware)
        if pre_middleware is not None
        else [PIIScrubberMiddleware(), CommunityContextMiddleware(resolved_community_service)]
    )
    resolved_post = (
        list(post_middleware)
        if post_middleware is not None
        else [
            TrajectoryCaptureMiddleware(
                emitter=emitter,
                cloud_client=trajectory_cloud_client,
                community_service=resolved_community_service,
                client_id=client_id,
                failed_upload_queue=upload_queue,
                status_store=status_store,
            )
        ]
    )
    resolved_watcher = watcher or GhostPrefetchWatcher(
        root=resolved_settings.resolve_watch_root(),
        extensions=resolved_settings.allowed_extensions(),
        debounce_ms=resolved_settings.watcher_debounce_ms,
        settle_time_ms=resolved_settings.watcher_settle_time_ms,
        emitter=emitter,
        prefetch_handler=resolved_community_service.prefetch_saved_file,
    )

    pipeline = Pipeline(
        pre_middleware=resolved_pre,
        post_middleware=resolved_post,
        adapters=adapter_registry,
        default_adapter=resolved_settings.default_adapter,
    )
    orchestrator = Orchestrator(
        pipeline=pipeline,
        max_loops=resolved_settings.max_loops,
    )

    return ApplicationRuntime(
        settings=resolved_settings,
        pipeline=pipeline,
        orchestrator=orchestrator,
        watcher=resolved_watcher,
        background_services=(upload_worker,),
        cloud_client=resolved_cloud_client,
        community_service=resolved_community_service,
        identity_store=resolved_identity_store,
        memory_store=resolved_memory_store,
        preferences_store=preferences_store,
        status_store=status_store,
        upload_queue=upload_queue,
        repo_policy=repo_policy,
    )
