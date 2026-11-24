"""Optional OpenTelemetry tracing.

If the OTel SDK is installed and SF_OTEL_ENDPOINT is set, spans are
exported via OTLP/HTTP. Otherwise this module degrades to a no-op so the
service runs in lean environments. Trace ids are also written to logs
when available (via the logging formatter extra fields).
"""
from __future__ import annotations

import contextlib
import logging
import time

log = logging.getLogger(__name__)

_tracer = None
_traced = False


def setup_tracing(endpoint: str | None) -> None:
    global _tracer, _traced
    if not endpoint:
        log.info("tracing disabled: no OTEL endpoint configured")
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint + "/v1/traces"))
        )
        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("songforge")
        _traced = True
        log.info("tracing enabled -> %s", endpoint)
    except Exception:  # pragma: no cover - otel optional
        log.warning("OpenTelemetry SDK not available; tracing disabled", exc_info=True)
        _tracer = None


@contextlib.contextmanager
def job_span(job_id: str, stage: str):
    """Context manager: real OTel span when enabled, no-op otherwise."""
    if _tracer is not None:  # pragma: no cover - depends on optional SDK
        with _tracer.start_as_current_span(f"job.{job_id}.stage.{stage}"):
            yield
    else:
        t0 = time.monotonic()
        try:
            yield
        finally:
            log.debug("span(job=%s stage=%s) %.2fs", job_id, stage, time.monotonic() - t0)
