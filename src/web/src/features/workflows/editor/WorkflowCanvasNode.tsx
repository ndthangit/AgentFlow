import { Handle, Position, type NodeProps } from "@xyflow/react";

import type { WorkflowFlowNode } from "./types";

const nodeMetadata: Record<string, { label: string; className: string }> = {
  "input.schema": { label: "START", className: "input-node" },
  agent: { label: "AI AGENT", className: "agent-node" },
  "llm.call": { label: "LLM CALL", className: "llm-node" },
  "code.python": { label: "PYTHON", className: "code-node" },
  "math.add": { label: "MATH", className: "math-node" },
  if: { label: "IF / ELSE", className: "if-node" },
  parallel: { label: "PARALLEL", className: "parallel-node" },
  "output.schema": { label: "END", className: "output-node" },
  "trigger.manual": { label: "TRIGGER", className: "trigger" },
  end: { label: "OUTPUT", className: "end" },
};

function WorkflowCanvasNode({ data, selected }: NodeProps<WorkflowFlowNode>) {
  const node = data.graphNode;
  const metadata = nodeMetadata[node.type ?? ""] ?? {
    label: (node.type ?? "NODE").toUpperCase(),
    className: "",
  };
  const acceptsInput = node.type !== "input.schema" && node.type !== "trigger.manual";
  const protectedNode = node.type === "input.schema" || node.type === "output.schema";

  return (
    <div className={`workflow-flow-node${selected ? " selected" : ""}`}>
      {acceptsInput && (
        <Handle
          className="workflow-target-handle"
          type="target"
          position={Position.Top}
          id="target"
        />
      )}
      <div className="flow-node-row">
        <div className={`node ${metadata.className}`}>
          <small>{metadata.label}</small>
          <strong>{typeof node.name === "string" ? node.name : node.id}</strong>
          <span>
            {typeof node.note === "string" && node.note ? node.note : `ID: ${node.id}`}
          </span>
        </div>
        <div className="node-row-actions nodrag nowheel">
          <button type="button" title="Cấu hình node" onClick={() => data.onConfigure(node.id)}>
            Cấu hình
          </button>
          <button
            type="button"
            className="danger"
            title={protectedNode ? "Start và End là node bắt buộc" : "Xóa node"}
            disabled={data.disabled || protectedNode}
            onClick={() => data.onDelete(node)}
          >
            Xóa
          </button>
        </div>
      </div>
      {!!data.ports.length && (
        <div className="node-connection-labels" aria-label={`Điểm nối của node ${node.id}`}>
          {data.ports.map((port) => (
            <span className={`port-${port}`} key={port}>{port}</span>
          ))}
        </div>
      )}
      {data.ports.map((port, index) => (
        <Handle
          className={`workflow-source-handle port-${port}`}
          type="source"
          position={Position.Bottom}
          id={port}
          key={port}
          style={{ left: `${((index + 1) * 100) / (data.ports.length + 1)}%` }}
          isConnectable={!data.disabled}
        />
      ))}
    </div>
  );
}

export const workflowNodeTypes = { workflowNode: WorkflowCanvasNode };
