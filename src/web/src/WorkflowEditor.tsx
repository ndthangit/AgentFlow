import { useEffect, useMemo, useState } from "react";

import type { Skill } from "./types";

type WorkflowEditorProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  skills?: Skill[];
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
type SchemaType = "string" | "number" | "integer" | "boolean" | "object" | "array";

type NodeForm = {
  id: string;
  name: string;
  note: string;
  schema: string;
  instructions: string;
  code: string;
  inputSchema: string;
  outputSchema: string;
  skillIds: string[];
  inheritsWorkflowSkills: boolean;
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
    skillIds: Array.isArray(config.skillIds)
      ? config.skillIds.filter((item): item is string => typeof item === "string")
      : [],
    inheritsWorkflowSkills: !Array.isArray(config.skillIds),
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

const schemaTypes: { value: SchemaType; label: string }[] = [
  { value: "string", label: "Văn bản (string)" },
  { value: "number", label: "Số (number)" },
  { value: "integer", label: "Số nguyên (integer)" },
  { value: "boolean", label: "Đúng / sai (boolean)" },
  { value: "object", label: "Đối tượng (object)" },
  { value: "array", label: "Danh sách (array)" },
];

function schemaForType(type: SchemaType): Record<string, unknown> {
  if (type === "object") return { type, properties: {}, additionalProperties: false };
  if (type === "array") return { type, items: { type: "string" } };
  return { type };
}

function SchemaBuilder({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  const parsed = useMemo<{ schema: Record<string, unknown>; error?: string }>(() => {
    try {
      const schema: unknown = JSON.parse(value);
      return isRecord(schema)
        ? { schema }
        : { schema: emptyObjectSchema, error: "Schema phải là một JSON object." };
    } catch {
      return { schema: emptyObjectSchema, error: "JSON nâng cao chưa hợp lệ." };
    }
  }, [value]);
  const properties = isRecord(parsed.schema.properties) ? parsed.schema.properties : {};
  const required = Array.isArray(parsed.schema.required)
    ? parsed.schema.required.filter((item): item is string => typeof item === "string")
    : [];

  function commit(nextProperties: Record<string, unknown>, nextRequired = required) {
    const nextSchema: Record<string, unknown> = {
      ...parsed.schema,
      type: "object",
      properties: nextProperties,
      additionalProperties: false,
    };
    if (nextRequired.length) nextSchema.required = nextRequired;
    else delete nextSchema.required;
    onChange(JSON.stringify(nextSchema, null, 2));
  }

  function addField() {
    let suffix = Object.keys(properties).length + 1;
    let name = `field_${suffix}`;
    while (name in properties) name = `field_${++suffix}`;
    commit({ ...properties, [name]: { type: "string" } });
  }

  function renameField(currentName: string, nextName: string) {
    if (nextName !== currentName && nextName in properties) return;
    const nextProperties: Record<string, unknown> = {};
    for (const [name, definition] of Object.entries(properties)) {
      nextProperties[name === currentName ? nextName : name] = definition;
    }
    commit(
      nextProperties,
      required.map((name) => name === currentName ? nextName : name),
    );
  }

  function setFieldType(name: string, type: SchemaType) {
    commit({ ...properties, [name]: schemaForType(type) });
  }

  function setArrayItemType(name: string, type: SchemaType) {
    const definition = isRecord(properties[name]) ? properties[name] : {};
    commit({ ...properties, [name]: { ...definition, items: schemaForType(type) } });
  }

  function setRequired(name: string, checked: boolean) {
    commit(
      properties,
      checked ? [...new Set([...required, name])] : required.filter((item) => item !== name),
    );
  }

  function removeField(name: string) {
    const nextProperties = { ...properties };
    delete nextProperties[name];
    commit(nextProperties, required.filter((item) => item !== name));
  }

  return (
    <section className="schema-builder">
      <div className="schema-builder-heading">
        <div><strong>{label}</strong><span>Khai báo các trường dữ liệu JSON</span></div>
        <button type="button" onClick={addField}>+ Thêm trường</button>
      </div>
      <div className="schema-field-list">
        {Object.entries(properties).map(([name, rawDefinition], index) => {
          const definition = isRecord(rawDefinition) ? rawDefinition : {};
          const type = schemaTypes.some((item) => item.value === definition.type)
            ? definition.type as SchemaType
            : "string";
          const items = isRecord(definition.items) ? definition.items : {};
          const itemType = schemaTypes.some((item) => item.value === items.type)
            ? items.type as SchemaType
            : "string";
          return (
            <div className={type === "array" ? "schema-field with-items" : "schema-field"} key={index}>
              <label>Tên trường<input value={name} placeholder="Ví dụ: email" onChange={(event) => renameField(name, event.target.value)} /></label>
              <label>Kiểu dữ liệu<select value={type} onChange={(event) => setFieldType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              {type === "array" && (
                <label>Kiểu phần tử<select value={itemType} onChange={(event) => setArrayItemType(name, event.target.value as SchemaType)}>{schemaTypes.map((item) => <option value={item.value} key={item.value}>{item.label}</option>)}</select></label>
              )}
              <label className="schema-required"><input type="checkbox" checked={required.includes(name)} onChange={(event) => setRequired(name, event.target.checked)} /> Bắt buộc</label>
              <button type="button" className="schema-remove" aria-label={`Xóa trường ${name}`} onClick={() => removeField(name)}>Xóa</button>
            </div>
          );
        })}
        {!Object.keys(properties).length && <p className="schema-empty">Chưa có trường dữ liệu. Chọn “Thêm trường” để bắt đầu.</p>}
      </div>
      <details className="schema-advanced">
        <summary>JSON Schema nâng cao</summary>
        <textarea className="schema-textarea" value={value} spellCheck={false} onChange={(event) => onChange(event.target.value)} />
        {parsed.error && <p>{parsed.error}</p>}
      </details>
    </section>
  );
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
        skillIds: [],
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

export function WorkflowEditor({ value, onChange, disabled = false, skills = [] }: WorkflowEditorProps) {
  const parsed = useMemo(() => parseGraph(value), [value]);
  const graph = parsed.graph;
  const [selectedId, setSelectedId] = useState<string>();
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId);
  const [form, setForm] = useState<NodeForm>();
  const [formError, setFormError] = useState("");
  const agentSkillIds = form?.inheritsWorkflowSkills
    ? skills.map((skill) => skill.id)
    : form?.skillIds ?? [];

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
          skillIds: agentSkillIds.filter((skillId) => skills.some((skill) => skill.id === skillId)),
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
                <SchemaBuilder label={selectedNode.type === "input.schema" ? "Input schema" : "Output schema"} value={form.schema} onChange={(schema) => setForm({ ...form, schema })} />
              )}

              {selectedNode.type === "agent" && (
                <>
                  <label>Instructions<textarea className="compact-textarea instructions" value={form.instructions} placeholder="Agent cần thực hiện điều gì?" onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>
                  <section className="agent-skill-picker">
                    <div><strong>Skills của Agent</strong><span>Mỗi Agent chỉ nhận các skill được chọn tại đây.</span></div>
                    <select
                      value=""
                      disabled={!skills.some((skill) => !agentSkillIds.includes(skill.id))}
                      onChange={(event) => {
                        if (event.target.value) setForm({ ...form, skillIds: [...agentSkillIds, event.target.value], inheritsWorkflowSkills: false });
                      }}
                    >
                      <option value="">+ Thêm skill…</option>
                      {skills.filter((skill) => !agentSkillIds.includes(skill.id)).map((skill) => <option value={skill.id} key={skill.id}>{skill.name}</option>)}
                    </select>
                    <div className="agent-skill-list">
                      {agentSkillIds.flatMap((skillId) => {
                        const skill = skills.find((item) => item.id === skillId);
                        return skill ? [<span key={skill.id}><b>{skill.name}</b><small>{skill.slug} · v{skill.version}</small><button type="button" aria-label={`Bỏ skill ${skill.name}`} onClick={() => setForm({ ...form, skillIds: agentSkillIds.filter((id) => id !== skill.id), inheritsWorkflowSkills: false })}>×</button></span>] : [];
                      })}
                      {!agentSkillIds.some((skillId) => skills.some((skill) => skill.id === skillId)) && <p>Chưa gắn skill. Hãy thêm skill vào kho workflow trước.</p>}
                    </div>
                  </section>
                  <SchemaBuilder label="Input schema" value={form.inputSchema} onChange={(inputSchema) => setForm({ ...form, inputSchema })} />
                  <SchemaBuilder label="Output schema" value={form.outputSchema} onChange={(outputSchema) => setForm({ ...form, outputSchema })} />
                </>
              )}

              {selectedNode.type === "code.python" && (
                <>
                  <label>Python code<textarea className="code-textarea" value={form.code} spellCheck={false} onChange={(event) => setForm({ ...form, code: event.target.value })} /></label>
                  <p className="node-form-hint">Khai báo hàm main(inputs) và trả về một JSON object.</p>
                  <SchemaBuilder label="Input schema" value={form.inputSchema} onChange={(inputSchema) => setForm({ ...form, inputSchema })} />
                  <SchemaBuilder label="Output schema" value={form.outputSchema} onChange={(outputSchema) => setForm({ ...form, outputSchema })} />
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
