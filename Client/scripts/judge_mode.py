"""Run the AuraBook offline judge/demo story end to end."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.replay_harness import FIXTURE_PATH, run_replay_harness


async def run_judge_mode(fixture_path: Path) -> dict[str, object]:
    """Execute the replay harness and return a compact judge-facing summary."""
    replay = await run_replay_harness(fixture_path)
    scenario_summary = [
        {
            "name": result["name"],
            "gradient": result["headers"]["gradient"],
            "source": result["headers"]["source"],
            "latency_ms": result["latency_ms"],
            "gradient_match": result["gradient_match"],
            "keyword_match": result["keyword_match"],
        }
        for result in replay["scenarios"]
    ]
    return {
        "summary": scenario_summary,
        "success_capture": replay["success_capture"],
    }


def main() -> None:
    """CLI entry point for the judge-mode demo."""
    parser = argparse.ArgumentParser(description="Run the AuraBook judge-mode offline demo.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH, help="Scenario fixture path.")
    args = parser.parse_args()

    result = asyncio.run(run_judge_mode(args.fixture))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
