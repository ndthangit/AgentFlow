#!/usr/bin/env node

import { spawn } from "node:child_process";
import { mkdtemp, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
import { randomUUID } from "node:crypto";

const MAX_INPUT_BYTES = 1024 * 1024;
const MAX_CAPTURE_BYTES = 1024 * 1024;
const MAX_MCP_CONFIG_BYTES = 128 * 1024;
const MAX_MCP_SERVERS = 20;
const MCP_SERVER_NAME_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MCP_HEADER_NAME_PATTERN = /^[!#$%&'*+.^_`|~0-9A-Za-z-]+$/;

class PublicAgentError extends Error {
  constructor(code, message) {
    super(message);
    this.code = code;
  }
}

function tomlString(value) {
  return JSON.stringify(value);
}

function tomlInlineStringMap(value) {
  return `{ ${Object.entries(value)
    .map(([key, item]) => `${tomlString(key)} = ${tomlString(item)}`)
    .join(", ")} }`;
}

function requireEnvironment(env, name) {
  const value = env[name]?.trim();
  if (!value) {
    throw new PublicAgentError(
      "CONFIGURATION_ERROR",
      `Agent runtime is missing ${name}. Select an enabled provider and model.`,
    );
  }
  return value;
}

function validateBaseUrl(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    throw new PublicAgentError(
      "CONFIGURATION_ERROR",
      "The selected provider has an invalid API base URL.",
    );
  }
  if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) {
    throw new PublicAgentError(
      "CONFIGURATION_ERROR",
      "The selected provider has an invalid API base URL.",
    );
  }
  return value.replace(/\/+$/, "");
}

export function validateRequest(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PublicAgentError("INVALID_INPUT", "Invalid Agent runtime request.");
  }
  if (typeof value.task !== "string" || !value.task.trim()) {
    throw new PublicAgentError("INVALID_INPUT", "The Agent task must be a non-empty string.");
  }
  if (value.context !== undefined && typeof value.context !== "string") {
    throw new PublicAgentError("INVALID_INPUT", "The Agent context must be a string.");
  }
  if (value.skills !== undefined && !Array.isArray(value.skills)) {
    throw new PublicAgentError("INVALID_INPUT", "The Agent skills must be an array.");
  }
  if (
    value.output_schema === undefined
    || !value.output_schema
    || typeof value.output_schema !== "object"
    || Array.isArray(value.output_schema)
  ) {
    throw new PublicAgentError("INVALID_INPUT", "The Agent output_schema must be a JSON object.");
  }
  return value;
}

export function buildPrompt(request) {
  const skills = (request.skills ?? [])
    .filter((skill) => skill && typeof skill === "object")
    .map((skill) => ({
      name: typeof skill.name === "string" ? skill.name : undefined,
      slug: typeof skill.slug === "string" ? skill.slug : undefined,
      version: skill.version,
      instructions: typeof skill.instructions === "string" ? skill.instructions : undefined,
    }));
  return [
    "Complete the AgentFlow task below.",
    "Return exactly one JSON object matching the supplied output schema.",
    "Do not wrap the result in Markdown and do not include commentary outside the JSON object.",
    "",
    "Task:",
    request.task,
    "",
    "Context:",
    request.context || "{}",
    "",
    "Assigned skills:",
    JSON.stringify(skills),
    "",
    "Required JSON output schema:",
    JSON.stringify(request.output_schema),
  ].join("\n");
}

export function buildMcpConfiguration(rawValue, baseEnvironment = {}) {
  const environment = { ...baseEnvironment };
  delete environment.AGENT_MCP_SERVERS;
  if (!rawValue?.trim()) return { args: [], environment, serverNames: [] };
  if (Buffer.byteLength(rawValue, "utf8") > MAX_MCP_CONFIG_BYTES) {
    throw new PublicAgentError(
      "CONFIGURATION_ERROR",
      "MCP server configuration exceeds 128 KiB.",
    );
  }

  let connections;
  try {
    connections = JSON.parse(rawValue);
  } catch {
    throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server configuration is invalid JSON.");
  }
  if (
    !connections
    || typeof connections !== "object"
    || Array.isArray(connections)
    || Object.keys(connections).length > MAX_MCP_SERVERS
  ) {
    throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server configuration is invalid.");
  }

  const args = [];
  const serverNames = [];
  for (const [name, connection] of Object.entries(connections)) {
    if (
      !MCP_SERVER_NAME_PATTERN.test(name)
      || !connection
      || typeof connection !== "object"
      || Array.isArray(connection)
    ) {
      throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server configuration is invalid.");
    }
    if (connection.transport !== "streamable_http") {
      throw new PublicAgentError(
        "CONFIGURATION_ERROR",
        "The Codex runtime supports only Streamable HTTP MCP servers.",
      );
    }

    let parsedUrl;
    try {
      parsedUrl = new URL(connection.url);
    } catch {
      throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server configuration is invalid.");
    }
    if (
      !["http:", "https:"].includes(parsedUrl.protocol)
      || parsedUrl.username
      || parsedUrl.password
      || parsedUrl.hash
    ) {
      throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server configuration is invalid.");
    }

    const headers = connection.headers ?? {};
    if (
      !headers
      || typeof headers !== "object"
      || Array.isArray(headers)
      || Object.keys(headers).length > 20
      || Object.entries(headers).some(([headerName, headerValue]) => (
        !MCP_HEADER_NAME_PATTERN.test(headerName)
        || typeof headerValue !== "string"
        || !headerValue
        || headerValue.includes("\r")
        || headerValue.includes("\n")
      ))
    ) {
      throw new PublicAgentError("CONFIGURATION_ERROR", "MCP server headers are invalid.");
    }

    args.push("-c", `mcp_servers.${name}.url=${tomlString(connection.url)}`);
    args.push("-c", `mcp_servers.${name}.required=true`);
    const headerEnvironment = {};
    let headerIndex = 0;
    for (const [headerName, headerValue] of Object.entries(headers)) {
      const variableName = `AGENTFLOW_MCP_${serverNames.length}_HEADER_${headerIndex}`;
      environment[variableName] = headerValue;
      headerEnvironment[headerName] = variableName;
      headerIndex += 1;
    }
    if (Object.keys(headerEnvironment).length) {
      args.push(
        "-c",
        `mcp_servers.${name}.env_http_headers=${tomlInlineStringMap(headerEnvironment)}`,
      );
    }
    serverNames.push(name);
  }
  return { args, environment, serverNames };
}

export function buildCodexArgs({ model, baseUrl, schemaPath, outputPath, mcpArgs = [] }) {
  return [
    "exec",
    "--ephemeral",
    "--ignore-user-config",
    "--skip-git-repo-check",
    "--color",
    "never",
    "--sandbox",
    "read-only",
    "--model",
    model,
    "--output-schema",
    schemaPath,
    "--output-last-message",
    outputPath,
    "-c",
    'approval_policy="never"',
    "-c",
    'model_provider="agentflow"',
    "-c",
    'model_providers.agentflow.name="AgentFlow managed provider"',
    "-c",
    `model_providers.agentflow.base_url=${tomlString(baseUrl)}`,
    "-c",
    'model_providers.agentflow.env_key="AGENT_PROVIDER_API_KEY"',
    "-c",
    'model_providers.agentflow.wire_api="responses"',
    "-c",
    "model_providers.agentflow.requires_openai_auth=false",
    "-c",
    "model_providers.agentflow.supports_websockets=false",
    ...mcpArgs,
    "-",
  ];
}

export function runCodex(binary, args, prompt, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(binary, args, {
      cwd: "/workspace",
      env,
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
    });
    const stdout = [];
    const stderr = [];
    let stdoutBytes = 0;
    let stderrBytes = 0;
    let exceededLimit = false;

    const capture = (chunks, chunk, currentBytes) => {
      const remaining = MAX_CAPTURE_BYTES - currentBytes;
      if (remaining > 0) chunks.push(chunk.subarray(0, remaining));
      if (chunk.length > remaining) {
        exceededLimit = true;
        child.kill("SIGKILL");
      }
      return currentBytes + chunk.length;
    };

    child.stdout.on("data", (chunk) => {
      stdoutBytes = capture(stdout, chunk, stdoutBytes);
    });
    child.stderr.on("data", (chunk) => {
      stderrBytes = capture(stderr, chunk, stderrBytes);
    });
    child.once("error", reject);
    child.once("close", (code) => {
      resolve({
        code: code ?? 1,
        stdout: Buffer.concat(stdout).toString("utf8"),
        stderr: Buffer.concat(stderr).toString("utf8"),
        exceededLimit,
      });
    });
    child.stdin.end(prompt, "utf8");
  });
}

function mapCodexFailure(result) {
  if (result.exceededLimit) {
    return new PublicAgentError("AGENT_FAILED", "Codex runtime output exceeded its safety limit.");
  }
  const diagnostic = `${result.stderr}\n${result.stdout}`.toLowerCase();
  if (
    diagnostic.includes("mcp")
    && (
      diagnostic.includes("failed")
      || diagnostic.includes("could not connect")
      || diagnostic.includes("initialize")
      || diagnostic.includes("timed out")
    )
  ) {
    return new PublicAgentError(
      "MCP_CONNECTION_FAILED",
      "Could not connect to one or more selected MCP servers.",
    );
  }
  if (
    diagnostic.includes("402")
    || diagnostic.includes("payment required")
    || diagnostic.includes("more credits")
  ) {
    return new PublicAgentError(
      "RATE_LIMITED",
      "The model provider credit or quota limit was reached.",
    );
  }
  if (diagnostic.includes("429") || diagnostic.includes("rate limit")) {
    return new PublicAgentError("RATE_LIMITED", "The model provider rate limit was reached. Retry later.");
  }
  if (diagnostic.includes("401") || diagnostic.includes("403") || diagnostic.includes("authentication")) {
    return new PublicAgentError("PROVIDER_AUTH_ERROR", "The model provider rejected its managed API key.");
  }
  if (diagnostic.includes("404") || diagnostic.includes("model not found")) {
    return new PublicAgentError("MODEL_NOT_FOUND", "The selected model is not available from the provider.");
  }
  if (diagnostic.includes("503") || diagnostic.includes("overloaded")) {
    return new PublicAgentError("PROVIDER_OVERLOADED", "The model provider is temporarily overloaded. Retry later.");
  }
  if (diagnostic.includes("400") || diagnostic.includes("invalid request") || diagnostic.includes("unsupported model")) {
    return new PublicAgentError("MODEL_REQUEST_REJECTED", "The selected model rejected the Codex request.");
  }
  if (
    diagnostic.includes("502")
    || diagnostic.includes("504")
    || diagnostic.includes("connection refused")
    || diagnostic.includes("error sending request")
    || diagnostic.includes("stream disconnected before completion")
    || diagnostic.includes("timed out")
  ) {
    return new PublicAgentError("PROVIDER_UNAVAILABLE", "The model provider is temporarily unavailable. Retry later.");
  }
  return new PublicAgentError("AGENT_FAILED", "Codex runtime failed to complete the task.");
}

export async function executeRequest(requestValue, env = process.env, commandRunner = runCodex) {
  const request = validateRequest(requestValue);
  requireEnvironment(env, "AGENT_PROVIDER_API_KEY");
  const model = requireEnvironment(env, "AGENT_MODEL");
  const baseUrl = validateBaseUrl(requireEnvironment(env, "AGENT_PROVIDER_BASE_URL"));
  const mcp = buildMcpConfiguration(env.AGENT_MCP_SERVERS, env);

  const workDirectory = await mkdtemp(join(tmpdir(), "agentflow-codex-"));
  const schemaPath = join(workDirectory, "schema.json");
  const outputPath = join(workDirectory, "answer.json");
  try {
    await mkdir(env.HOME || join(workDirectory, "home"), { recursive: true });
    await mkdir(env.CODEX_HOME || join(workDirectory, "codex-home"), { recursive: true });
    await writeFile(schemaPath, JSON.stringify(request.output_schema), { encoding: "utf8", mode: 0o600 });
    const args = buildCodexArgs({
      model,
      baseUrl,
      schemaPath,
      outputPath,
      mcpArgs: mcp.args,
    });
    const result = await commandRunner(
      env.CODEX_BINARY || "codex",
      args,
      buildPrompt(request),
      mcp.environment,
    );
    if (result.code !== 0) throw mapCodexFailure(result);

    let output;
    try {
      output = JSON.parse(await readFile(outputPath, "utf8"));
    } catch {
      throw new PublicAgentError("OUTPUT_INVALID", "Codex returned invalid structured output.");
    }
    if (!output || typeof output !== "object" || Array.isArray(output)) {
      throw new PublicAgentError("OUTPUT_INVALID", "Codex must return one JSON object.");
    }
    return {
      run_id: randomUUID(),
      status: "succeeded",
      mode: "live",
      output,
    };
  } finally {
    await rm(workDirectory, { recursive: true, force: true });
  }
}

async function readJsonStdin() {
  const chunks = [];
  let size = 0;
  for await (const chunk of process.stdin) {
    size += chunk.length;
    if (size > MAX_INPUT_BYTES) {
      throw new PublicAgentError("INVALID_INPUT", "Agent runtime request exceeds the 1 MiB limit.");
    }
    chunks.push(chunk);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new PublicAgentError("INVALID_INPUT", "Invalid task/context JSON.");
  }
}

export async function main() {
  try {
    const result = await executeRequest(await readJsonStdin());
    process.stdout.write(`${JSON.stringify(result)}\n`);
  } catch (error) {
    const publicError = error instanceof PublicAgentError
      ? error
      : new PublicAgentError("AGENT_FAILED", "Codex runtime failed to complete the task.");
    process.stdout.write(`${JSON.stringify({ error: { code: publicError.code, message: publicError.message } })}\n`);
    process.exitCode = publicError.code === "INVALID_INPUT" ? 2 : 1;
  }
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  await main();
}
