"""OpenInference tracing bootstrap for the disposable Agent runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass

from openinference.instrumentation.langchain import LangChainInstrumentor
from opentelemetry import context, trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import extract
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


@dataclass(frozen=True)
class AgentTelemetry:
    provider: TracerProvider
    context_token: object | None


def configure_agent_telemetry() -> AgentTelemetry | None:
    """Export LangChain/DeepAgents steps while preserving the parent workflow trace."""
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not endpoint or os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return None

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.getenv(
                    "OTEL_SERVICE_NAME", "agentflow-agent-runtime"
                ),
                "deployment.environment": os.getenv("DEPLOYMENT_ENVIRONMENT", "local"),
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True))
    )
    trace.set_tracer_provider(provider)
    LangChainInstrumentor().instrument(tracer_provider=provider)

    carrier = {
        key: value
        for key, value in {
            "traceparent": os.getenv("TRACEPARENT"),
            "tracestate": os.getenv("TRACESTATE"),
        }.items()
        if value
    }
    token = context.attach(extract(carrier)) if carrier else None
    return AgentTelemetry(provider=provider, context_token=token)


def shutdown_agent_telemetry(telemetry: AgentTelemetry | None) -> None:
    if telemetry is None:
        return
    if telemetry.context_token is not None:
        context.detach(telemetry.context_token)
    telemetry.provider.shutdown()
