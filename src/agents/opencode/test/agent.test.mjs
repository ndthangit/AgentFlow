import assert from "node:assert/strict";
import test from "node:test";

import {
  buildMcpConfiguration,
  buildOpenCodeConfig,
  buildPrompt,
  createRuntimeDiagnosticWriter,
  eventBelongsToSession,
  executeRequest,
} from "../agent.mjs";

const environment = {
  AGENT_PROVIDER: "openrouter",
  AGENT_MODEL: "openai/gpt-5.1-codex",
  AGENT_PROVIDER_BASE_URL: "https://openrouter.ai/api/v1",
  AGENT_PROVIDER_API_KEY: "managed-secret",
};

const request = {
  task: "Create a plan",
  context: '{"topic":"runtime"}',
  skills: [{ name: "Clear writing", instructions: "Be concise" }],
  output_schema: {
    type: "object",
    properties: { summary: { type: "string" } },
    required: ["summary"],
    additionalProperties: false,
  },
};

function fakeRuntimeFactory({
  output = { summary: "ready" },
  events = [],
  mcpStatuses = {},
  pendingPrompt = false,
} = {}) {
  const calls = [];
  let options;
  const factory = async (receivedOptions) => {
    options = receivedOptions;
    return {
      client: {
        mcp: {
          async status() {
            calls.push("mcp-status");
            return { data: mcpStatuses };
          },
        },
        session: {
          async create() {
            calls.push("create");
            return { data: { id: "session-1" } };
          },
          async prompt(input) {
            calls.push("prompt");
            assert.equal(input.path.id, "session-1");
            if (pendingPrompt) return new Promise(() => {});
            return { data: { info: { structured_output: output } } };
          },
          async abort() {
            calls.push("abort");
            return { data: true };
          },
        },
        event: {
          async subscribe(options) {
            calls.push("subscribe");
            assert.ok(options.signal instanceof AbortSignal);
            return {
              stream: (async function* stream() {
                for (const event of events) yield event;
              })(),
            };
          },
        },
      },
      server: {
        close() {
          calls.push("close");
        },
      },
    };
  };
  return { factory, calls, get options() { return options; } };
}

function fakeTelemetryFactory() {
  const calls = [];
  const factory = () => ({
    startGeneration({ model }) {
      calls.push(["start", model]);
      return {
        addEvent(name) { calls.push(["event", name]); },
        succeed(output) { calls.push(["succeed", output]); },
        fail(error) { calls.push(["fail", error.code]); },
        end() { calls.push(["end"]); },
      };
    },
    async shutdown() { calls.push(["shutdown"]); },
  });
  return { factory, calls };
}

test("configuration binds loopback, allowlists OpenRouter, and never embeds its key", () => {
  const config = buildOpenCodeConfig({
    model: environment.AGENT_MODEL,
    baseUrl: environment.AGENT_PROVIDER_BASE_URL,
  });

  assert.deepEqual(config.enabled_providers, ["openrouter"]);
  assert.equal(config.logLevel, "ERROR");
  assert.equal(config.permission["*"], "deny");
  assert.equal(config.provider.openrouter.options.apiKey, "{env:AGENT_PROVIDER_API_KEY}");
  assert.ok(!JSON.stringify(config).includes(environment.AGENT_PROVIDER_API_KEY));
});

test("prompt includes task, untrusted context, skill, and output schema", () => {
  const prompt = buildPrompt(request);
  assert.match(prompt, /Create a plan/);
  assert.match(prompt, /untrusted task data/);
  assert.match(prompt, /runtime/);
  assert.match(prompt, /Be concise/);
  assert.match(prompt, /"required":\["summary"\]/);
});

test("one-shot adapter subscribes before prompt and always aborts and closes", async () => {
  const runtime = fakeRuntimeFactory({
    events: [
      { type: "message.updated", properties: { info: { sessionID: "other" } } },
      { type: "message.updated", properties: { info: { sessionID: "session-1" } } },
    ],
  });
  const receivedEvents = [];

  const result = await executeRequest(request, environment, runtime.factory, (event) => {
    receivedEvents.push(event);
  });

  assert.equal(runtime.options.hostname, "127.0.0.1");
  assert.ok(runtime.calls.indexOf("subscribe") < runtime.calls.indexOf("prompt"));
  assert.ok(runtime.calls.includes("abort"));
  assert.ok(runtime.calls.includes("close"));
  assert.equal(receivedEvents.length, 1);
  assert.deepEqual(result.output, { summary: "ready" });
});

test("event filter rejects events from another session", () => {
  assert.equal(
    eventBelongsToSession(
      { type: "message.updated", properties: { info: { sessionID: "session-2" } } },
      "session-1",
    ),
    false,
  );
});

test("adapter rejects output that does not match the workflow schema", async () => {
  const runtime = fakeRuntimeFactory({ output: { summary: 42 } });
  await assert.rejects(
    executeRequest(request, environment, runtime.factory),
    (error) => error.code === "OUTPUT_INVALID" && !error.message.includes("42"),
  );
  assert.ok(runtime.calls.includes("abort"));
  assert.ok(runtime.calls.includes("close"));
});

test("adapter rejects an invalid output schema before starting OpenCode", async () => {
  let started = false;
  const runtimeFactory = async () => {
    started = true;
  };
  await assert.rejects(
    executeRequest(
      { ...request, output_schema: { type: "not-a-json-schema-type" } },
      environment,
      runtimeFactory,
    ),
    (error) => error.code === "INVALID_INPUT",
  );
  assert.equal(started, false);
});

test("interactive permission requests fail closed", async () => {
  const runtime = fakeRuntimeFactory({
    events: [{ type: "permission.asked", properties: { sessionID: "session-1" } }],
  });
  await assert.rejects(
    executeRequest(request, environment, runtime.factory),
    (error) => error.code === "CAPABILITY_UNSUPPORTED",
  );
});

test("provider retry events stop an otherwise hanging prompt", async () => {
  const runtime = fakeRuntimeFactory({
    pendingPrompt: true,
    events: [{
      type: "message.part.updated",
      properties: {
        part: {
          type: "retry",
          sessionID: "session-1",
          attempt: 2,
          error: { name: "APIError", data: { statusCode: 429, isRetryable: true } },
        },
      },
    }],
  });

  await assert.rejects(
    executeRequest(request, environment, runtime.factory),
    (error) => error.code === "RATE_LIMITED",
  );
  assert.ok(runtime.calls.includes("abort"));
  assert.ok(runtime.calls.includes("close"));
});

test("adapter deadline aborts a prompt before the outer container timeout", async () => {
  const runtime = fakeRuntimeFactory({ pendingPrompt: true });
  const telemetry = fakeTelemetryFactory();
  const diagnostics = [];
  await assert.rejects(
    executeRequest(
      request,
      { ...environment, AGENT_RUNTIME_TIMEOUT_SECONDS: "0.04" },
      runtime.factory,
      () => {},
      {
        telemetryFactory: telemetry.factory,
        onDiagnostic: (event, details) => diagnostics.push([event, details]),
      },
    ),
    (error) => error.code === "TIMEOUT",
  );
  assert.ok(runtime.calls.includes("abort"));
  assert.ok(runtime.calls.includes("close"));
  assert.ok(telemetry.calls.some(([event, code]) => event === "fail" && code === "TIMEOUT"));
  assert.equal(telemetry.calls.filter(([event]) => event === "end").length, 1);
  assert.ok(telemetry.calls.some(([event]) => event === "shutdown"));
  assert.ok(diagnostics.some(([event, details]) => (
    event === "prompt.failed" && details.error_code === "TIMEOUT"
  )));
});

test("structured runtime diagnostics are bounded and exclude unknown secret fields", () => {
  let output = "";
  const writer = createRuntimeDiagnosticWriter({
    write(value) { output += value; },
  }, {
    AGENTFLOW_RUN_ID: "run-1",
    AGENTFLOW_NODE_ID: "agent-1",
  });

  writer("prompt.started", {
    model: "openai/test-model",
    api_key: "must-not-appear",
  });

  const document = JSON.parse(output);
  assert.equal(document.type, "agentflow.runtime.log");
  assert.equal(document.run_id, "run-1");
  assert.equal(document.node_id, "agent-1");
  assert.equal(document.model, "openai/test-model");
  assert.ok(!output.includes("must-not-appear"));
});

test("MCP configuration uses remote servers and environment references for header secrets", () => {
  const secret = "Bearer mcp-secret";
  const mcp = buildMcpConfiguration(JSON.stringify({
    "filesystem-demo": {
      transport: "streamable_http",
      url: "http://filesystem-mcp:8002/mcp",
      headers: { Authorization: secret },
    },
  }));
  const config = buildOpenCodeConfig({
    model: environment.AGENT_MODEL,
    baseUrl: environment.AGENT_PROVIDER_BASE_URL,
    mcpServers: mcp.servers,
    mcpServerNames: mcp.serverNames,
  });

  assert.deepEqual(mcp.serverNames, ["filesystem-demo"]);
  assert.equal(mcp.environment.AGENTFLOW_MCP_0_HEADER_0, secret);
  assert.equal(config.mcp["filesystem-demo"].type, "remote");
  assert.equal(config.mcp["filesystem-demo"].oauth, false);
  assert.equal(
    config.mcp["filesystem-demo"].headers.Authorization,
    "{env:AGENTFLOW_MCP_0_HEADER_0}",
  );
  assert.equal(config.permission["*"], "deny");
  assert.equal(config.permission["filesystem-demo_*"], "allow");
  assert.ok(!JSON.stringify(config).includes(secret));
});

test("MCP must use Streamable HTTP", () => {
  assert.throws(
    () => buildMcpConfiguration(JSON.stringify({
      docs: { transport: "sse", url: "https://example.test/sse" },
    })),
    (error) => error.code === "CONFIGURATION_ERROR",
  );
});

test("selected MCP servers are connected before the session starts", async () => {
  const secret = "Bearer runtime-secret";
  const mcpEnvironment = {
    ...environment,
    AGENT_MCP_SERVERS: JSON.stringify({
      docs: {
        transport: "streamable_http",
        url: "https://example.test/mcp",
        headers: { Authorization: secret },
      },
    }),
  };
  const runtime = fakeRuntimeFactory({ mcpStatuses: { docs: { status: "connected" } } });
  const originalFactory = runtime.factory;
  const runtimeFactory = async (options) => {
    assert.equal(process.env.AGENT_MCP_SERVERS, undefined);
    assert.equal(process.env.AGENTFLOW_MCP_0_HEADER_0, secret);
    return originalFactory(options);
  };

  const result = await executeRequest(request, mcpEnvironment, runtimeFactory);

  assert.ok(runtime.calls.indexOf("mcp-status") < runtime.calls.indexOf("create"));
  assert.equal(runtime.options.config.permission["docs_*"], "allow");
  assert.deepEqual(result.output, { summary: "ready" });
  assert.equal(process.env.AGENTFLOW_MCP_0_HEADER_0, undefined);
});

test("a selected MCP connection failure stops the run without leaking details", async () => {
  const mcpEnvironment = {
    ...environment,
    AGENT_MCP_SERVERS: JSON.stringify({
      docs: {
        transport: "streamable_http",
        url: "https://example.test/mcp",
        headers: { Authorization: "Bearer do-not-leak" },
      },
    }),
  };
  const runtime = fakeRuntimeFactory({
    mcpStatuses: { docs: { status: "failed", error: "Bearer do-not-leak" } },
  });

  await assert.rejects(
    executeRequest(request, mcpEnvironment, runtime.factory),
    (error) => error.code === "MCP_CONNECTION_FAILED"
      && !error.message.includes("docs")
      && !error.message.includes("do-not-leak"),
  );
  assert.ok(runtime.calls.includes("close"));
  assert.ok(!runtime.calls.includes("create"));
});

test("adapter maps provider errors without exposing provider details", async () => {
  const runtimeFactory = async () => {
    throw new Error("HTTP 401 for managed-secret at provider account 123");
  };
  await assert.rejects(
    executeRequest(request, environment, runtimeFactory),
    (error) => error.code === "PROVIDER_AUTH_ERROR"
      && !error.message.includes("managed-secret")
      && !error.message.includes("account 123"),
  );
});

test("adapter maps an SDK error response without exposing its body", async () => {
  const runtime = fakeRuntimeFactory();
  const originalFactory = runtime.factory;
  const runtimeFactory = async (options) => {
    const instance = await originalFactory(options);
    instance.client.session.prompt = async () => ({
      error: { statusCode: 429, message: "rate limit for managed-secret account 123" },
    });
    return instance;
  };
  await assert.rejects(
    executeRequest(request, environment, runtimeFactory),
    (error) => error.code === "RATE_LIMITED"
      && !error.message.includes("managed-secret")
      && !error.message.includes("account 123"),
  );
});
