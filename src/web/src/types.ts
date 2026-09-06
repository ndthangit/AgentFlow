export type Workflow = {
  id: string;
  name: string;
  draft: Record<string, unknown>;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type WorkflowVersion = {
  id: string;
  workflow_id: string;
  version: number;
  graph: Record<string, unknown>;
  content_hash: string;
  created_at: string;
};

export type FlowRun = {
  id: string;
  workflow_version_id: string;
  status: string;
  input: Record<string, unknown>;
  output: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
};
