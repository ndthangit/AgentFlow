import { accessToken } from "./auth";
import type {
  FlowRun,
  LlmProvider,
  OpenRouterSettings,
  ProviderModel,
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
    const payload = await response.json().catch(() => null) as { detail?: string } | null;
    throw new Error(payload?.detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  listLlmProviders: () => request<LlmProvider[]>("/v1/llm-providers"),
  createLlmProvider: (input: { name: string; kind: "openrouter"; api_key: string; settings: OpenRouterSettings }) =>
    request<LlmProvider>("/v1/llm-providers", { method: "POST", body: JSON.stringify(input) }),
  updateLlmProvider: (provider: LlmProvider, input: { name: string; settings: OpenRouterSettings; enabled: boolean; api_key?: string }) =>
    request<LlmProvider>(`/v1/llm-providers/${provider.id}`, {
      method: "PUT",
      body: JSON.stringify({ expected_revision: provider.revision, ...input }),
    }),
  listProviderModels: (providerId: string) =>
    request<ProviderModel[]>(`/v1/llm-providers/${providerId}/models`),
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
  listWorkflowSkills: (workflowId: string) =>
    request<Skill[]>(`/v1/workflows/${workflowId}/skills`),
  selectWorkflowSkills: (workflowId: string, skillIds: string[]) =>
    request<Skill[]>(`/v1/workflows/${workflowId}/skills`, {
      method: "PUT",
      body: JSON.stringify({ skill_ids: skillIds }),
    }),
  listWorkflows: () => request<Workflow[]>("/v1/workflows"),
  createWorkflow: (name: string) =>
    request<Workflow>("/v1/workflows", {
      method: "POST",
      body: JSON.stringify({ name }),
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
  createRun: (workflowId: string, versionId: string) =>
    request<FlowRun>(`/v1/workflows/${workflowId}/runs`, {
      method: "POST",
      body: JSON.stringify({ version_id: versionId, input: {} }),
    }),
};
