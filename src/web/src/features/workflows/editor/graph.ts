import type {
  EditableNodeType,
  Graph,
  GraphNode,
  IfOperator,
  NodeForm,
} from "./types";

const emptyObjectSchema = {
  type: "object",
  properties: {},
  additionalProperties: false,
};

export const defaultCanvasNodeSize = { width: 322, height: 165 };

export type OccupiedCanvasNode = {
  position: { x: number; y: number };
  width: number;
  height: number;
};

export function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

export function parseGraph(value: string): { graph?: Graph; error?: string } {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed) || !Array.isArray(parsed.nodes) || !Array.isArray(parsed.edges)) {
      return { error: "Draft phải có hai mảng nodes và edges." };
    }
    if (!parsed.nodes.every((node) => (
      isRecord(node)
      && typeof node.id === "string"
      && (node.type === undefined || typeof node.type === "string")
    ))) {
      return { error: "Mỗi node phải là object, có id dạng chuỗi và type hợp lệ." };
    }
    if (!parsed.edges.every((edge) => (
      isRecord(edge)
      && typeof edge.from === "string"
      && typeof edge.to === "string"
    ))) {
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

  const ready = graph.nodes
    .filter((node) => indegree.get(node.id) === 0)
    .map((node) => node.id);
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

export function isNodePosition(value: unknown): value is { x: number; y: number } {
  return isRecord(value) && Number.isFinite(value.x) && Number.isFinite(value.y);
}

export function layoutNodePositions(graph: Graph) {
  const positions = new Map<string, { x: number; y: number }>();
  const layers = graphLayers(graph);
  const horizontalStep = 370;
  const verticalStep = 245;
  for (const [layerIndex, layer] of layers.entries()) {
    const layerWidth = Math.max(0, (layer.length - 1) * horizontalStep);
    for (const [nodeIndex, node] of layer.entries()) {
      positions.set(node.id, {
        x: nodeIndex * horizontalStep - layerWidth / 2,
        y: layerIndex * verticalStep,
      });
    }
  }
  return positions;
}

export function availableNodePosition(
  desired: { x: number; y: number },
  occupied: OccupiedCanvasNode[],
) {
  const horizontalStep = 370;
  const verticalStep = 215;
  const gap = 24;
  const isFree = (candidate: { x: number; y: number }) => occupied.every((node) => (
    candidate.x + defaultCanvasNodeSize.width + gap <= node.position.x
    || node.position.x + node.width + gap <= candidate.x
    || candidate.y + defaultCanvasNodeSize.height + gap <= node.position.y
    || node.position.y + node.height + gap <= candidate.y
  ));

  if (isFree(desired)) return desired;
  for (let ring = 1; ring <= 12; ring += 1) {
    const offsets = [
      { x: ring, y: 0 },
      { x: 0, y: ring },
      { x: -ring, y: 0 },
      { x: 0, y: -ring },
    ];
    for (let y = -ring; y <= ring; y += 1) {
      for (let x = -ring; x <= ring; x += 1) {
        if (Math.abs(x) !== ring && Math.abs(y) !== ring) continue;
        if ((x === 0 || y === 0) && Math.abs(x + y) === ring) continue;
        offsets.push({ x, y });
      }
    }
    for (const offset of offsets) {
      const candidate = {
        x: desired.x + offset.x * horizontalStep,
        y: desired.y + offset.y * verticalStep,
      };
      if (isFree(candidate)) return candidate;
    }
  }
  return { x: desired.x + occupied.length * 32, y: desired.y + occupied.length * 32 };
}

export function createsCycle(graph: Graph, from: string, to: string) {
  const children = new Map<string, string[]>();
  for (const edge of graph.edges) {
    const targets = children.get(edge.from) ?? [];
    targets.push(edge.to);
    children.set(edge.from, targets);
  }
  const pending = [to];
  const visited = new Set<string>();
  while (pending.length) {
    const nodeId = pending.pop()!;
    if (nodeId === from) return true;
    if (visited.has(nodeId)) continue;
    visited.add(nodeId);
    pending.push(...(children.get(nodeId) ?? []));
  }
  return false;
}

export function portsForType(type?: string) {
  if (type === "output.schema" || type === "end") return [];
  if (type === "if") return ["true", "false"];
  if (type === "parallel") return ["parallel"];
  return ["success"];
}

function schemaText(value: unknown) {
  return JSON.stringify(isRecord(value) ? value : emptyObjectSchema, null, 2);
}

export function formFromNode(node: GraphNode): NodeForm {
  const config = isRecord(node.config) ? node.config : {};
  const inputs = isRecord(node.inputs) ? node.inputs : {};
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
    runtime: typeof config.runtime === "string" && config.runtime ? config.runtime : "default",
    providerId: typeof config.providerId === "string" ? config.providerId : "",
    model: typeof config.model === "string" ? config.model : "",
    code: typeof config.code === "string" ? config.code : "",
    inputSchema: schemaText(config.inputSchema),
    outputSchema: schemaText(config.outputSchema),
    skillIds: Array.isArray(config.skillIds)
      ? config.skillIds.filter((item): item is string => typeof item === "string")
      : [],
    mcpServerIds: Array.isArray(config.mcpServerIds)
      ? config.mcpServerIds.filter((item): item is string => typeof item === "string")
      : [],
    conditionFrom: isRecord(node.inputs)
      && isRecord(node.inputs.value)
      && typeof node.inputs.value.from === "string"
      ? node.inputs.value.from
      : "$input.condition",
    operator: typeof config.operator === "string" ? config.operator as IfOperator : "truthy",
    expected: JSON.stringify(config.expected ?? true),
    outputBindings: Object.fromEntries(
      Object.entries(inputs).flatMap(([field, binding]) => (
        isRecord(binding) && typeof binding.from === "string" ? [[field, binding.from]] : []
      )),
    ),
  };
}

export function parseSchema(value: string, label: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch {
    throw new Error(`${label} phải là JSON hợp lệ.`);
  }
  if (!isRecord(parsed)) throw new Error(`${label} phải là một JSON object.`);
  return parsed;
}

export function inputSchemaFields(value: string): string[] {
  try {
    const schema: unknown = JSON.parse(value);
    if (!isRecord(schema) || !isRecord(schema.properties)) return [];
    return Object.keys(schema.properties);
  } catch {
    return [];
  }
}

export function outputReferenceOptions(graph: Graph, selectedNodeId: string) {
  const references: string[] = [];
  for (const node of graph.nodes) {
    if (node.id === selectedNodeId) continue;
    if (node.type === "input.schema") {
      for (const field of inputSchemaFields(schemaText(node.schema))) {
        references.push(`$input.${field}`);
      }
      continue;
    }
    const config = isRecord(node.config) ? node.config : {};
    const outputSchema = node.type === "output.schema" ? node.schema : config.outputSchema;
    const fields = inputSchemaFields(schemaText(outputSchema));
    if (node.type === "math.add") {
      const outputKey = typeof config.outputKey === "string" ? config.outputKey : "sum";
      fields.push(outputKey);
    }
    references.push(`$nodes.${node.id}.output`);
    for (const field of new Set(fields)) {
      references.push(`$nodes.${node.id}.output.${field}`);
    }
  }
  return [...new Set(references)];
}

export function uniqueNodeId(nodes: GraphNode[], type: EditableNodeType) {
  const base = type === "input.schema"
    ? "start"
    : type === "output.schema"
      ? "end"
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

export function newNode(
  type: EditableNodeType,
  id: string,
  typeVersion: number,
): GraphNode {
  if (type === "if") {
    return {
      id,
      type,
      typeVersion,
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
      typeVersion,
      name: "Rẽ nhánh song song",
      note: "Chạy đồng thời tất cả nhánh con và truyền output cha cho từng nhánh.",
    };
  }
  if (type === "agent") {
    return {
      id,
      type,
      typeVersion,
      name: "Agent",
      note: "Mô tả ngắn nhiệm vụ của agent.",
      config: {
        runtime: "default",
        instructions: "",
        skillIds: [],
        mcpServerIds: [],
        inputSchema: emptyObjectSchema,
        outputSchema: emptyObjectSchema,
      },
    };
  }
  if (type === "llm.call") {
    return {
      id,
      type,
      typeVersion,
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
      typeVersion,
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
    typeVersion,
    name: input ? "Bắt đầu" : "Kết thúc",
    note: input
      ? "Định nghĩa các biến đầu vào ban đầu của workflow."
      : "Định nghĩa các biến workflow trả về.",
    schema: emptyObjectSchema,
    ...(input ? {} : { inputs: {} }),
  };
}

export function nodeInsertIndex(nodes: GraphNode[], type: EditableNodeType) {
  if (type === "input.schema") {
    const firstNonInput = nodes.findIndex((node) => node.type !== "input.schema");
    return firstNonInput < 0 ? nodes.length : firstNonInput;
  }
  if (["agent", "llm.call", "code.python", "if", "parallel"].includes(type)) {
    const firstOutput = nodes.findIndex((node) => (
      node.type === "output.schema" || node.type === "end"
    ));
    return firstOutput < 0 ? nodes.length : firstOutput;
  }
  return nodes.length;
}
