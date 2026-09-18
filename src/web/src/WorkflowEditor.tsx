import { useEffect, useMemo, useState } from "react";

import type { LlmProvider, Skill } from "./types";

type WorkflowEditorProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  skills?: Skill[];
  providers?: LlmProvider[];
};

type GraphNode = Record<string, unknown> & {
  id: string;
  type?: string;
};

type GraphEdge = Record<string, unknown> & {
  from: string;
  to: string;
  port?: string;
};

type Graph = Record<string, unknown> & {
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type EditableNodeType = "input.schema" | "agent" | "llm.call" | "code.python" | "if" | "parallel" | "output.schema";
type SchemaType = "string" | "number" | "integer" | "boolean" | "object" | "array";
type IfOperator = "equals" | "notEquals" | "greaterThan" | "greaterThanOrEqual" | "lessThan" | "lessThanOrEqual" | "truthy" | "falsy";

type NodeForm = {
  id: string;
  name: string;
  note: string;
  schema: string;
  instructions: string;
  providerId: string;
  model: string;
  code: string;
  inputSchema: string;
  outputSchema: string;
  skillIds: string[];
  inheritsWorkflowSkills: boolean;
  conditionFrom: string;
  operator: IfOperator;
  expected: string;
};

const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

const nodeMetadata: Record<string, { label: string; className: string }> = {
  "input.schema": { label: "INPUT SCHEMA", className: "input-node" },
  agent: { label: "AI AGENT", className: "agent-node" },
  "llm.call": { label: "LLM CALL", className: "llm-node" },
  "code.python": { label: "PYTHON", className: "code-node" },
  "math.add": { label: "MATH", className: "math-node" },
  if: { label: "IF / ELSE", className: "if-node" },
  parallel: { label: "PARALLEL", className: "parallel-node" },
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

function graphLayers(graph: Graph): GraphNode[][] {
  const order = new Map(graph.nodes.map((node, index) => [node.id, index]));
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const indegree = new Map(graph.nodes.map((node) => [node.id, 0]));
  const children = new Map(graph.nodes.map((node) => [node.id, [] as string[]]));
  const level = new Map(graph.nodes.map((node) => [node.id, 0]));

  for (const edge of graph.edges) {
    if (!byId.has(edge.from) || !byId.has(edge.to)) continue;
    children.get(edge.from)?.push(edge.to);
    indegree.set(edge.to, (indegree.get(edge.to) ?? 0) + 1);
  }

  const ready = graph.nodes.filter((node) => indegree.get(node.id) === 0).map((node) => node.id);
  const visited: string[] = [];
  while (ready.length) {
    ready.sort((left, right) => (order.get(left) ?? 0) - (order.get(right) ?? 0));
    const nodeId = ready.shift()!;
    visited.push(nodeId);
    for (const childId of children.get(nodeId) ?? []) {
      level.set(childId, Math.max(level.get(childId) ?? 0, (level.get(nodeId) ?? 0) + 1));
      const nextIndegree = (indegree.get(childId) ?? 0) - 1;
      indegree.set(childId, nextIndegree);
      if (nextIndegree === 0) ready.push(childId);
    }
  }

  if (visited.length !== graph.nodes.length) return graph.nodes.map((node) => [node]);
  const layers: GraphNode[][] = [];
  for (const nodeId of visited) {
    const node = byId.get(nodeId)!;
    const nodeLevel = level.get(nodeId) ?? 0;
    (layers[nodeLevel] ??= []).push(node);
  }
  return layers;
}

function LayerConnections({
  graph,
  sourceLayer,
  targetLayer,
}: {
  graph: Graph;
  sourceLayer: GraphNode[];
  targetLayer: GraphNode[];
}) {
  const sourceIds = new Set(sourceLayer.map((node) => node.id));
  const targetIds = new Set(targetLayer.map((node) => node.id));
  const edges = graph.edges.filter((edge) => sourceIds.has(edge.from) && targetIds.has(edge.to));
  const visualEdges = edges.map((edge, index) => {
    const sourceIndex = sourceLayer.findIndex((node) => node.id === edge.from);
    const targetIndex = targetLayer.findIndex((node) => node.id === edge.to);
    const sourceX = ((sourceIndex + 0.5) * 1000) / sourceLayer.length;
    const targetX = ((targetIndex + 0.5) * 1000) / targetLayer.length;
    const port = edge.port ?? "success";
    return {
      key: `${edge.from}-${edge.to}-${port}-${index}`,
      sourceX,
      targetX,
      labelX: (sourceX + targetX) / 20,
      port,
      label: port === "parallel" ? "PARALLEL" : port.toUpperCase(),
    };
  });

  return (
    <div className="layer-connections" aria-label="Liên kết giữa các node">
      <svg viewBox="0 0 1000 112" preserveAspectRatio="none" role="img">
        {visualEdges.map((edge) => (
            <g className={`visual-edge edge-${edge.port}`} key={edge.key}>
              <path d={`M ${edge.sourceX} 2 C ${edge.sourceX} 45, ${edge.targetX} 67, ${edge.targetX} 110`} />
              <circle cx={edge.sourceX} cy="3" r="4" />
              <circle cx={edge.targetX} cy="109" r="4" />
            </g>
        ))}
      </svg>
      {visualEdges.map((edge) => <span className={`visual-edge-label edge-${edge.port}`} style={{ left: `${edge.labelX}%` }} key={`${edge.key}-label`}>{edge.label}</span>)}
    </div>
  );
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
    instructions: typeof config.instructions === "string"
      ? config.instructions
      : typeof config.prompt === "string"
        ? config.prompt
        : "",
    providerId: typeof config.providerId === "string" ? config.providerId : "",
    model: typeof config.model === "string" ? config.model : "",
    code: typeof config.code === "string" ? config.code : "",
    inputSchema: schemaText(config.inputSchema),
    outputSchema: schemaText(config.outputSchema),
    skillIds: Array.isArray(config.skillIds)
      ? config.skillIds.filter((item): item is string => typeof item === "string")
      : [],
    inheritsWorkflowSkills: !Array.isArray(config.skillIds),
    conditionFrom: isRecord(node.inputs) && isRecord(node.inputs.value) && typeof node.inputs.value.from === "string"
      ? node.inputs.value.from
      : "$input.condition",
    operator: typeof config.operator === "string" ? config.operator as IfOperator : "truthy",
    expected: JSON.stringify(config.expected ?? true),
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

function inputSchemaFields(value: string): string[] {
  try {
    const schema: unknown = JSON.parse(value);
    if (!isRecord(schema) || !isRecord(schema.properties)) return [];
    return Object.keys(schema.properties);
  } catch {
    return [];
  }
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
        : type === "llm.call"
          ? "llm"
        : type === "if"
          ? "decision"
          : type === "parallel"
            ? "parallel"
            : "agent";
  let candidate = base;
  let suffix = 2;
  while (nodes.some((node) => node.id === candidate)) candidate = `${base}_${suffix++}`;
  return candidate;
}

function newNode(type: EditableNodeType, id: string): GraphNode {
  if (type === "if") {
    return {
      id,
      type,
      name: "Điều kiện If / Else",
      note: "Chỉ chạy nhánh true hoặc false theo điều kiện.",
      inputs: { value: { from: "$input.condition" } },
      config: { operator: "truthy" },
    };
  }
  if (type === "parallel") {
    return {
      id,
      type,
      name: "Rẽ nhánh song song",
      note: "Chạy đồng thời tất cả nhánh con và truyền output cha cho từng nhánh.",
    };
  }
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
  if (type === "llm.call") {
    return {
      id,
      type,
      name: "LLM Call",
      note: "Gọi model đúng một lần, không dùng skill hay vòng lặp Agent.",
      config: {
        prompt: "Xử lý dữ liệu đầu vào và trả về kết quả theo output schema.",
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
  if (type === "agent" || type === "llm.call" || type === "code.python" || type === "if" || type === "parallel") {
    const firstOutput = nodes.findIndex((node) => node.type === "output.schema" || node.type === "end");
    return firstOutput < 0 ? nodes.length : firstOutput;
  }
  return nodes.length;
}

export function WorkflowEditor({ value, onChange, disabled = false, skills = [], providers = [] }: WorkflowEditorProps) {
  const parsed = useMemo(() => parseGraph(value), [value]);
  const graph = parsed.graph;
  const layers = useMemo(() => graph ? graphLayers(graph) : [], [graph]);
  const [selectedId, setSelectedId] = useState<string>();
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId);
  const [form, setForm] = useState<NodeForm>();
  const [formError, setFormError] = useState("");
  const [edgeDraft, setEdgeDraft] = useState({ from: "", to: "", port: "success" });
  const agentSkillIds = form?.inheritsWorkflowSkills
    ? skills.map((skill) => skill.id)
    : form?.skillIds ?? [];
  const enabledProviders = providers.filter((provider) => provider.enabled);
  const selectedProvider = enabledProviders.find((provider) => provider.id === form?.providerId);
  const selectedProviderModels = selectedProvider?.settings.selected_models ?? [];
  const promptInputFields = form ? inputSchemaFields(form.inputSchema) : [];

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

  function portsForNode(nodeId: string) {
    const type = graph?.nodes.find((node) => node.id === nodeId)?.type;
    if (type === "if") return ["true", "false"];
    if (type === "parallel") return ["parallel"];
    return ["success"];
  }

  function addEdge() {
    if (!graph || !edgeDraft.from || !edgeDraft.to || edgeDraft.from === edgeDraft.to) return;
    const port = portsForNode(edgeDraft.from).includes(edgeDraft.port)
      ? edgeDraft.port
      : portsForNode(edgeDraft.from)[0];
    if (graph.edges.some((edge) => edge.from === edgeDraft.from && edge.to === edgeDraft.to && (edge.port ?? "success") === port)) return;
    commit({ ...graph, edges: [...graph.edges, { from: edgeDraft.from, to: edgeDraft.to, port }] });
  }

  function removeEdge(index: number) {
    if (!graph) return;
    commit({ ...graph, edges: graph.edges.filter((_, edgeIndex) => edgeIndex !== index) });
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
        const port = type === "if" ? "true" : type === "parallel" ? "parallel" : "success";
        edges.push({ ...directEdge, to: id }, { from: id, to: next.id, port });
      } else {
        const port = type === "if" ? "true" : type === "parallel" ? "parallel" : "success";
        edges.push({ from: previous.id, to: id, port: "success" }, { from: id, to: next.id, port });
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
      if (selectedNode.type === "agent" || selectedNode.type === "llm.call") {
        const isAgent = selectedNode.type === "agent";
        const config = isRecord(selectedNode.config) ? selectedNode.config : {};
        const nextConfig: Record<string, unknown> = {
          ...config,
          inputSchema: parseSchema(form.inputSchema, "Input schema"),
          outputSchema: parseSchema(form.outputSchema, "Output schema"),
        };
        if (isAgent) {
          nextConfig.instructions = form.instructions.trim();
          nextConfig.skillIds = agentSkillIds.filter((skillId) => skills.some((skill) => skill.id === skillId));
        } else {
          if (!form.instructions.trim()) throw new Error("Prompt của LLM Call không được để trống.");
          nextConfig.prompt = form.instructions.trim();
          delete nextConfig.instructions;
          delete nextConfig.skillIds;
        }
        if (form.providerId || form.model) {
          if (!selectedProvider) throw new Error("Hãy chọn một provider đang hoạt động.");
          if (!selectedProviderModels.includes(form.model)) {
            throw new Error("Hãy chọn một model đã đăng ký của provider.");
          }
          nextConfig.providerId = selectedProvider.id;
          nextConfig.model = form.model;
        } else {
          delete nextConfig.providerId;
          delete nextConfig.model;
        }
        updated.config = nextConfig;
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
      if (selectedNode.type === "if") {
        if (!form.conditionFrom.trim()) throw new Error("Nguồn dữ liệu điều kiện không được để trống.");
        const config = isRecord(selectedNode.config) ? selectedNode.config : {};
        const inputs = isRecord(selectedNode.inputs) ? selectedNode.inputs : {};
        const nextConfig: Record<string, unknown> = { ...config, operator: form.operator };
        if (form.operator === "truthy" || form.operator === "falsy") {
          delete nextConfig.expected;
        } else {
          try {
            nextConfig.expected = JSON.parse(form.expected);
          } catch {
            throw new Error("Giá trị so sánh phải là JSON hợp lệ, ví dụ true, 10 hoặc \"done\".");
          }
        }
        updated.inputs = { ...inputs, value: { from: form.conditionFrom.trim() } };
        updated.config = nextConfig;
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
          <button disabled={disabled || !graph} onClick={() => addNode("llm.call")}>+ LLM Call</button>
          <button disabled={disabled || !graph} onClick={() => addNode("code.python")}>+ Python</button>
          <button disabled={disabled || !graph} onClick={() => addNode("if")}>+ If / Else</button>
          <button disabled={disabled || !graph} onClick={() => addNode("parallel")}>+ Song song</button>
          <button disabled={disabled || !graph} onClick={() => addNode("output.schema")}>+ Output schema</button>
        </div>
      </div>

      <div className="builder-grid">
        <section className="flow-preview" aria-label="Danh sách node">
          {layers.length ? layers.map((layer, layerIndex) => {
            const nextLayer = layers[layerIndex + 1];
            return (
            <div className="flow-level-group" key={layerIndex}>
              <div className="flow-level" style={{ gridTemplateColumns: `repeat(${layer.length}, minmax(285px, 1fr))` }}>
                {layer.map((node) => {
                  const metadata = nodeMetadata[node.type ?? ""] ?? { label: (node.type ?? "NODE").toUpperCase(), className: "" };
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
                    </div>
                  );
                })}
              </div>
              {nextLayer && <LayerConnections graph={graph!} sourceLayer={layer} targetLayer={nextLayer} />}
            </div>
            );
          }) : (
            <div className="nodes-empty">
              <strong>Workflow chưa có node</strong>
              <span>Hãy bắt đầu bằng Input schema, LLM Call, Agent, Python hoặc Output schema.</span>
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

              {(selectedNode.type === "agent" || selectedNode.type === "llm.call") && (
                <>
                  <section className="agent-model-picker">
                    <div><strong>{selectedNode.type === "agent" ? "Model của Agent" : "Model của LLM Call"}</strong><span>Chọn model từ các provider đang hoạt động đã đăng ký.</span></div>
                    <label>Provider<select
                      value={form.providerId}
                      onChange={(event) => {
                        const providerId = event.target.value;
                        const provider = enabledProviders.find((item) => item.id === providerId);
                        const models = provider?.settings.selected_models ?? [];
                        const model = provider?.settings.default_model && models.includes(provider.settings.default_model)
                          ? provider.settings.default_model
                          : models[0] ?? "";
                        setForm({ ...form, providerId, model });
                      }}
                    >
                      <option value="">Tự động dùng provider/model mặc định</option>
                      {enabledProviders.map((provider) => (
                        <option value={provider.id} key={provider.id}>{provider.name} ({provider.kind})</option>
                      ))}
                    </select></label>
                    <label>Model<select
                      value={form.model}
                      disabled={!selectedProvider}
                      onChange={(event) => setForm({ ...form, model: event.target.value })}
                    >
                      <option value="">{selectedProvider ? "Chọn model…" : "Chọn provider trước"}</option>
                      {selectedProviderModels.map((model) => <option value={model} key={model}>{model}</option>)}
                    </select></label>
                    {!enabledProviders.length && <p>Chưa có provider đang hoạt động. Hãy đăng ký provider và chọn model trong trang Models.</p>}
                    {form.providerId && !selectedProvider && <p>Provider đã lưu không còn hoạt động. Hãy chọn provider khác.</p>}
                    {selectedProvider && !selectedProviderModels.length && <p>Provider này chưa có model đã đăng ký.</p>}
                    {selectedProvider && form.model && !selectedProviderModels.includes(form.model) && <p>Model đã lưu không còn được bật cho provider này. Hãy chọn model khác.</p>}
                  </section>
                  <label>{selectedNode.type === "agent" ? "Instructions" : "Prompt"}<textarea className="compact-textarea instructions" value={form.instructions} placeholder={selectedNode.type === "agent" ? "Agent cần thực hiện điều gì?" : "Model cần xử lý dữ liệu đầu vào như thế nào?"} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>
                  <section className="prompt-variable-picker">
                    <div><strong>Input trong prompt</strong><span>Output của node trước trở thành input của node này. Chèn biến để dùng trực tiếp trong prompt.</span></div>
                    <div className="prompt-variable-list">
                      {["input", ...promptInputFields.map((field) => `input.${field}`)].map((path) => {
                        const reference = `{{${path}}}`;
                        return <button type="button" key={path} onClick={() => {
                          const separator = form.instructions && !form.instructions.endsWith(" ") ? " " : "";
                          setForm({ ...form, instructions: `${form.instructions}${separator}${reference}` });
                        }}><code>{reference}</code></button>;
                      })}
                    </div>
                    <p>Dùng <code>{"{{input.field}}"}</code> cho một trường, hoặc <code>{"{{input}}"}</code> cho toàn bộ JSON. Có thể dùng đường dẫn lồng nhau như <code>{"{{input.article.title}}"}</code>.</p>
                  </section>
                  {selectedNode.type === "llm.call" && <p className="node-form-hint">Node này gửi một request duy nhất tới model, không nạp skill và không chạy vòng lặp Agent.</p>}
                  {selectedNode.type === "agent" && <section className="agent-skill-picker">
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
                  </section>}
                  <SchemaBuilder label="Input schema" value={form.inputSchema} onChange={(inputSchema) => setForm({ ...form, inputSchema })} />
                  <SchemaBuilder label="Output schema" value={form.outputSchema} onChange={(outputSchema) => setForm({ ...form, outputSchema })} />
                </>
              )}

              {selectedNode.type === "if" && (
                <section className="branch-config">
                  <div><strong>Điều kiện rẽ nhánh</strong><span>Đúng chạy cổng true, sai chạy cổng false.</span></div>
                  <label>Nguồn dữ liệu<input value={form.conditionFrom} placeholder="$input.approved" onChange={(event) => setForm({ ...form, conditionFrom: event.target.value })} /></label>
                  <label>Phép so sánh<select value={form.operator} onChange={(event) => setForm({ ...form, operator: event.target.value as IfOperator })}>
                    <option value="truthy">Có giá trị / true</option>
                    <option value="falsy">Không có giá trị / false</option>
                    <option value="equals">Bằng</option>
                    <option value="notEquals">Khác</option>
                    <option value="greaterThan">Lớn hơn</option>
                    <option value="greaterThanOrEqual">Lớn hơn hoặc bằng</option>
                    <option value="lessThan">Nhỏ hơn</option>
                    <option value="lessThanOrEqual">Nhỏ hơn hoặc bằng</option>
                  </select></label>
                  {form.operator !== "truthy" && form.operator !== "falsy" && <label>Giá trị so sánh (JSON)<input value={form.expected} placeholder="true" onChange={(event) => setForm({ ...form, expected: event.target.value })} /></label>}
                </section>
              )}

              {selectedNode.type === "parallel" && (
                <section className="branch-config">
                  <div><strong>Rẽ nhánh song song</strong><span>Tạo ít nhất hai kết nối cổng parallel. Mỗi nhánh nhận cùng output của node này.</span></div>
                </section>
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

      {graph && (
        <section className="connection-editor">
          <div className="connection-heading">
            <div><strong>Kết nối và nhánh</strong><span>If dùng cổng true/false; Parallel dùng cổng parallel.</span></div>
            <div className="connection-form">
              <select value={edgeDraft.from} onChange={(event) => {
                const from = event.target.value;
                setEdgeDraft({ ...edgeDraft, from, port: portsForNode(from)[0] });
              }}><option value="">Node nguồn…</option>{graph.nodes.map((node) => <option value={node.id} key={node.id}>{node.id}</option>)}</select>
              <select value={edgeDraft.port} disabled={!edgeDraft.from} onChange={(event) => setEdgeDraft({ ...edgeDraft, port: event.target.value })}>{portsForNode(edgeDraft.from).map((port) => <option value={port} key={port}>{port}</option>)}</select>
              <select value={edgeDraft.to} onChange={(event) => setEdgeDraft({ ...edgeDraft, to: event.target.value })}><option value="">Node đích…</option>{graph.nodes.filter((node) => node.id !== edgeDraft.from).map((node) => <option value={node.id} key={node.id}>{node.id}</option>)}</select>
              <button type="button" disabled={disabled || !edgeDraft.from || !edgeDraft.to} onClick={addEdge}>+ Kết nối</button>
            </div>
          </div>
          <div className="connection-list">
            {graph.edges.map((edge, index) => <div className="connection-row" key={`${edge.from}-${edge.to}-${edge.port ?? "success"}-${index}`}><code>{edge.from}</code><b>{edge.port ?? "success"}</b><span>→</span><code>{edge.to}</code><button type="button" disabled={disabled} onClick={() => removeEdge(index)}>Xóa</button></div>)}
            {!graph.edges.length && <p>Chưa có kết nối. Thêm edge để xác định thứ tự và nhánh chạy.</p>}
          </div>
        </section>
      )}

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
