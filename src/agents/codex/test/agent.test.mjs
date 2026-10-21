import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import test from "node:test";

import {
  buildCodexArgs,
  buildMcpConfiguration,
  buildPrompt,
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

test("Codex argv selects the managed Responses provider without embedding its key", () => {
  const args = buildCodexArgs({
    model: environment.AGENT_MODEL,
    baseUrl: environment.AGENT_PROVIDER_BASE_URL,
    schemaPath: "/tmp/schema.json",
    outputPath: "/tmp/output.json",
  });

  assert.equal(args[0], "exec");
  assert.ok(args.includes("--ephemeral"));
  assert.ok(args.includes("--output-schema"));
  assert.ok(args.includes('model_providers.agentflow.env_key="AGENT_PROVIDER_API_KEY"'));
  assert.ok(!args.join(" ").includes(environment.AGENT_PROVIDER_API_KEY));
});

test("prompt includes the workflow task, context, and assigned skill", () => {
  const prompt = buildPrompt(request);
  assert.match(prompt, /Create a plan/);
  assert.match(prompt, /runtime/);
  assert.match(prompt, /Be concise/);
  assert.match(prompt, /Required JSON output schema/);
  assert.match(prompt, /"required":\["summary"\]/);
});

test("MCP configuration uses environment-backed headers without putting secrets in argv", () => {
  const raw = JSON.stringify({
    "filesystem-demo": {
      transport: "streamable_http",
      url: "http://filesystem-mcp:8002/mcp",
      headers: {
        Authorization: "Bearer managed-secret",
        "X-Tenant": "tenant-1",
      },
    },
  });
  const mcp = buildMcpConfiguration(raw, { ...environment, AGENT_MCP_SERVERS: raw });
  const serializedArgs = mcp.args.join(" ");

  assert.match(serializedArgs, /mcp_servers\.filesystem-demo\.url/);
  assert.match(serializedArgs, /mcp_servers\.filesystem-demo\.required=true/);
  assert.match(serializedArgs, /env_http_headers/);
  assert.ok(!serializedArgs.includes("managed-secret"));
  assert.equal(mcp.environment.AGENTFLOW_MCP_0_HEADER_0, "Bearer managed-secret");
  assert.equal(mcp.environment.AGENTFLOW_MCP_0_HEADER_1, "tenant-1");
  assert.equal(mcp.environment.AGENT_MCP_SERVERS, undefined);
  assert.deepEqual(mcp.serverNames, ["filesystem-demo"]);

  const codexArgs = buildCodexArgs({
    model: environment.AGENT_MODEL,
    baseUrl: environment.AGENT_PROVIDER_BASE_URL,
    schemaPath: "/tmp/schema.json",
    outputPath: "/tmp/output.json",
    mcpArgs: mcp.args,
  });
  assert.ok(codexArgs.includes('mcp_servers.filesystem-demo.required=true'));
});

test("MCP configuration rejects the unsupported SSE transport", () => {
  const raw = JSON.stringify({
    legacy: { transport: "sse", url: "https://mcp.example.test/sse" },
  });
  assert.throws(
    () => buildMcpConfiguration(raw, environment),
    (error) => error.code === "CONFIGURATION_ERROR" && /Streamable HTTP/.test(error.message),
  );
});

test("one-shot adapter returns the AgentFlow success envelope", async () => {
  const fakeRunner = async (_binary, args, prompt, env) => {
    assert.equal(env.AGENT_PROVIDER_API_KEY, "managed-secret");
    assert.match(prompt, /Create a plan/);
    const outputPath = args[args.indexOf("--output-last-message") + 1];
    await writeFile(outputPath, JSON.stringify({ summary: "ready" }), "utf8");
    return { code: 0, stdout: "", stderr: "", exceededLimit: false };
  };

  const result = await executeRequest(request, environment, fakeRunner);
  assert.equal(result.status, "succeeded");
  assert.equal(result.mode, "live");
  assert.deepEqual(result.output, { summary: "ready" });
});

test("adapter rejects a run without a Models-managed key", async () => {
  await assert.rejects(
    executeRequest(request, { ...environment, AGENT_PROVIDER_API_KEY: "" }),
    /Select an enabled provider and model/,
  );
});

test("adapter maps an incompatible model response to a public error", async () => {
  const fakeRunner = async () => ({
    code: 1,
    stdout: "",
    stderr: "HTTP 400: unsupported model",
    exceededLimit: false,
  });

  await assert.rejects(
    executeRequest(request, environment, fakeRunner),
    (error) => error.code === "MODEL_REQUEST_REJECTED"
      && !error.message.includes("HTTP 400")
      && error.detail === "HTTP 400: unsupported model",
  );
});

test("adapter maps a Codex transport failure to provider unavailable", async () => {
  const fakeRunner = async () => ({
    code: 1,
    stdout: "",
    stderr: "stream disconnected before completion: error sending request for url",
    exceededLimit: false,
  });

  await assert.rejects(
    executeRequest(request, environment, fakeRunner),
    (error) => error.code === "PROVIDER_UNAVAILABLE" && !error.message.includes("request for url"),
  );
});

test("adapter maps a required MCP startup failure to a public MCP error", async () => {
  const fakeRunner = async () => ({
    code: 1,
    stdout: "",
    stderr: "Required MCP server filesystem-demo failed to initialize",
    exceededLimit: false,
  });

  await assert.rejects(
    executeRequest(request, environment, fakeRunner),
    (error) => error.code === "MCP_CONNECTION_FAILED" && !error.message.includes("filesystem-demo"),
  );
});

test("adapter maps provider credit exhaustion without exposing provider details", async () => {
  const fakeRunner = async () => ({
    code: 1,
    stdout: "",
    stderr: "402 Payment Required: more credits needed at provider account 123",
    exceededLimit: false,
  });

  await assert.rejects(
    executeRequest(request, environment, fakeRunner),
    (error) => error.code === "RATE_LIMITED"
      && !error.message.includes("account 123")
      && error.detail.includes("402 Payment Required")
      && error.detail.includes("account [REDACTED]")
      && !error.detail.includes("account 123"),
  );
});

test("adapter preserves an unknown Codex failure with secrets redacted", async () => {
  const fakeRunner = async () => ({
    code: 1,
    stdout: "",
    stderr: "compiler crashed while using managed-secret",
    exceededLimit: false,
  });

  await assert.rejects(
    executeRequest(request, environment, fakeRunner),
    (error) => error.code === "AGENT_FAILED"
      && error.detail === "compiler crashed while using [REDACTED]",
  );
});
