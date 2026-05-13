"""Abstract base classes for AuraBook Cloud Brain storage backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from models.schema import (
    FeedbackRequest,
    IngestResponse,
    NoteRecord,
    NoteSearchResult,
    PrefetchMatch,
    StoredTrajectory,
    TrajectoryIngestRequest,
)


class GraphStore(ABC):
    """Interface for trajectory storage and graph relationships."""

    @abstractmethod
    async def insert_trajectory(self, trajectory: TrajectoryIngestRequest) -> IngestResponse: ...

    @abstractmethod
    async def get_prefetch_match(self, cdc_hash: str) -> PrefetchMatch | None: ...

    @abstractmethod
    async def list_unprocessed(self, *, limit: int = 10) -> list[StoredTrajectory]: ...

    @abstractmethod
    async def list_recent_trajectories(self, *, limit: int = 50) -> list[StoredTrajectory]: ...

    @abstractmethod
    async def mark_processed(self, trajectory_id: str, *, note_model: str) -> None: ...

    @abstractmethod
    async def mark_processing_error(self, trajectory_id: str, *, error_message: str) -> None: ...

    @abstractmethod
    async def delete_older_than(self, *, retention_days: int) -> int: ...


class VectorStore(ABC):
    """Interface for embedded note storage and semantic search."""

    @abstractmethod
    async def upsert_embeddings(self, notes: list[NoteRecord]) -> int: ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        query_text: str = "",
        settings: Any | None = None,
        limit: int = 10,
        match_threshold: float = 0.75,
    ) -> list[NoteSearchResult]: ...

    @abstractmethod
    async def record_matches(self, note_ids: list[str]) -> None: ...

    @abstractmethod
    async def submit_feedback(self, payload: FeedbackRequest) -> None: ...

    @abstractmethod
    async def count(self) -> int: ...

    @abstractmethod
    async def list_recent_notes(self, *, limit: int = 50) -> list[NoteRecord]: ...
