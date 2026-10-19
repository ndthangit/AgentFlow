import {
  context,
  propagation,
  SpanKind,
  SpanStatusCode,
} from "@opentelemetry/api";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-grpc";
import { resourceFromAttributes } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  NodeTracerProvider,
} from "@opentelemetry/sdk-trace-node";
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from "@opentelemetry/semantic-conventions";

const DEFAULT_MAX_IO_BYTES = 65_536;
const MAX_STATUS_MESSAGE_CHARS = 1_000;
const SENSITIVE_KEY = /(?:api[_-]?key|authorization|cookie|password|secret|token)/i;

function telemetryEnabled(env) {
  return env.OTEL_SDK_DISABLED?.toLowerCase() !== "true"
    && env.LANGFUSE_ENABLED?.toLowerCase() !== "false"
    && Boolean(env.OTEL_EXPORTER_OTLP_ENDPOINT?.trim());
}

function redact(value, key = "") {
  if (SENSITIVE_KEY.test(key)) return "[REDACTED]";
  if (Array.isArray(value)) return value.slice(0, 100).map((item) => redact(item));
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value).slice(0, 100).map(([childKey, childValue]) => (
        [childKey, redact(childValue, childKey)]
      )),
    );
  }
  if (typeof value === "string") return value.slice(0, 16_384);
  return value;
}

function parseContext(value) {
  if (typeof value !== "string") return value;
  try {
    return JSON.parse(value);
  } catch {
    return value;
  }
}

function payloadAttribute(value, env) {
  const configured = Number(env.LANGFUSE_MAX_IO_BYTES);
  const maxBytes = Number.isFinite(configured) && configured >= 1_024
    ? configured
    : DEFAULT_MAX_IO_BYTES;
  const serialized = JSON.stringify(redact(value));
  if (Buffer.byteLength(serialized, "utf8") <= maxBytes) return serialized;
  return JSON.stringify({ truncated: true, size_bytes: Buffer.byteLength(serialized, "utf8") });
}

function baseAttributes(env, model) {
  const attributes = {
    "agentflow.langfuse.export": true,
    "langfuse.observation.type": "generation",
    "langfuse.observation.model.name": model,
    "langfuse.environment": env.DEPLOYMENT_ENVIRONMENT || "local",
    "langfuse.trace.name": "workflow.run",
    "openinference.span.kind": "LLM",
    "gen_ai.operation.name": "chat",
    "gen_ai.provider.name": env.AGENT_PROVIDER || "openrouter",
    "gen_ai.request.model": model,
    "agentflow.agent.runtime": "opencode",
  };
  if (env.AGENTFLOW_RUN_ID) {
    attributes["langfuse.session.id"] = env.AGENTFLOW_RUN_ID;
    attributes["langfuse.observation.metadata.run_id"] = env.AGENTFLOW_RUN_ID;
    attributes["langfuse.trace.metadata.run_id"] = env.AGENTFLOW_RUN_ID;
  }
  if (env.AGENTFLOW_NODE_ID) {
    attributes["langfuse.observation.metadata.node_id"] = env.AGENTFLOW_NODE_ID;
    attributes["langfuse.trace.metadata.node_id"] = env.AGENTFLOW_NODE_ID;
  }
  if (env.AGENTFLOW_AGENT_JOB_ID) {
    attributes["langfuse.observation.metadata.agent_job_id"] = env.AGENTFLOW_AGENT_JOB_ID;
  }
  return attributes;
}

function noOpTelemetry() {
  return {
    startGeneration() {
      return {
        addEvent() {},
        succeed() {},
        fail() {},
        end() {},
      };
    },
    async shutdown() {},
  };
}

export function configureOpenCodeTelemetry(env = process.env, onDiagnostic = () => {}) {
  if (!telemetryEnabled(env)) return noOpTelemetry();
  try {
    const exporter = new OTLPTraceExporter({ url: env.OTEL_EXPORTER_OTLP_ENDPOINT });
    const provider = new NodeTracerProvider({
      resource: resourceFromAttributes({
        [ATTR_SERVICE_NAME]: env.OTEL_SERVICE_NAME || "agentflow-agent-runtime",
        [ATTR_SERVICE_VERSION]: "0.1.0",
        "agentflow.agent.runtime": "opencode",
      }),
      spanProcessors: [new BatchSpanProcessor(exporter, {
        scheduledDelayMillis: 250,
        exportTimeoutMillis: 5_000,
      })],
    });
    provider.register();
    const parentContext = propagation.extract(context.active(), {
      traceparent: env.TRACEPARENT,
      tracestate: env.TRACESTATE,
    });
    const tracer = provider.getTracer("agentflow.opencode", "0.1.0");
    return {
      startGeneration({ model, request }) {
        const attributes = baseAttributes(env, model);
        if (env.OPENINFERENCE_HIDE_INPUTS?.toLowerCase() !== "true") {
          attributes["langfuse.observation.input"] = payloadAttribute({
            task: request.task,
            context: parseContext(request.context),
          }, env);
        }
        const span = tracer.startSpan(
          "opencode.session.prompt",
          { kind: SpanKind.CLIENT, attributes },
          parentContext,
        );
        return {
          addEvent(name, eventAttributes = {}) {
            span.addEvent(name, eventAttributes);
          },
          succeed(output) {
            if (env.OPENINFERENCE_HIDE_OUTPUTS?.toLowerCase() !== "true") {
              span.setAttribute("langfuse.observation.output", payloadAttribute(output, env));
            }
            span.setAttribute("langfuse.observation.level", "DEFAULT");
            span.setStatus({ code: SpanStatusCode.OK });
          },
          fail(error) {
            const code = typeof error?.code === "string" ? error.code : "AGENT_FAILED";
            const message = typeof error?.message === "string"
              ? error.message.slice(0, MAX_STATUS_MESSAGE_CHARS)
              : "OpenCode runtime failed.";
            span.setAttribute("error.type", code);
            span.setAttribute("langfuse.observation.level", "ERROR");
            span.setAttribute("langfuse.observation.status_message", message);
            span.setAttribute("langfuse.observation.metadata.error_code", code);
            span.recordException({ name: code, message });
            span.setStatus({ code: SpanStatusCode.ERROR, message: code });
          },
          end() {
            span.end();
          },
        };
      },
      async shutdown() {
        try {
          await provider.forceFlush({ timeoutMillis: 5_000 });
        } catch (error) {
          onDiagnostic("telemetry.flush_failed", {
            error_code: error?.name || "OTEL_FLUSH_FAILED",
          });
        }
        try {
          await provider.shutdown();
        } catch (error) {
          onDiagnostic("telemetry.shutdown_failed", {
            error_code: error?.name || "OTEL_SHUTDOWN_FAILED",
          });
        }
      },
    };
  } catch (error) {
    onDiagnostic("telemetry.init_failed", {
      error_code: error?.name || "OTEL_INIT_FAILED",
    });
    return noOpTelemetry();
  }
}
