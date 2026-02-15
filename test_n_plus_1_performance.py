#!/usr/bin/env python3
"""
Performance test for N+1 query problem in get_all_models.

This script:
1. Creates a test setup with N models all using the same global filter
2. Measures get_all_models() performance with and without database latency simulation
3. Compares results to show the impact of the batch-loading fix

Usage:
    python test_n_plus_1_performance.py --num-models 10 --latency 50

The script works by:
- Creating models directly in the database via Python (no HTTP needed)
- Optionally injecting synthetic latency to simulate PostgreSQL over network
- Measuring execution time of get_all_models()
- Comparing before/after the fix via git checkout
"""

import argparse
import asyncio
import sys
import time
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
import subprocess
import json

# Add backend to path
backend_path = Path(__file__).parent / "backend"
sys.path.insert(0, str(backend_path))

from open_webui.models.models import Models, ModelModel, ModelParams, ModelMeta, ModelForm
from open_webui.models.functions import Functions
from open_webui.utils.models import get_all_models
from open_webui.internal.db import get_db, Base, engine
from sqlalchemy.orm import Session
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)-5s [%(name)s] %(message)s'
)
log = logging.getLogger(__name__)

# Reduce verbosity from other modules
logging.getLogger("alembic").setLevel(logging.WARNING)
logging.getLogger("open_webui").setLevel(logging.INFO)
logging.getLogger("langchain_community").setLevel(logging.WARNING)


class LatencyInjector:
    """Inject synthetic latency into SQLite queries to simulate PostgreSQL."""

    def __init__(self, latency_ms: float = 0):
        self.latency_ms = latency_ms
        self.original_execute = None
        self.active = False

    def __enter__(self):
        if self.latency_ms <= 0:
            return self

        self.original_execute = sqlite3.Cursor.execute

        def slow_execute(cursor_self, *args, **kwargs):
            time.sleep(self.latency_ms / 1000.0)
            return self.original_execute(cursor_self, *args, **kwargs)

        sqlite3.Cursor.execute = slow_execute
        self.active = True
        log.info(
            f"Latency injection enabled: {self.latency_ms}ms per query"
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.active and self.original_execute:
            sqlite3.Cursor.execute = self.original_execute
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


async def create_test_models(num_models: int, user_id: str = "test_user") -> list[str]:
    """Create N test models in the database, all using a global filter."""
    with get_db() as db:
        # Ensure the global test filter exists (you said you created "example_filter")
        filter_exists = Functions.get_function_by_id("example_filter", db=db)
        if not filter_exists:
            log.warning(
                "Global filter 'example_filter' not found. "
                "Please create it in the UI first with is_global=True"
            )
            return []

        model_ids = []
        for i in range(num_models):
            model_id = f"test_model_{i:03d}"

            # Check if model already exists
            existing = Models.get_model_by_id(model_id, db=db)
            if existing:
                log.info(f"Model {model_id} already exists, skipping creation")
                model_ids.append(model_id)
                continue

            # Create model with metadata that references the global filter
            # Use Pydantic models for form_data
            meta = ModelMeta(
                description=f"Auto-generated test model {i}",
            )
            # Add filterIds to meta dict
            meta_dict = meta.model_dump()
            meta_dict["filterIds"] = ["example_filter"]
            meta = ModelMeta(**meta_dict)

            form_data = ModelForm(
                id=model_id,
                name=f"Test Model {i:03d}",
                base_model_id="echo",
                params=ModelParams(),
                meta=meta,
                is_active=True,
            )

            try:
                model = Models.insert_new_model(form_data, user_id, db=db)
                if model:
                    model_ids.append(model_id)
                    log.info(f"Created model: {model_id}")
                else:
                    log.error(f"Failed to create model {model_id}")
            except Exception as e:
                log.error(f"Error creating model {model_id}: {e}")

        log.info(f"Created {len(model_ids)}/{num_models} test models")
        return model_ids


async def measure_get_all_models(
    num_runs: int = 3, latency_ms: float = 0
) -> dict:
    """Measure get_all_models() performance."""
    request = MockRequest()

    times = []

    for run in range(num_runs):
        with LatencyInjector(latency_ms=latency_ms):
            start = time.perf_counter()
            try:
                models = await get_all_models(request, refresh=True, user=None)
                elapsed = time.perf_counter() - start
                times.append(elapsed)
                log.info(
                    f"Run {run + 1}/{num_runs}: {elapsed:.3f}s "
                    f"({len(models)} models returned)"
                )
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


async def cleanup_test_models():
    """Remove all test_model_* entries from the database."""
    with get_db() as db:
        # Query all models starting with test_model_
        all_models = Models.get_all_models(db=db)
        test_models = [m for m in all_models if m.id.startswith("test_model_")]

        for model in test_models:
            try:
                Models.delete_model_by_id(model.id, db=db)
                log.info(f"Deleted test model: {model.id}")
            except Exception as e:
                log.error(f"Error deleting {model.id}: {e}")

        log.info(f"Cleaned up {len(test_models)} test models")


async def main():
    parser = argparse.ArgumentParser(
        description="Measure N+1 query performance impact in get_all_models()"
    )
    parser.add_argument(
        "--num-models",
        type=int,
        default=10,
        help="Number of test models to create (default: 10)",
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
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete test models after running tests",
    )
    parser.add_argument(
        "--setup-only",
        action="store_true",
        help="Only create test models, don't measure",
    )

    args = parser.parse_args()

    log.info("=" * 70)
    log.info("N+1 Query Performance Test for get_all_models()")
    log.info("=" * 70)

    # Setup test models
    log.info(f"\nStep 1: Creating {args.num_models} test models...")
    model_ids = await create_test_models(args.num_models)

    if not model_ids:
        log.error(
            "Failed to create test models. "
            "Make sure 'example_filter' exists and 'echo' base model is available."
        )
        return 1

    if args.setup_only:
        log.info("\nSetup complete. Run without --setup-only to measure performance.")
        return 0

    # Measure performance
    log.info(
        f"\nStep 2: Measuring get_all_models() with {args.latency}ms latency "
        f"({args.runs} runs)...\n"
    )

    result = await measure_get_all_models(num_runs=args.runs, latency_ms=args.latency)

    if "error" in result:
        log.error(f"Measurement failed: {result['error']}")
        return 1

    # Print results
    log.info("\n" + "=" * 70)
    log.info("RESULTS:")
    log.info("=" * 70)
    log.info(f"Models created: {len(model_ids)}")
    log.info(f"Database latency simulated: {result['latency_ms']}ms per query")
    log.info(f"Measurement runs: {args.runs}")
    log.info(f"\nAverage time: {result['avg']:.3f}s")
    log.info(f"Min time:     {result['min']:.3f}s")
    log.info(f"Max time:     {result['max']:.3f}s")
    log.info("=" * 70)

    # Save results to JSON
    results_file = Path(__file__).parent / "test_results.json"
    with open(results_file, "w") as f:
        json.dump(
            {
                "num_models": len(model_ids),
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
    log.info(f"\nResults saved to: {results_file}")

    if args.cleanup:
        log.info("\nCleaning up test models...")
        await cleanup_test_models()

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
