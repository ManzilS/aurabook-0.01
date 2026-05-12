"""Abstract middleware base classes reused by the AuraBook pipeline."""

from __future__ import annotations

import abc

from aurabook.core.state import PipelineState


class MiddlewareBase(abc.ABC):
    """Common contract shared by pre and post middleware."""

    name: str = "base_middleware"
    phase: str = "unknown"

    @abc.abstractmethod
    async def process(self, state: PipelineState) -> PipelineState:
        """Inspect or mutate the pipeline state."""


class PreMiddleware(MiddlewareBase):
    """Middleware that runs before adapter dispatch."""

    phase = "pre"

    @abc.abstractmethod
    async def process(self, state: PipelineState) -> PipelineState:
        """Mutate the request before it reaches the adapter."""


class PostMiddleware(MiddlewareBase):
    """Middleware that runs after adapter dispatch."""

    phase = "post"

    @abc.abstractmethod
    async def process(self, state: PipelineState) -> PipelineState:
        """Mutate the response after the adapter returns."""
