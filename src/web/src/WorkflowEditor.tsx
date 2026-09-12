import { useEffect, useMemo, useState } from "react";

type WorkflowEditorProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
};

type GraphNode = Record<string, unknown> & {
  id: string;
  type?: string;
};

type GraphEdge = Record<string, unknown> & {
  from: string;
  to: string;
};

type Graph = Record<string, unknown> & {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type EditableNodeType = "input.schema" | "agent" | "code.python" | "output.schema";

type NodeForm = {
  id: string;
  name: string;
  note: string;
  schema: string;
  instructions: string;
  code: string;
  inputSchema: string;
  outputSchema: string;
};

const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const nodeMetadata: Record<string, { label: string; className: string }> = {
  "input.schema": { label: "INPUT SCHEMA", className: "input-node" },
  agent: { label: "AI AGENT", className: "agent-node" },
  "code.python": { label: "PYTHON", className: "code-node" },
  "math.add": { label: "MATH", className: "math-node" },
  "output.schema": { label: "OUTPUT SCHEMA", className: "output-node" },
  "trigger.manual": { label: "TRIGGER", className: "trigger" },
  end: { label: "OUTPUT", className: "end" },
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function parseGraph(value: string): { graph?: Graph; error?: string } {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed) || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
      return { error: "Draft phải có hai mảng nodes và edges." };
    }
    if (!parsed.nodes.every((node) => isRecord(node) && typeof node.id === "string" && (node.type === undefined || typeof node.type === "string"))) {
      return { error: "Mỗi node phải là object, có id dạng chuỗi và type hợp lệ." };
    }
    if (!parsed.edges.every((edge) => isRecord(edge) && typeof edge.from === "string" && typeof edge.to === "string")) {
      return { error: "Mỗi edge phải có from và to dạng chuỗi." };
    }
    return { graph: parsed as Graph };
  } catch {
    return { error: "JSON chưa hợp lệ. Hãy sửa JSON để tiếp tục dùng trình thiết kế." };
  }
}

function schemaText(value: unknown) {
  return JSON.stringify(isRecord(value) ? value : emptyObjectSchema, null, 2);
}

function formFromNode(node: GraphNode): NodeForm {
  const config = isRecord(node.config) ? node.config : {};
  return {
    id: node.id,
    name: typeof node.name === "string" ? node.name : node.id,
    note: typeof node.note === "string" ? node.note : "",
    schema: schemaText(node.schema),
    instructions: typeof config.instructions === "string" ? config.instructions : "",
    code: typeof config.code === "string" ? config.code : "",
    inputSchema: schemaText(config.inputSchema),
    outputSchema: schemaText(config.outputSchema),
  };
}

function parseSchema(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} phải là JSON hợp lệ.`);
  }
  if (!isRecord(parsed)) throw new Error(`${label} phải là một JSON object.`);
  return parsed;
}

function uniqueNodeId(nodes: GraphNode[], type: EditableNodeType) {
  const base = type === "input.schema"
    ? "input"
    : type === "output.schema"
      ? "output"
      : type === "code.python"
        ? "python"
        : "agent";
  let candidate = base;
  let suffix = 2;
  while (nodes.some((node) => node.id === candidate)) candidate = `${base}_${suffix++}`;
  return candidate;
}

function newNode(type: EditableNodeType, id: string): GraphNode {
  if (type === "agent") {
    return {
      id,
      type,
      name: "Agent",
      note: "Mô tả ngắn nhiệm vụ của agent.",
      config: {
        instructions: "",
        inputSchema: emptyObjectSchema,
        outputSchema: emptyObjectSchema,
      },
    };
  }
  if (type === "code.python") {
    return {
      id,
      type,
      name: "Python code",
      note: "Xử lý dữ liệu bằng một hàm Python thuần.",
      config: {
        language: "python",
        code: "def main(inputs):\n    return inputs\n",
        inputSchema: emptyObjectSchema,
        outputSchema: emptyObjectSchema,
      },
    };
  }
  const input = type === "input.schema";
  return {
    id,
    type,
    name: input ? "Dữ liệu đầu vào" : "Dữ liệu đầu ra",
    note: input ? "Định nghĩa dữ liệu workflow tiếp nhận." : "Định nghĩa kết quả workflow trả về.",
    schema: emptyObjectSchema,
  };
}

function nodeInsertIndex(nodes: GraphNode[], type: EditableNodeType) {
  if (type === "input.schema") {
    const firstNonInput = nodes.findIndex((node) => node.type !== "input.schema");
    return firstNonInput < 0 ? nodes.length : firstNonInput;
  }
  if (type === "agent" || type === "code.python") {
    const firstOutput = nodes.findIndex((node) => node.type === "output.schema" || node.type === "end");
    return firstOutput < 0 ? nodes.length : firstOutput;
  }
  return nodes.length;
}

export function WorkflowEditor({ value, onChange, disabled = false }: WorkflowEditorProps) {
  const parsed = useMemo(() => parseGraph(value), [value]);
  const graph = parsed.graph;
  const [selectedId, setSelectedId] = useState<string>();
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId);
  const [form, setForm] = useState<NodeForm>();
  const [formError, setFormError] = useState("");

  useEffect(() => {
    if (selectedNode) return;
    setSelectedId(graph?.nodes[0]?.id);
  }, [graph, selectedNode]);

  useEffect(() => {
    setForm(selectedNode ? formFromNode(selectedNode) : undefined);
    setFormError("");
  }, [selectedNode]);

  function commit(next: Graph) {
    onChange(JSON.stringify(next, null, 2));
  }

  function addNode(type: EditableNodeType) {
    if (!graph) return;
    const id = uniqueNodeId(graph.nodes, type);
    const index = nodeInsertIndex(graph.nodes, type);
    const previous = graph.nodes[index - 1];
    const next = graph.nodes[index];
    let edges = [...graph.edges];

    if (previous && next) {
      const directEdge = edges.find((edge) => edge.from === previous.id && edge.to === next.id);
      if (directEdge) {
        edges = edges.filter((edge) => edge !== directEdge);
        edges.push({ ...directEdge, to: id }, { ...directEdge, from: id });
      } else {
        edges.push({ from: previous.id, to: id, port: "success" }, { from: id, to: next.id, port: "success" });
      }
    } else if (previous) {
      edges.push({ from: previous.id, to: id, port: "success" });
    } else if (next) {
      edges.push({ from: id, to: next.id, port: "success" });
    }

    const nodes = [...graph.nodes];
    nodes.splice(index, 0, newNode(type, id));
    commit({ ...graph, nodes, edges });
    setSelectedId(id);
  }

  function deleteNode(node: GraphNode) {
    if (!graph || !window.confirm(`Xóa node “${typeof node.name === "string" ? node.name : node.id}”?`)) return;
    const incoming = graph.edges.filter((edge) => edge.to === node.id);
    const outgoing = graph.edges.filter((edge) => edge.from === node.id);
    const edges = graph.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id);

    for (const before of incoming) {
      for (const after of outgoing) {
        if (before.from === after.to || edges.some((edge) => edge.from === before.from && edge.to === after.to)) continue;
        edges.push({ from: before.from, to: after.to, port: before.port ?? "success" });
      }
    }

    commit({ ...graph, nodes: graph.nodes.filter((item) => item.id !== node.id), edges });
    setSelectedId(undefined);
  }

  function saveNode() {
    if (!graph || !selectedNode || !form) return;
    const id = form.id.trim();
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) {
      setFormError("ID phải bắt đầu bằng chữ và chỉ gồm chữ, số, _ hoặc -.");
      return;
    }
    if (graph.nodes.some((node) => node !== selectedNode && node.id === id)) {
      setFormError("ID này đã được một node khác sử dụng.");
      return;
    }

    try {
      const updated: GraphNode = {
        ...selectedNode,
        id,
        name: form.name.trim() || id,
        note: form.note.trim(),
      };
      if (selectedNode.type === "input.schema" || selectedNode.type === "output.schema") {
        updated.schema = parseSchema(form.schema, "Schema");
      }
      if (selectedNode.type === "agent") {
        const config = isRecord(selectedNode.config) ? selectedNode.config : {};
        updated.config = {
          ...config,
          instructions: form.instructions.trim(),
          inputSchema: parseSchema(form.inputSchema, "Input schema"),
          outputSchema: parseSchema(form.outputSchema, "Output schema"),
        };
      }
      if (selectedNode.type === "code.python") {
        const config = isRecord(selectedNode.config) ? selectedNode.config : {};
        if (!form.code.trim()) throw new Error("Python code không được để trống.");
        updated.config = {
          ...config,
          language: "python",
          code: form.code,
          inputSchema: parseSchema(form.inputSchema, "Input schema"),
          outputSchema: parseSchema(form.outputSchema, "Output schema"),
        };
      }
      const edges = graph.edges.map((edge) => ({
        ...edge,
        from: edge.from === selectedNode.id ? id : edge.from,
        to: edge.to === selectedNode.id ? id : edge.to,
      }));
      commit({
        ...graph,
        nodes: graph.nodes.map((node) => node === selectedNode ? updated : node),
        edges,
      });
      setSelectedId(id);
      setFormError("");
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Không thể lưu node.");
    }
  }

  return (
    <div className="workflow-editor">
      <div className="node-toolbar">
        <div>
          <strong>Nodes</strong>
          <span>Thêm và cấu hình các bước của workflow</span>
        </div>
        <div className="node-add-actions">
          <button disabled={disabled || !graph} onClick={() => addNode("input.schema")}>+ Input schema</button>
          <button disabled={disabled || !graph} onClick={() => addNode("agent")}>+ Agent</button>
          <button disabled={disabled || !graph} onClick={() => addNode("code.python")}>+ Python</button>
          <button disabled={disabled || !graph} onClick={() => addNode("output.schema")}>+ Output schema</button>
        </div>
      </div>

      <div className="builder-grid">
        <section className="flow-preview" aria-label="Danh sách node">
          {graph?.nodes.length ? graph.nodes.map((node, index) => {
            const metadata = nodeMetadata[node.type ?? ""] ?? { label: (node.type ?? "NODE").toUpperCase(), className: "" };
            const directEdges = index < graph.nodes.length - 1
              ? graph.edges.filter((edge) => edge.from === node.id && edge.to === graph.nodes[index + 1].id)
              : [];
            return (
              <div className="flow-node-item" key={node.id}>
                <div className="flow-node-row">
                  <button
                    className={`node ${metadata.className}${selectedId === node.id ? " selected" : ""}`}
                    onClick={() => setSelectedId(node.id)}
                  >
                    <small>{metadata.label}</small>
                    <strong>{typeof node.name === "string" ? node.name : node.id}</strong>
                    <span>{typeof node.note === "string" && node.note ? node.note : `ID: ${node.id}`}</span>
                  </button>
                  <div className="node-row-actions">
                    <button title="Sửa node" onClick={() => setSelectedId(node.id)}>Sửa</button>
                    <button className="danger" title="Xóa node" disabled={disabled} onClick={() => deleteNode(node)}>Xóa</button>
                  </div>
                </div>
                {index < graph.nodes.length - 1 && (
                  <div className={directEdges.length ? "connector connected" : "connector"}>
                    <i /><b>{directEdges.length || "·"}</b><i />
                  </div>
                )}
              </div>
            );
          }) : (
            <div className="nodes-empty">
              <strong>Workflow chưa có node</strong>
              <span>Hãy bắt đầu bằng Input schema, Agent, Python hoặc Output schema.</span>
            </div>
          )}
          {parsed.error && <p className="builder-error">{parsed.error}</p>}
        </section>

        <aside className="node-inspector">
          <div className="panel-title"><span>Thuộc tính node</span><code>{selectedNode?.type ?? "NONE"}</code></div>
          {selectedNode && form ? (
            <div className="node-form">
              <label>ID<input value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} /></label>
              <label>Tên node<input value={form.name} maxLength={160} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label>Note<textarea className="compact-textarea" value={form.note} placeholder="Mô tả vai trò của node…" onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>

              {(selectedNode.type === "input.schema" || selectedNode.type === "output.schema") && (
                <label>JSON Schema<textarea className="schema-textarea" value={form.schema} spellCheck={false} onChange={(event) => setForm({ ...form, schema: event.target.value })} /></label>
              )}

              {selectedNode.type === "agent" && (
                <>
                  <label>Instructions<textarea className="compact-textarea instructions" value={form.instructions} placeholder="Agent cần thực hiện điều gì?" onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>
                  <label>Input schema<textarea className="schema-textarea" value={form.inputSchema} spellCheck={false} onChange={(event) => setForm({ ...form, inputSchema: event.target.value })} /></label>
                  <label>Output schema<textarea className="schema-textarea" value={form.outputSchema} spellCheck={false} onChange={(event) => setForm({ ...form, outputSchema: event.target.value })} /></label>
                </>
              )}

              {selectedNode.type === "code.python" && (
                <>
                  <label>Python code<textarea className="code-textarea" value={form.code} spellCheck={false} onChange={(event) => setForm({ ...form, code: event.target.value })} /></label>
                  <p className="node-form-hint">Khai báo hàm main(inputs) và trả về một JSON object.</p>
                  <label>Input schema<textarea className="schema-textarea" value={form.inputSchema} spellCheck={false} onChange={(event) => setForm({ ...form, inputSchema: event.target.value })} /></label>
                  <label>Output schema<textarea className="schema-textarea" value={form.outputSchema} spellCheck={false} onChange={(event) => setForm({ ...form, outputSchema: event.target.value })} /></label>
                </>
              )}

              {formError && <p className="form-error">{formError}</p>}
              <button className="save-node" disabled={disabled} onClick={saveNode}>Lưu thay đổi node</button>
            </div>
          ) : (
            <div className="inspector-empty">Chọn một node để sửa thuộc tính.</div>
          )}
        </aside>
      </div>

      <details className="raw-json">
        <summary>JSON nâng cao</summary>
        <div className="json-panel">
          <div className="panel-title"><span>Graph definition</span><code>JSON</code></div>
          <textarea value={value} onChange={(event) => onChange(event.target.value)} spellCheck={false} />
        </div>
      </details>
    </div>
  );
}
