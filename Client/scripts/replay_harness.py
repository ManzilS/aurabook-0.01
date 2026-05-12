"""Replay canned AuraBook scenarios against the offline demo runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from httpx import ASGITransport, AsyncClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from aurabook.core.runtime import ApplicationRuntime, build_runtime
from aurabook.server.main import create_app
from aurabook.utils.config import Settings

FIXTURE_PATH = PROJECT_ROOT / "fixtures" / "replay_scenarios.json"


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    """One replay scenario loaded from JSON."""

    name: str
    prompt: str
    max_gradient: str
    expected_gradient: str
    expected_keyword: str
    file_name: str | None = None
    file_content: str | None = None


async def run_replay_harness(
    fixture_path: Path = FIXTURE_PATH,
) -> dict[str, Any]:
    """Execute all replay scenarios and one upload demo flow."""
    scenarios = load_scenarios(fixture_path)
    results = [await run_scenario(scenario) for scenario in scenarios]
    upload_result = await run_success_capture_flow()
    return {
        "scenarios": results,
        "success_capture": upload_result,
    }


def load_scenarios(fixture_path: Path) -> list[ReplayScenario]:
    """Load replay scenarios from disk."""
    raw = json.loads(fixture_path.read_text(encoding="utf-8"))
    return [ReplayScenario(**entry) for entry in raw]


async def run_scenario(scenario: ReplayScenario) -> dict[str, Any]:
    """Run one scenario against a fresh offline runtime."""
    with tempfile.TemporaryDirectory(prefix="aurabook-replay-") as tmpdir:
        root = Path(tmpdir)
        runtime = build_runtime(
            settings=Settings(
                demo_mode=True,
                cloud_demo_mode=True,
                cloud_enabled=True,
                dev_mode=True,
                watch_root=str(root),
                state_dir=str(root / ".aurabook"),
                max_gradient=scenario.max_gradient,
            ),
            emitter=lambda _: None,
        )
        app = create_app(runtime)

        async with app.router.lifespan_context(app):
            if scenario.file_name and scenario.file_content:
                file_path = root / scenario.file_name
                file_path.write_text(scenario.file_content, encoding="utf-8")
                await runtime.watcher.process_path(file_path)

            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                started = perf_counter()
                response = await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "openai_proxy/gpt-4o-mini",
                        "messages": [{"role": "user", "content": scenario.prompt}],
                    },
                )
                latency_ms = round((perf_counter() - started) * 1000, 2)

        payload = response.json()
        body = payload["choices"][0]["message"]["content"]
        headers = {
            "gradient": response.headers.get("X-AuraBook-Gradient", ""),
            "source": response.headers.get("X-AuraBook-Source", ""),
            "cache": response.headers.get("X-AuraBook-Cache", ""),
        }
        return {
            "name": scenario.name,
            "status_code": response.status_code,
            "latency_ms": latency_ms,
            "headers": headers,
            "body_preview": body[:240],
            "expected_gradient": scenario.expected_gradient,
            "gradient_match": headers["gradient"] == scenario.expected_gradient,
            "keyword_match": scenario.expected_keyword.lower() in body.lower(),
        }


async def run_success_capture_flow() -> dict[str, Any]:
    """Run a short flow that demonstrates trajectory capture and local upload."""
    with tempfile.TemporaryDirectory(prefix="aurabook-success-") as tmpdir:
        root = Path(tmpdir)
        settings = Settings(
            demo_mode=True,
            cloud_demo_mode=True,
            cloud_enabled=True,
            dev_mode=True,
            watch_root=str(root),
            state_dir=str(root / ".aurabook"),
            max_gradient="full",
        )
        runtime = build_runtime(settings=settings, emitter=lambda _: None)
        app = create_app(runtime)
        demo_store_path = settings.resolve_demo_store_path()

        before_count = _count_demo_records(demo_store_path)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client:
                await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "openai_proxy/gpt-4o-mini",
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "Fix this blocking sleep inside my async worker.\n\n"
                                    "```python\n"
                                    "import time\n\n"
                                    "async def poll() -> None:\n"
                                    "    time.sleep(2)\n"
                                    "    await fetch_updates()\n"
                                    "```"
                                ),
                            }
                        ],
                    },
                )
                await client.post(
                    "/v1/chat/completions",
                    json={
                        "model": "openai_proxy/gpt-4o-mini",
                        "messages": [{"role": "user", "content": "That fixed it, thanks"}],
                    },
                )

        after_count = _count_demo_records(demo_store_path)
        status = runtime.status_store.snapshot()
        return {
            "records_before": before_count,
            "records_after": after_count,
            "new_record_created": after_count > before_count,
            "last_upload": None if status.last_upload is None else status.last_upload.model_dump(mode="json"),
        }


def _count_demo_records(store_path: Path) -> int:
    if not store_path.exists():
        return 0
    raw = json.loads(store_path.read_text(encoding="utf-8"))
    return len(raw.get("records", []))


def main() -> None:
    """CLI entry point for the replay harness."""
    parser = argparse.ArgumentParser(description="Run the AuraBook offline replay harness.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=FIXTURE_PATH,
        help="Optional scenario fixture path.",
    )
    args = parser.parse_args()

    result = asyncio.run(run_replay_harness(args.fixture))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
