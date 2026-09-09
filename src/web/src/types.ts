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

export type Skill = {
  id: string;
  slug: string;
  name: string;
  description: string;
  instructions: string;
  version: number;
  content_hash: string;
  enabled: boolean;
  source: "builtin" | "user";
  created_at: string;
  updated_at: string;
};

export type OpenRouterSettings = {
  default_model: string | null;
  site_url: string | null;
  app_title: string;
};

export type LlmProvider = {
  id: string;
  name: string;
  kind: "openrouter";
  settings: OpenRouterSettings;
  has_api_key: boolean;
  enabled: boolean;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type ProviderModel = {
  id: string;
  name: string;
  context_length: number | null;
  pricing: Record<string, string>;
};
