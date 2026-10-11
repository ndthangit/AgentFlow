import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import test from "node:test";

import { buildCodexArgs, buildPrompt, executeRequest } from "../agent.mjs";

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
    (error) => error.code === "MODEL_REQUEST_REJECTED" && !error.message.includes("HTTP 400"),
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
