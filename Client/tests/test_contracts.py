"""Contract tests between the local client and cloud schemas."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from aurabook.core.models import (
    CommunitySearchResponse,
    CommunitySearchResult,
    TrajectoryTurn,
    TrajectoryUploadPayload,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CLOUD_ROOT = PROJECT_ROOT / "aurabook-cloud"
if str(CLOUD_ROOT) not in sys.path:
    sys.path.insert(0, str(CLOUD_ROOT))

schema_spec = importlib.util.spec_from_file_location(
    "aurabook_cloud_schema",
    CLOUD_ROOT / "models" / "schema.py",
)
assert schema_spec is not None and schema_spec.loader is not None
cloud_schema = importlib.util.module_from_spec(schema_spec)
sys.modules["aurabook_cloud_schema"] = cloud_schema
schema_spec.loader.exec_module(cloud_schema)


def test_local_trajectory_payload_validates_against_cloud_ingest_schema() -> None:
    payload = TrajectoryUploadPayload(
        client_id="client-123",
        original_goal="Fix async HTTP usage.",
        original_prompt="My async request is blocking the event loop.",
        broken_code="response = requests.get(url)",
        error_log="RuntimeError: blocking call detected",
        ai_reflection="The failure came from a blocking HTTP client inside async code.",
        final_fix="Use httpx.AsyncClient instead.",
        history=[
            TrajectoryTurn(
                prompt="Attempt 1",
                response="Summary: Replaced requests with httpx.",
                model="gpt-local-test",
                thought="The blocking call comes from requests in async code.",
                tried="Swapped the client and awaited the request.",
                outcome="success",
                commands=["python -m pytest -q"],
                changed_files=["app.py"],
                token_count=480,
                duration_ms=2100,
            )
        ],
        metadata={"community_gradient": "notes"},
    )

    validated = cloud_schema.TrajectoryIngestRequest.model_validate(payload.model_dump(mode="json"))

    assert validated.client_id == "client-123"
    assert validated.final_fix == "Use httpx.AsyncClient instead."
    assert validated.history[0].model == "gpt-local-test"


def test_cloud_search_payload_validates_against_local_search_schema() -> None:
    payload = {
        "matches": [
            {
                "note_id": "note-1",
                "trajectory_id": "traj-1",
                "note_text": "System Note: Use httpx.AsyncClient in async code.",
                "source_model": "gpt-5.4-mini",
                "similarity": 0.91,
                "metadata": {},
                "created_at": "2026-04-23T00:00:00+00:00",
                "original_prompt": "My async request is blocking the event loop.",
                "final_fix": "Use httpx.AsyncClient instead.",
                "ai_reflection": "Blocking clients stall the event loop.",
            }
        ]
    }

    response = CommunitySearchResponse.model_validate(payload)

    assert len(response.matches) == 1
    assert isinstance(response.matches[0], CommunitySearchResult)
