import { accessToken } from "./auth";
import type {
  FlowRun,
  LlmProvider,
  McpServer,
  OpenRouterSettings,
  ProviderModel,
  ProviderModelTestResult,
  RunStep,
  Skill,
  Workflow,
  WorkflowVersion,
} from "./types";

const API_URL = import.meta.env.VITE_SYSTEM_API_URL ?? "http://localhost:8000";

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const token = await accessToken();
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
      ...init.headers,
    },
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as {
      detail?: string | { message?: string; errors?: string[] };
    } | null;
    const detail = payload?.detail;
    const message = typeof detail === "string"
      ? detail
      : detail?.message ?? detail?.errors?.join(" · ");
    throw new Error(message || `HTTP ${response.status}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  listMcpServers: () => request<McpServer[]>("/v1/mcp-servers"),
  createMcpServer: (input: {
    slug: string;
    name: string;
    description: string;
    transport: McpServer["transport"];
    url: string;
    headers: Record<string, string>;
    enabled: boolean;
  }) => request<McpServer>("/v1/mcp-servers", {
    method: "POST",
    body: JSON.stringify(input),
  }),
  updateMcpServer: (server: McpServer, input: {
    name: string;
    description: string;
    transport: McpServer["transport"];
    url: string;
    headers?: Record<string, string>;
    enabled: boolean;
  }) => request<McpServer>(`/v1/mcp-servers/${server.id}`, {
    method: "PUT",
    body: JSON.stringify({ expected_revision: server.revision, ...input }),
  }),
  deleteMcpServer: (serverId: string) =>
    request<void>(`/v1/mcp-servers/${serverId}`, { method: "DELETE" }),
  listLlmProviders: () => request<LlmProvider[]>("/v1/llm-providers"),
  createLlmProvider: (input: { api_key: string }) =>
    request<LlmProvider>("/v1/llm-providers", { method: "POST", body: JSON.stringify(input) }),
  deleteLlmProvider: (providerId: string) =>
    request<void>(`/v1/llm-providers/${providerId}`, { method: "DELETE" }),
  updateLlmProvider: (provider: LlmProvider, input: { name: string; settings: OpenRouterSettings; enabled: boolean; api_key?: string }) =>
    request<LlmProvider>(`/v1/llm-providers/${provider.id}`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: provider.revision, ...input }),
    }),
  listProviderModels: (providerId: string) =>
    request<ProviderModel[]>(`/v1/llm-providers/${providerId}/models`),
  testProviderModel: (providerId: string, model: string) =>
    request<ProviderModelTestResult>(`/v1/llm-providers/${providerId}/models/test`, {
      method: "POST",
      body: JSON.stringify({ model }),
    }),
  listSkills: () => request<Skill[]>("/v1/skills"),
  createSkill: (input: {
    slug: string;
    name: string;
    description: string;
    instructions: string;
  }) =>
    request<Skill>("/v1/skills", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  updateSkill: (skill: Skill, input: {
    name: string;
    description: string;
    instructions: string;
    enabled: boolean;
  }) =>
    request<Skill>(`/v1/skills/${skill.id}`, {
      method: "PUT",
      body: JSON.stringify({ expected_version: skill.version, ...input }),
    }),
  deleteSkill: (skillId: string) =>
    request<void>(`/v1/skills/${skillId}`, { method: "DELETE" }),
  listWorkflows: () => request<Workflow[]>("/v1/workflows"),
  deleteWorkflow: (workflowId: string) =>
    request<void>(`/v1/workflows/${workflowId}`, { method: "DELETE" }),
  createWorkflow: (name: string, draft?: Record<string, unknown>) =>
    request<Workflow>("/v1/workflows", {
      method: "POST",
      body: JSON.stringify({ name, ...(draft ? { draft } : {}) }),
    }),
  updateDraft: (workflow: Workflow, draft: Record<string, unknown>) =>
    request<Workflow>(`/v1/workflows/${workflow.id}/draft`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: workflow.revision, draft }),
    }),
  validate: (workflowId: string) =>
    request<{ valid: boolean; errors: string[] }>(
      `/v1/workflows/${workflowId}/validate`,
      { method: "POST" },
    ),
  publish: (workflowId: string) =>
    request<WorkflowVersion>(`/v1/workflows/${workflowId}/versions`, {
      method: "POST",
    }),
  getCurrentWorkflowVersion: (workflowId: string) =>
    request<WorkflowVersion | null>(`/v1/workflows/${workflowId}/versions/current`),
  createRun: (workflowId: string, versionId: string, input: Record<string, unknown>) =>
    request<FlowRun>(`/v1/workflows/${workflowId}/runs`, {
      method: "POST",
      body: JSON.stringify({ version_id: versionId, input }),
    }),
  listWorkflowRuns: (workflowId: string) =>
    request<FlowRun[]>(`/v1/workflows/${workflowId}/runs`),
  getRun: (runId: string) => request<FlowRun>(`/v1/runs/${runId}`),
  listRunSteps: (runId: string) =>
    request<RunStep[]>(`/v1/runs/${runId}/steps`),
};
