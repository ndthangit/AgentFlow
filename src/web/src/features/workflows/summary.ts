import type { Workflow } from "../../shared/types";

export function workflowNodeTypes(workflow: Workflow) {
  const nodes = workflow.draft.nodes;
  if (!Array.isArray(nodes)) return [];
  return nodes.flatMap((node) => (
    node && typeof node === "object" && "type" in node && typeof node.type === "string"
      ? [node.type]
      : []
  ));
}

export function workflowNodeLabel(type: string) {
  const labels: Record<string, string> = {
    agent: "Agent",
    "llm.call": "LLM Call",
    "code.python": "Python",
    "input.schema": "Start",
    "output.schema": "End",
    "math.add": "Math",
    if: "If / Else",
    parallel: "Parallel",
  };
  return labels[type] ?? type;
}
