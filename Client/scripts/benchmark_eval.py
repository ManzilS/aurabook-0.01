"""Benchmark and replay evaluation suite for the AuraBook demo runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.replay_harness import FIXTURE_PATH, load_scenarios, run_scenario


async def run_benchmark(iterations: int, fixture_path: Path) -> dict[str, Any]:
    """Run the replay scenarios repeatedly and summarize key metrics."""
    scenarios = load_scenarios(fixture_path)
    all_results: list[dict[str, Any]] = []
    for _ in range(iterations):
        for scenario in scenarios:
            all_results.append(await run_scenario(scenario))

    latencies = [result["latency_ms"] for result in all_results]
    intercepts = [result for result in all_results if result["headers"]["gradient"] == "full"]
    guidance = [
        result
        for result in all_results
        if result["headers"]["gradient"] in {"notes", "guide", "walkthrough"}
    ]
    token_savings = sum(800 for result in intercepts if result["status_code"] == 200)

    return {
        "iterations": iterations,
        "requests": len(all_results),
        "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "p95_latency_ms": round(_percentile(latencies, 0.95), 2) if latencies else 0.0,
        "intercept_rate": round(len(intercepts) / len(all_results), 4) if all_results else 0.0,
        "guidance_rate": round(len(guidance) / len(all_results), 4) if all_results else 0.0,
        "guidance_relevance_rate": round(
            sum(1 for result in guidance if result["keyword_match"]) / len(guidance),
            4,
        )
        if guidance
        else 0.0,
        "estimated_token_savings": token_savings,
        "results": all_results,
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * percentile)))
    return ordered[index]


def main() -> None:
    """CLI entry point for the benchmark suite."""
    parser = argparse.ArgumentParser(description="Run the AuraBook replay benchmark suite.")
    parser.add_argument("--iterations", type=int, default=3, help="Number of full replay passes.")
    parser.add_argument("--fixture", type=Path, default=FIXTURE_PATH, help="Scenario fixture path.")
    args = parser.parse_args()

    result = asyncio.run(run_benchmark(args.iterations, args.fixture))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
