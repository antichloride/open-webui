#!/usr/bin/env python3
"""
Simple performance measurement script for N+1 query problem.

Measures get_all_models() execution time with optional database latency simulation.
"""

import argparse
import asyncio
import sys
import time
from pathlib import Path
from typing import Optional
import json

# Add backend to path
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from open_webui.models.models import Models
from open_webui.models.functions import Functions
from open_webui.utils.models import get_all_models
from open_webui.internal.db import get_db, engine
from sqlalchemy import event
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-5s [%(name)s] %(message)s'
)
log = logging.getLogger(__name__)

# Reduce verbosity from other modules
logging.getLogger("alembic").setLevel(logging.WARNING)
logging.getLogger("open_webui").setLevel(logging.WARNING)
logging.getLogger("langchain_community").setLevel(logging.WARNING)


class LatencyInjector:
    """Inject synthetic latency into SQLAlchemy cursor execution."""

    def __init__(self, latency_ms: float = 0):
        self.latency_ms = latency_ms
        self.active = False
        self._handler = None

    def __enter__(self):
        if self.latency_ms <= 0:
            return self

        def before_cursor_execute(
            conn, cursor, statement, parameters, context, executemany
        ):
            time.sleep(self.latency_ms / 1000.0)

        self._handler = before_cursor_execute
        event.listen(engine, "before_cursor_execute", self._handler)
        self.active = True
        log.info(f"Latency injection: {self.latency_ms}ms per query")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.active and self._handler:
            event.remove(engine, "before_cursor_execute", self._handler)
            self.active = False


class MockRequest:
    """Mock FastAPI request object for testing."""

    def __init__(self):
        self.app = MockApp()


class MockApp:
    """Mock app state."""

    def __init__(self):
        self.state = MockState()


class MockState:
    """Mock app.state."""

    def __init__(self):
        self.MODELS = {}
        self.BASE_MODELS = []
        self.config = MockConfig()


class MockConfig:
    """Mock config."""

    ENABLE_OPENAI_API = False
    ENABLE_OLLAMA_API = False
    ENABLE_BASE_MODELS_CACHE = False
    ENABLE_EVALUATION_ARENA_MODELS = False


async def measure_get_all_models(
    num_runs: int = 3, latency_ms: float = 0
) -> dict:
    """Measure get_all_models() performance."""
    request = MockRequest()
    times = []

    log.info(f"Running {num_runs} measurement runs...")

    for run in range(num_runs):
        with LatencyInjector(latency_ms=latency_ms):
            start = time.perf_counter()
            try:
                models = await get_all_models(request, refresh=True, user=None)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                log.info(f"  Run {run + 1}: {elapsed:.3f}s ({len(models)} models)")
            except Exception as e:
                log.error(f"Error in get_all_models: {e}", exc_info=True)
                times.append(None)

    valid_times = [t for t in times if t is not None]
    if not valid_times:
        return {"error": "All runs failed"}

    return {
        "avg": sum(valid_times) / len(valid_times),
        "min": min(valid_times),
        "max": max(valid_times),
        "runs": times,
        "latency_ms": latency_ms,
    }


async def main():
    parser = argparse.ArgumentParser(
        description="Measure get_all_models() performance"
    )
    parser.add_argument(
        "--latency",
        type=float,
        default=50,
        help="Simulate database latency in milliseconds (default: 50ms)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Number of measurement runs (default: 3)",
    )

    args = parser.parse_args()

    log.info("=" * 70)
    log.info("Performance Measurement: get_all_models()")
    log.info("=" * 70)

    # Check test models exist
    log.info("\nChecking test models...")
    with get_db() as db:
        all_models = Models.get_all_models(db=db)
        test_models = [m for m in all_models if m.id.startswith("test_model_")]
        if not test_models:
            log.error("No test_model_* entries found!")
            log.error("Create them first with: python verify_test_setup.py")
            return 1
        log.info(f"Found {len(test_models)} test models")

    # Measure performance
    log.info(
        f"\nMeasuring with {args.latency}ms latency per query ({args.runs} runs)...\n"
    )

    result = await measure_get_all_models(num_runs=args.runs, latency_ms=args.latency)

    if "error" in result:
        log.error(f"Measurement failed: {result['error']}")
        return 1

    # Print results
    log.info("\n" + "=" * 70)
    log.info("RESULTS")
    log.info("=" * 70)
    log.info(f"Database latency simulated: {result['latency_ms']}ms per query")
    log.info(f"Runs: {args.runs}")
    log.info(f"\n  Average: {result['avg']:.3f}s")
    log.info(f"  Min:     {result['min']:.3f}s")
    log.info(f"  Max:     {result['max']:.3f}s")
    log.info("=" * 70)

    # Save results to JSON
    results_file = Path(__file__).parent / f"measure_results_{int(time.time())}.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "latency_ms": result["latency_ms"],
                "avg_seconds": result["avg"],
                "min_seconds": result["min"],
                "max_seconds": result["max"],
                "runs": result["runs"],
                "timestamp": time.time(),
            },
            f,
            indent=2,
        )
    log.info(f"\nResults saved to: {results_file}\n")

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
