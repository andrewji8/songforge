"""Prometheus metrics for the SongForge pipeline.

Exposed at GET /metrics. Keep the cardinality small (stage/status labels).
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, generate_latest

JOBS_TOTAL = Counter(
    "sf_jobs_total", "Jobs finished, by final status and input type.",
    ["status", "input_type"],
)
STAGE_DURATION = Histogram(
    "sf_stage_duration_seconds", "Wall time per pipeline stage.",
    ["stage"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 25, 60, 120, 300),
)
PREVIEW_LATENCY = Histogram(
    "sf_preview_latency_seconds", "Seconds from job start to first playable preview.",
    ["slo_met"],
    buckets=(5, 10, 15, 20, 25, 40, 60, 120),
)
COST_BILLED = Counter("sf_cost_billed_usd", "USD billed per successful stage.", ["stage"])
PROVIDER_ERRORS = Counter("sf_provider_errors_total", "Provider call failures.", ["provider"])
JOBS_IN_FLIGHT = Gauge("sf_jobs_in_flight", "Jobs currently being processed.")


def render_metrics() -> bytes:
    return generate_latest()
