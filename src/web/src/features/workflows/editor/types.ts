import type { Edge, Node } from "@xyflow/react";

import type { LlmProvider, McpServer, NodeTypeDefinition, Skill } from "../../../shared/types";

export type WorkflowEditorProps = {
  value: string;
  onChange: (value: string) => void;
  onSave: (value: string) => void;
  disabled?: boolean;
  skills?: Skill[];
  providers?: LlmProvider[];
  mcpServers?: McpServer[];
  nodeTypes?: NodeTypeDefinition[];
};

export type GraphNode = Record<string, unknown> & {
  id: string;
  type?: string;
  position?: { x: number; y: number };
};

export type GraphEdge = Record<string, unknown> & {
  from: string;
  to: string;
  port?: string;
};

export type Graph = Record<string, unknown> & {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

export type WorkflowNodeData = Record<string, unknown> & {
  graphNode: GraphNode;
  ports: string[];
  disabled: boolean;
  onConfigure: (nodeId: string) => void;
  onDelete: (node: GraphNode) => void;
};

export type WorkflowFlowNode = Node<WorkflowNodeData, "workflowNode">;
export type WorkflowFlowEdge = Edge<{ graphEdge: GraphEdge; index: number }>;

export type EditableNodeType =
  | "input.schema"
  | "agent"
  | "llm.call"
  | "code.python"
  | "if"
  | "parallel"
  | "output.schema";

export type SchemaType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "object"
  | "array";

export type IfOperator =
  | "equals"
  | "notEquals"
  | "greaterThan"
  | "greaterThanOrEqual"
  | "lessThan"
  | "lessThanOrEqual"
  | "truthy"
  | "falsy";

export type NodeForm = {
  id: string;
  name: string;
  note: string;
  schema: string;
  instructions: string;
  runtime: string;
  providerId: string;
  model: string;
  code: string;
  inputSchema: string;
  outputSchema: string;
  skillIds: string[];
  mcpServerIds: string[];
  conditionFrom: string;
  operator: IfOperator;
  expected: string;
  outputBindings: Record<string, string>;
};
