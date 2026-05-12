"""Offline demo implementation of the AuraBook Cloud Brain client."""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from threading import Lock
from typing import Any

from aurabook.core.models import (
    CommunityFeedbackRequest,
    CommunityFeedbackResponse,
    CommunityPrefetchMatch,
    CommunityPrefetchResponse,
    CommunitySearchResponse,
    CommunitySearchResult,
    TrajectoryUploadPayload,
)
from aurabook.utils.config import Settings
from aurabook.watcher.cdc import cdc_digest

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True, slots=True)
class DemoMemoryRecord:
    """One local demo memory entry."""

    trajectory_id: str
    cdc_hash: str
    original_goal: str
    original_prompt: str
    error_log: str
    final_fix: str
    note_text: str
    ai_reflection: str
    source_model: str = "aurabook-demo"
    helpful_count: int = 0
    unhelpful_count: int = 0
    suppress_count: int = 0

    def searchable_text(self) -> str:
        """Return searchable text for the demo entry."""
        return "\n\n".join(
            (
                self.original_goal,
                self.original_prompt,
                self.error_log,
                self.note_text,
                self.ai_reflection,
                self.final_fix,
            )
        )


def _seed_records() -> list[DemoMemoryRecord]:
    """Return the built-in demo memory records."""
    async_fix = (
        "```python\n"
        "import httpx\n\n"
        "async def fetch_data(url: str) -> dict:\n"
        "    async with httpx.AsyncClient() as client:\n"
        "        response = await client.get(url)\n"
        "        response.raise_for_status()\n"
        "        return response.json()\n"
        "```"
    )
    cors_fix = (
        "```python\n"
        "from fastapi import FastAPI\n"
        "from fastapi.middleware.cors import CORSMiddleware\n\n"
        "app = FastAPI()\n"
        "app.add_middleware(\n"
        "    CORSMiddleware,\n"
        "    allow_origins=['http://localhost:3000'],\n"
        "    allow_methods=['*'],\n"
        "    allow_headers=['*'],\n"
        ")\n"
        "```"
    )
    pandas_fix = (
        "```python\n"
        "mask = df['active']\n"
        "df.loc[mask, 'price'] = df.loc[mask, 'price'].fillna(0).astype(float)\n"
        "```"
    )
    react_fix = (
        "```tsx\n"
        "useEffect(() => {\n"
        "  let cancelled = false;\n"
        "  fetch(`/api/notes?project=${projectId}`)\n"
        "    .then((r) => r.json())\n"
        "    .then((payload) => {\n"
        "      if (!cancelled) {\n"
        "        setNotes(payload);\n"
        "      }\n"
        "    });\n"
        "  return () => {\n"
        "    cancelled = true;\n"
        "  };\n"
        "}, [projectId]);\n"
        "```"
    )

    seed_payloads = (
        DemoMemoryRecord(
            trajectory_id="demo-async-httpx",
            cdc_hash=cdc_digest("response = requests.get(url)\n"),
            original_goal="Make async HTTP requests without blocking the event loop.",
            original_prompt="My async request is blocking the event loop.",
            error_log="RuntimeError: blocking call detected inside async function",
            final_fix=async_fix,
            note_text=(
                "System Note: Do not use the requests library inside async code; "
                "use httpx.AsyncClient so the event loop stays non-blocking."
            ),
            ai_reflection=(
                "The failure came from using a blocking HTTP client in async code, "
                "which stalled the event loop and hid backpressure problems."
            ),
            helpful_count=4,
        ),
        DemoMemoryRecord(
            trajectory_id="demo-fastapi-cors",
            cdc_hash=cdc_digest("app.add_middleware(CORSMiddleware)\n"),
            original_goal="Enable localhost CORS in a FastAPI app.",
            original_prompt="Fix this FastAPI CORS middleware setup.",
            error_log="NameError: name 'CORSMiddleware' is not defined",
            final_fix=cors_fix,
            note_text=(
                "System Note: Import CORSMiddleware from fastapi.middleware.cors "
                "before registering it on the app."
            ),
            ai_reflection=(
                "The middleware registration failed immediately because the symbol "
                "was never imported into the module."
            ),
            helpful_count=3,
        ),
        DemoMemoryRecord(
            trajectory_id="demo-pandas-loc",
            cdc_hash=cdc_digest("active = df[df['active']]\nactive['price'] = 0\n"),
            original_goal="Safely update filtered pandas rows.",
            original_prompt="Fix this pandas SettingWithCopyWarning.",
            error_log="SettingWithCopyWarning: A value is trying to be set on a copy of a slice",
            final_fix=pandas_fix,
            note_text=(
                "System Note: When updating filtered pandas rows, write back through "
                "df.loc[...] instead of mutating a sliced view."
            ),
            ai_reflection=(
                "The attempted mutation targeted a temporary slice instead of the owning "
                "DataFrame, so pandas could not guarantee a safe write."
            ),
            helpful_count=2,
        ),
        DemoMemoryRecord(
            trajectory_id="demo-react-useeffect",
            cdc_hash=cdc_digest("useEffect(() => { setNotes(notes); }, [notes]);\n"),
            original_goal="Fetch notes when the selected React project changes.",
            original_prompt="This React hook keeps re-rendering forever.",
            error_log="Warning: Maximum update depth exceeded",
            final_fix=react_fix,
            note_text=(
                "System Note: Do not include state that you set inside the effect "
                "dependency list unless the effect truly depends on it."
            ),
            ai_reflection=(
                "The effect depended on state that it also updated, creating a render "
                "feedback loop."
            ),
            helpful_count=2,
        ),
    )
    return list(seed_payloads)


class DemoCloudBrainClient:
    """Local JSON-backed Cloud Brain client used for demos and offline testing."""

    def __init__(
        self,
        settings: Settings,
        *,
        client_id: str,
        store_path: Path | None = None,
    ) -> None:
        self._settings = settings
        self._client_id = client_id
        self._store_path = store_path or settings.resolve_demo_store_path()
        self._lock = Lock()
        self._ensure_store()

    async def prefetch(self, cdc_hash: str) -> CommunityPrefetchResponse:
        """Return a demo exact-match response."""
        for record in self._load_records():
            if record.cdc_hash != cdc_hash:
                continue
            return CommunityPrefetchResponse(
                found=True,
                match=CommunityPrefetchMatch(
                    trajectory_id=record.trajectory_id,
                    cdc_hash=record.cdc_hash,
                    note_id=f"note-{record.trajectory_id}",
                    original_goal=record.original_goal,
                    final_fix=record.final_fix,
                    note_text=record.note_text,
                    source_model=record.source_model,
                    ai_reflection=record.ai_reflection,
                ),
            )
        return CommunityPrefetchResponse(found=False, match=None)

    async def search(
        self,
        *,
        prompt: str,
        error_log: str | None = None,
        limit: int = 5,
        match_threshold: float | None = None,
    ) -> CommunitySearchResponse:
        """Return demo semantic matches using token-overlap similarity."""
        query = "\n\n".join(part for part in (prompt, error_log or "") if part.strip())
        threshold = 0.05 if match_threshold is None else match_threshold
        scored: list[tuple[float, DemoMemoryRecord]] = []
        for record in self._load_records():
            similarity = max(
                _token_similarity(prompt, record.original_prompt),
                _token_similarity(query, record.note_text),
                _token_similarity(query, record.ai_reflection),
                _token_similarity(query, record.error_log),
                _token_similarity(query, record.final_fix),
            )
            if similarity >= threshold:
                scored.append((similarity, record))

        scored.sort(key=lambda item: item[0], reverse=True)
        return CommunitySearchResponse(
            matches=[
                CommunitySearchResult(
                    note_id=f"note-{record.trajectory_id}",
                    trajectory_id=record.trajectory_id,
                    note_text=record.note_text,
                    source_model=record.source_model,
                    similarity=round(score, 4),
                    metadata={
                        "client_id": self._client_id,
                        "demo": True,
                        "match_count": record.helpful_count + record.unhelpful_count + record.suppress_count,
                    },
                    original_prompt=record.original_prompt,
                    final_fix=record.final_fix,
                    ai_reflection=record.ai_reflection,
                    helpful_count=record.helpful_count,
                    unhelpful_count=record.unhelpful_count,
                    suppress_count=record.suppress_count,
                )
                for score, record in scored[:limit]
            ]
        )

    async def ingest(self, payload: TrajectoryUploadPayload) -> dict[str, Any]:
        """Persist a successful trajectory into the local demo memory file."""
        with self._lock:
            records = self._load_records_locked()
            fingerprint = _normalize_text(
                "\n".join(
                    (
                        payload.original_prompt,
                        payload.error_log,
                        payload.final_fix,
                    )
                )
            )
            for record in records:
                existing_fingerprint = _normalize_text(
                    "\n".join((record.original_prompt, record.error_log, record.final_fix))
                )
                if payload.cdc_hash and payload.cdc_hash == record.cdc_hash:
                    return {"trajectory_id": record.trajectory_id, "created": False}
                if fingerprint == existing_fingerprint:
                    return {"trajectory_id": record.trajectory_id, "created": False}

            trajectory_id = uuid.uuid4().hex
            record = DemoMemoryRecord(
                trajectory_id=trajectory_id,
                cdc_hash=payload.cdc_hash or cdc_digest(payload.final_fix),
                original_goal=payload.original_goal or "Community supplied coding repair.",
                original_prompt=payload.original_prompt,
                error_log=payload.error_log,
                final_fix=payload.final_fix,
                note_text=_note_from_payload(payload),
                ai_reflection=payload.ai_reflection or payload.final_fix[:240],
                source_model="aurabook-demo-upload",
            )
            records.append(record)
            self._write_records_locked(records)
            logger.info("Stored offline demo trajectory %s", trajectory_id)
            return {"trajectory_id": trajectory_id, "created": True}

    async def submit_feedback(
        self,
        payload: CommunityFeedbackRequest,
    ) -> CommunityFeedbackResponse:
        """Apply one offline feedback event to the demo memory store."""
        if payload.trajectory_id is None and payload.note_id is None:
            return CommunityFeedbackResponse(accepted=False, feedback=payload.feedback)
        target_trajectory_id = payload.trajectory_id
        if target_trajectory_id is None and payload.note_id is not None:
            target_trajectory_id = payload.note_id.removeprefix("note-")
        with self._lock:
            records = self._load_records_locked()
            for index, record in enumerate(records):
                if record.trajectory_id != target_trajectory_id:
                    continue
                if payload.feedback.value == "helpful":
                    records[index] = replace(record, helpful_count=record.helpful_count + 1)
                elif payload.feedback.value == "wrong":
                    records[index] = replace(record, unhelpful_count=record.unhelpful_count + 1)
                else:
                    records[index] = replace(record, suppress_count=record.suppress_count + 1)
                self._write_records_locked(records)
                break
        return CommunityFeedbackResponse(
            accepted=True,
            note_id=payload.note_id,
            trajectory_id=target_trajectory_id,
            feedback=payload.feedback,
        )

    async def close(self) -> None:
        """No-op close method for API parity."""
        return None

    def _ensure_store(self) -> None:
        with self._lock:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            if self._store_path.exists():
                return
            self._write_records_locked(_seed_records())

    def _load_records(self) -> list[DemoMemoryRecord]:
        with self._lock:
            return self._load_records_locked()

    def _load_records_locked(self) -> list[DemoMemoryRecord]:
        raw = json.loads(self._store_path.read_text(encoding="utf-8"))
        return [DemoMemoryRecord(**entry) for entry in raw.get("records", [])]

    def _write_records_locked(self, records: list[DemoMemoryRecord]) -> None:
        payload = {"records": [asdict(record) for record in records]}
        self._store_path.write_text(
            json.dumps(payload, indent=2),
            encoding="utf-8",
        )


def _normalize_text(text: str) -> str:
    return " ".join(_TOKEN_PATTERN.findall(text.lower()))


def _token_similarity(left: str, right: str) -> float:
    left_tokens = set(_TOKEN_PATTERN.findall(left.lower()))
    right_tokens = set(_TOKEN_PATTERN.findall(right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    intersection = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    jaccard = intersection / union
    overlap = intersection / min(len(left_tokens), len(right_tokens))
    return max(jaccard, overlap)


def _note_from_payload(payload: TrajectoryUploadPayload) -> str:
    if payload.ai_reflection:
        reflection = payload.ai_reflection.strip().rstrip(".")
        return f"System Note: {reflection}."
    return "System Note: Reuse the previously successful repair pattern for this failure mode."
