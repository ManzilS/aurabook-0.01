"""AuraBook middleware pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aurabook.core.state import PipelineState
from aurabook.utils.errors import AdapterError, AdapterNotFoundError, AuraBookError

if TYPE_CHECKING:
    from aurabook.adapters.base import AdapterBase
    from aurabook.middleware.base import MiddlewareBase

logger = logging.getLogger(__name__)


class Pipeline:
    """Ordered execution of pre-middleware, adapter dispatch, and post-middleware."""

    def __init__(
        self,
        *,
        pre_middleware: list[MiddlewareBase] | None = None,
        post_middleware: list[MiddlewareBase] | None = None,
        adapters: dict[str, AdapterBase] | None = None,
        default_adapter: str = "openai_proxy",
    ) -> None:
        self.pre_middleware = pre_middleware or []
        self.post_middleware = post_middleware or []
        self.adapters = adapters or {}
        self.default_adapter = default_adapter

    async def execute(self, state: PipelineState) -> PipelineState:
        """Run pre-middleware and then dispatch to the selected adapter."""
        for middleware in self.pre_middleware:
            logger.debug("Running pre-middleware: %s", middleware.name)
            state = await middleware.process(state)
            if state.early_exit:
                logger.info("Early exit triggered by %s", middleware.name)
                return state
        return await self.dispatch(state)

    async def dispatch(self, state: PipelineState) -> PipelineState:
        """Dispatch the request to an adapter and run post-middleware."""
        adapter_name = state.request.target_adapter or self.default_adapter
        adapter = self.adapters.get(adapter_name)
        if adapter is None:
            raise AdapterNotFoundError(
                f"No adapter registered for '{adapter_name}'",
                adapter=adapter_name,
            )

        try:
            state.response = await adapter.send(state.request)
        except AuraBookError:
            raise
        except Exception as exc:
            raise AdapterError(
                f"Adapter '{adapter_name}' failed: {exc}",
                adapter=adapter_name,
            ) from exc

        for middleware in self.post_middleware:
            logger.debug("Running post-middleware: %s", middleware.name)
            state = await middleware.process(state)
            if state.early_exit:
                logger.info("Early exit triggered by %s", middleware.name)
                return state

        return state
