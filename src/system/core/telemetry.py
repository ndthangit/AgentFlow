"""Shared logging, tracing, and metrics bootstrap for AgentFlow processes."""

from __future__ import annotations

import atexit
import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

_configured = False
_instruments: dict[str, Any] = {}
_SECRET_PATTERN = re.compile(
    r"(?i)(authorization|api[_-]?key|token|password)(\s*[:=]\s*)([^\s,;]+)"
)


def _redact(value: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2[REDACTED]", value)


class JsonFormatter(logging.Formatter):
    """Emit a bounded, correlation-friendly JSON record to stdout."""

    def format(self, record: logging.LogRecord) -> str:
        span_context = trace.get_current_span().get_span_context()
        document: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "service": os.getenv("OTEL_SERVICE_NAME", "agentflow-system"),
            "environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
            "logger": record.name,
            "message": _redact(record.getMessage())[:16_384],
        }
        if span_context.is_valid:
            document["trace_id"] = format(span_context.trace_id, "032x")
            document["span_id"] = format(span_context.span_id, "016x")
        for field in ("event", "run_id", "node_id", "status", "error_code"):
            value = getattr(record, field, None)
            if value is not None:
                document[field] = str(value)
        if record.exc_info:
            document["exception"] = _redact(self.formatException(record.exc_info))[
                :32_768
            ]
        return json.dumps(document, ensure_ascii=False, separators=(",", ":"))


def configure_logging() -> None:
    level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    handler = logging.StreamHandler()
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def _create_instruments() -> None:
    meter = metrics.get_meter("agentflow.runtime")
    _instruments.update(
        {
            "runs_started": meter.create_counter(
                "agentflow_runs_started", description="Durable workflow runs created"
            ),
            "runs_completed": meter.create_counter(
                "agentflow_runs_completed", description="Terminal workflow runs"
            ),
            "run_duration": meter.create_histogram(
                "agentflow_run_duration", unit="s", description="Workflow run duration"
            ),
            "node_attempts": meter.create_counter(
                "agentflow_node_attempts", description="Workflow node attempts"
            ),
            "node_duration": meter.create_histogram(
                "agentflow_node_duration",
                unit="s",
                description="Workflow node duration",
            ),
            "outbox_dispatches": meter.create_counter(
                "agentflow_outbox_dispatches", description="Outbox dispatch attempts"
            ),
            "worker_heartbeat": meter.create_gauge(
                "agentflow_worker_heartbeat_timestamp",
                unit="s",
                description="Last workflow worker heartbeat as Unix time",
            ),
        }
    )


def configure_telemetry(
    service_name: str, *, sqlalchemy_engine: Any | None = None
) -> None:
    """Configure one process once; exporter failure never blocks business work."""
    global _configured
    os.environ.setdefault("OTEL_SERVICE_NAME", service_name)
    configure_logging()
    if _configured:
        return
    _configured = True
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        _create_instruments()
        return

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": os.getenv("AGENTFLOW_VERSION", "0.1.0"),
            "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
        }
    )
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")
    ratio = min(1.0, max(0.0, float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "1.0"))))
    tracer_provider = TracerProvider(
        resource=resource, sampler=ParentBased(TraceIdRatioBased(ratio))
    )
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(tracer_provider)

    interval = int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL", "15000"))
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=endpoint, insecure=True),
        export_interval_millis=interval,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)
    _create_instruments()

    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    if sqlalchemy_engine is not None:
        SQLAlchemyInstrumentor().instrument(engine=sqlalchemy_engine)

    def shutdown() -> None:
        tracer_provider.shutdown()
        meter_provider.shutdown()

    atexit.register(shutdown)


def instrument_fastapi(application: Any) -> None:
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(
        application,
        excluded_urls="/health,/ready",
        http_capture_headers_server_request="",
        http_capture_headers_server_response="",
    )


def record_run_started(trigger_type: str = "api") -> None:
    instrument = _instruments.get("runs_started")
    if instrument:
        instrument.add(1, {"trigger_type": trigger_type})


def record_run_completed(status: str, duration_seconds: float) -> None:
    attributes = {"status": status}
    if instrument := _instruments.get("runs_completed"):
        instrument.add(1, attributes)
    if instrument := _instruments.get("run_duration"):
        instrument.record(duration_seconds, attributes)


def record_node_attempt(
    node_type: str, status: str, duration_seconds: float, error_code: str = "none"
) -> None:
    attributes = {
        "node_type": node_type,
        "status": status,
        "error_code": error_code,
    }
    if instrument := _instruments.get("node_attempts"):
        instrument.add(1, attributes)
    if instrument := _instruments.get("node_duration"):
        instrument.record(duration_seconds, {"node_type": node_type, "status": status})


def record_outbox_dispatch(status: str) -> None:
    if instrument := _instruments.get("outbox_dispatches"):
        instrument.add(1, {"status": status})


def record_worker_heartbeat(worker_pool: str = "default") -> None:
    if instrument := _instruments.get("worker_heartbeat"):
        instrument.set(datetime.now(UTC).timestamp(), {"worker_pool": worker_pool})
