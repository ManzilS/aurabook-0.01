"""Request orchestrator built on top of the AuraBook pipeline."""

from __future__ import annotations

import asyncio
import copy
import logging

from aurabook.core.state import PipelineState

logger = logging.getLogger(__name__)


class Orchestrator:
    """Coordinates pipeline execution and optional fan-out or loop retries."""

    def __init__(self, pipeline, max_loops: int = 1) -> None:
        self.pipeline = pipeline
        self.max_loops = max_loops

    async def run(self, state: PipelineState) -> PipelineState:
        """Execute the full orchestration cycle."""
        state.max_loops = self.max_loops

        while True:
            state = await self._run_pre_middleware(state)
            if state.early_exit:
                return state

            if state.fan_out_targets:
                state = await self._fan_out(state)
            else:
                state = await self.pipeline.dispatch(state)

            if state.early_exit or not state.can_loop:
                return state

            state.increment_loop()
            logger.info("Orchestrator loop %d/%d", state.loop_count, state.max_loops)

    async def _run_pre_middleware(self, state: PipelineState) -> PipelineState:
        for middleware in self.pipeline.pre_middleware:
            state = await middleware.process(state)
            if state.early_exit:
                return state
        return state

    async def _fan_out(self, state: PipelineState) -> PipelineState:
        targets = state.fan_out_targets
        state.fan_out_targets = []

        async def dispatch(adapter_name: str):
            branch = copy.deepcopy(state)
            branch.request.target_adapter = adapter_name
            branch = await self.pipeline.dispatch(branch)
            return branch.response

        results = await asyncio.gather(
            *[dispatch(target) for target in targets],
            return_exceptions=True,
        )

        for target, result in zip(targets, results, strict=False):
            if isinstance(result, Exception):
                logger.error("Fan-out to %s failed", target, exc_info=result)
                continue
            if result is not None:
                state.fan_out_responses.append(result)

        if state.fan_out_responses:
            state.response = state.fan_out_responses[0]
        return state
