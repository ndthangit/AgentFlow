import { accessToken } from "./auth";
import type { FlowRun, Workflow, WorkflowVersion } from "./types";

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
    const detail = await response.text();
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export const api = {
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
