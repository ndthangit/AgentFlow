import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  type Connection,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  availableNodePosition,
  createsCycle,
  defaultCanvasNodeSize,
  formFromNode,
  inputSchemaFields,
  isNodePosition,
  isRecord,
  layoutNodePositions,
  newNode,
  nodeInsertIndex,
  outputReferenceOptions,
  parseGraph,
  parseSchema,
  portsForType,
  type OccupiedCanvasNode,
  uniqueNodeId,
} from "./graph";
import { SchemaBuilder } from "./SchemaBuilder";
import type {
  EditableNodeType,
  Graph,
  GraphEdge,
  GraphNode,
  IfOperator,
  NodeForm,
  WorkflowEditorProps,
  WorkflowFlowEdge,
  WorkflowFlowNode,
} from "./types";
import { workflowNodeTypes } from "./WorkflowCanvasNode";

function WorkflowEditorView({
  value,
  onChange,
  onSave,
  disabled = false,
  skills = [],
  providers = [],
  mcpServers = [],
  nodeTypes = [],
}: WorkflowEditorProps) {
  const parsed = useMemo(() => parseGraph(value), [value]);
  const graph = parsed.graph;
  const [selectedId, setSelectedId] = useState<string>();
  const selectedNode = graph?.nodes.find((node) => node.id === selectedId);
  const [form, setForm] = useState<NodeForm>();
  const [formError, setFormError] = useState("");
  const [connectionSource, setConnectionSource] = useState<{ from: string; port: string }>();
  const [connectionMessage, setConnectionMessage] = useState("Kéo từ cổng dưới node sang node đích; chọn đường nối rồi nhấn Delete để xóa.");
  const [selectedEdgeId, setSelectedEdgeId] = useState<string>();
  const [isJsonOpen, setIsJsonOpen] = useState(false);
  const [flowNodes, setFlowNodes] = useState<WorkflowFlowNode[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<WorkflowFlowNode, WorkflowFlowEdge>>();
  const canvasRef = useRef<HTMLDivElement>(null);
  const didFitInitialView = useRef(false);
  const agentSkillIds = form?.skillIds ?? [];
  const agentMcpServerIds = form?.mcpServerIds ?? [];
  const enabledMcpServers = mcpServers.filter((server) => server.enabled);
  const enabledProviders = providers.filter((provider) => provider.enabled);
  const nodeTypesByName = useMemo(
    () => new Map(nodeTypes.map((definition) => [definition.type, definition])),
    [nodeTypes],
  );
  const selectedProvider = enabledProviders.find((provider) => provider.id === form?.providerId);
  const selectedProviderModels = selectedProvider?.settings.selected_models ?? [];
  const promptInputFields = form ? inputSchemaFields(form.inputSchema) : [];
  const outputReferences = useMemo(
    () => graph && selectedNode ? outputReferenceOptions(graph, selectedNode.id) : [],
    [graph, selectedNode],
  );
  const flowEdges = useMemo<WorkflowFlowEdge[]>(() => graph ? graph.edges.map((edge, index) => {
    const port = edge.port ?? "success";
    const stroke = port === "true" ? "#68a638" : port === "false" ? "#c05d5d" : port === "parallel" ? "#169d84" : "#71867b";
    const id = `${index}:${edge.from}:${edge.to}:${port}`;
    const selected = id === selectedEdgeId;
    return {
      id,
      source: edge.from,
      target: edge.to,
      sourceHandle: port,
      targetHandle: "target",
      type: "smoothstep",
      label: port.toUpperCase(),
      labelStyle: { fill: stroke, fontSize: 9, fontWeight: 700 },
      labelBgStyle: { fill: "#f8faf7", stroke, strokeWidth: 1 },
      labelBgPadding: [7, 4],
      labelBgBorderRadius: 12,
      markerEnd: { type: MarkerType.ArrowClosed, color: stroke },
      style: { stroke, strokeWidth: selected ? 4 : 2, strokeDasharray: port === "false" ? "7 5" : undefined },
      selected,
      deletable: !disabled,
      data: { graphEdge: edge, index },
    };
  }) : [], [disabled, graph, selectedEdgeId]);

  useEffect(() => {
    if (!graph) {
      setFlowNodes([]);
      return;
    }
    const fallbackPositions = layoutNodePositions(graph);
    setFlowNodes((current) => {
      const currentById = new Map(current.map((node) => [node.id, node]));
      const occupied: OccupiedCanvasNode[] = [];
      const nextNodes: WorkflowFlowNode[] = [];
      for (const node of graph.nodes) {
        const previous = currentById.get(node.id);
        const desiredPosition = isNodePosition(node.position)
          ? node.position
          : previous?.position ?? fallbackPositions.get(node.id) ?? { x: 0, y: 0 };
        const position = availableNodePosition(desiredPosition, occupied);
        const measuredWidth = previous?.measured?.width ?? defaultCanvasNodeSize.width;
        const measuredHeight = previous?.measured?.height ?? defaultCanvasNodeSize.height;
        occupied.push({ position, width: measuredWidth, height: measuredHeight });
        nextNodes.push({
          id: node.id,
          type: "workflowNode",
          position,
          selected: previous?.selected ?? false,
          draggable: !disabled,
          selectable: true,
          connectable: !disabled,
          deletable: false,
          data: {
            graphNode: node,
            ports: portsForType(node.type),
            disabled,
            onConfigure: openNode,
            onDelete: deleteNode,
          },
        });
      }
      return nextNodes;
    });
  }, [value, disabled]);

  useEffect(() => {
    if (!flowInstance || !flowNodes.length || didFitInitialView.current) return;
    didFitInitialView.current = true;
    window.requestAnimationFrame(() => flowInstance.fitView({ padding: 0.18, maxZoom: 1 }));
  }, [flowInstance, flowNodes.length]);

  useEffect(() => {
    setForm(selectedNode ? formFromNode(selectedNode) : undefined);
    setFormError("");
  }, [selectedNode]);

  useEffect(() => {
    if (!selectedNode) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeNodeInspector();
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [selectedNode]);

  function closeNodeInspector() {
    setSelectedId(undefined);
    setFormError("");
  }

  function commit(next: Graph) {
    const serialized = JSON.stringify(next, null, 2);
    onChange(serialized);
    onSave(serialized);
  }

  function connectNodes(from: string, to: string, requestedPort: string) {
    if (!graph || disabled) return false;
    if (!from || !to || from === to) {
      setConnectionMessage("Node nguồn và node đích phải khác nhau.");
      return false;
    }
    const source = graph.nodes.find((node) => node.id === from);
    const ports = portsForType(source?.type);
    const port = ports.includes(requestedPort) ? requestedPort : ports[0];
    if (!port) {
      setConnectionMessage("Node nguồn này không có cổng đầu ra.");
      return false;
    }
    if (graph.edges.some((edge) => edge.from === from && edge.to === to && (edge.port ?? "success") === port)) {
      setConnectionMessage(`Liên kết ${from} → ${to} qua cổng ${port} đã tồn tại.`);
      return false;
    }
    if (createsCycle(graph, from, to)) {
      setConnectionMessage("Không thể tạo liên kết vì sẽ hình thành vòng lặp trong workflow.");
      return false;
    }
    commit({ ...graph, edges: [...graph.edges, { from, to, port }] });
    setConnectionMessage(`Đã nối ${from} → ${to} qua cổng ${port}.`);
    return true;
  }

  function connectFlowNodes(connection: Connection) {
    if (!connection.source || !connection.target) return;
    connectNodes(connection.source, connection.target, connection.sourceHandle ?? "success");
    setConnectionSource(undefined);
  }

  function openNode(nodeId: string) {
    setConnectionSource(undefined);
    setSelectedEdgeId(undefined);
    setSelectedId(nodeId);
  }

  function removeFlowEdges(edgesToRemove: WorkflowFlowEdge[]) {
    if (!graph || disabled) return;
    const graphEdges = edgesToRemove
      .map((edge) => edge.data?.graphEdge)
      .filter((edge): edge is GraphEdge => Boolean(edge));
    if (!graphEdges.length) return;
    const removed = new Set(graphEdges);
    commit({ ...graph, edges: graph.edges.filter((edge) => !removed.has(edge)) });
    setSelectedEdgeId(undefined);
    setConnectionMessage(graphEdges.length === 1
      ? `Đã xóa kết nối ${graphEdges[0].from} → ${graphEdges[0].to}.`
      : `Đã xóa ${graphEdges.length} kết nối.`);
  }

  function addNode(type: EditableNodeType) {
    if (!graph) return;
    const definition = nodeTypesByName.get(type);
    if (!definition) {
      setConnectionMessage(`Node ${type} khÃ´ng Ä‘Æ°á»£c System hiá»‡n táº¡i há»— trá»£.`);
      return;
    }
    if (type === "input.schema" || type === "output.schema") {
      const existing = graph.nodes.find((node) => node.type === type);
      if (existing) {
        setSelectedId(existing.id);
        return;
      }
    }
    const id = uniqueNodeId(graph.nodes, type);
    const index = nodeInsertIndex(graph.nodes, type);
    const nodes = [...graph.nodes];
    const bounds = canvasRef.current?.getBoundingClientRect();
    const desiredPosition = flowInstance && bounds
      ? flowInstance.screenToFlowPosition({ x: bounds.left + bounds.width / 2 - 145, y: bounds.top + bounds.height / 2 - 80 })
      : { x: graph.nodes.length * 36, y: graph.nodes.length * 36 };
    const position = availableNodePosition(desiredPosition, flowNodes.map((node) => ({
      position: node.position,
      width: node.measured?.width ?? defaultCanvasNodeSize.width,
      height: node.measured?.height ?? defaultCanvasNodeSize.height,
    })));
    nodes.splice(index, 0, { ...newNode(type, id, definition.typeVersion), position });
    commit({ ...graph, nodes });
    setConnectionMessage(`Đã thêm ${id}. Kéo cổng của node nguồn sang node này để tạo kết nối.`);
    setSelectedId(id);
  }

  function arrangeNodes() {
    if (!graph) return;
    const positions = layoutNodePositions(graph);
    commit({
      ...graph,
      nodes: graph.nodes.map((node) => ({ ...node, position: positions.get(node.id) ?? { x: 0, y: 0 } })),
    });
    window.setTimeout(() => flowInstance?.fitView({ padding: 0.18, duration: 350 }), 0);
  }

  function saveNodePosition(nodeId: string, position: { x: number; y: number }) {
    if (!graph || disabled) return;
    commit({
      ...graph,
      nodes: graph.nodes.map((node) => node.id === nodeId ? { ...node, position } : node),
    });
  }

  function deleteNode(node: GraphNode) {
    if (node.type === "input.schema" || node.type === "output.schema") return;
    if (!graph || !window.confirm(`Xóa node “${typeof node.name === "string" ? node.name : node.id}”?`)) return;
    const edges = graph.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id);
    commit({ ...graph, nodes: graph.nodes.filter((item) => item.id !== node.id), edges });
    setConnectionMessage(`Đã xóa ${node.id} và các kết nối trực tiếp của node.`);
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
        position: flowNodes.find((node) => node.id === selectedNode.id)?.position ?? selectedNode.position,
      };
      if (selectedNode.type === "input.schema" || selectedNode.type === "output.schema") {
        const schema = parseSchema(form.schema, "Schema");
        updated.schema = schema;
        if (selectedNode.type === "output.schema") {
          const properties = isRecord(schema.properties) ? schema.properties : {};
          const fields = Object.keys(properties);
          const missing = fields.filter((field) => !form.outputBindings[field]?.trim());
          if (missing.length) throw new Error(`Hãy chọn nguồn dữ liệu trả về cho: ${missing.join(", ")}.`);
          updated.inputs = Object.fromEntries(fields.map((field) => [
            field,
            { from: form.outputBindings[field].trim() },
          ]));
        }
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
          nextConfig.runtime = form.runtime;
          nextConfig.instructions = form.instructions.trim();
          nextConfig.skillIds = agentSkillIds.filter((skillId) => skills.some((skill) => skill.id === skillId));
          nextConfig.mcpServerIds = agentMcpServerIds.filter((serverId) => enabledMcpServers.some((server) => server.id === serverId));
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
      setSelectedId(undefined);
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
          <span>Thêm rồi kéo node tới vị trí mong muốn; kéo từ cổng dưới node sang cổng trên node đích</span>
        </div>
        <div className="node-add-actions">
          {nodeTypesByName.has("input.schema") && <button disabled={disabled || !graph || graph.nodes.some((node) => node.type === "input.schema")} onClick={() => addNode("input.schema")}>+ Start</button>}
          {nodeTypesByName.has("agent") && <button disabled={disabled || !graph} onClick={() => addNode("agent")}>+ Agent</button>}
          {nodeTypesByName.has("llm.call") && <button disabled={disabled || !graph} onClick={() => addNode("llm.call")}>+ LLM Call</button>}
          {nodeTypesByName.has("code.python") && <button disabled={disabled || !graph} onClick={() => addNode("code.python")}>+ Python</button>}
          {nodeTypesByName.has("if") && <button disabled={disabled || !graph} onClick={() => addNode("if")}>+ If / Else</button>}
          {nodeTypesByName.has("parallel") && <button disabled={disabled || !graph} onClick={() => addNode("parallel")}>+ Song song</button>}
          {nodeTypesByName.has("output.schema") && <button disabled={disabled || !graph || graph.nodes.some((node) => node.type === "output.schema")} onClick={() => addNode("output.schema")}>+ End</button>}
          <span className="node-toolbar-divider" aria-hidden="true" />
          <button disabled={disabled || !graph?.nodes.length} onClick={arrangeNodes}>Sắp xếp</button>
          <button className="canvas-tool-button" onClick={() => setIsJsonOpen(true)}>JSON</button>
        </div>
      </div>

      <div className="builder-canvas" ref={canvasRef}>
        <span className={connectionSource ? "canvas-connection-hint active" : "canvas-connection-hint"}>{connectionMessage}</span>
        <span className="canvas-pan-hint" aria-hidden="true">Kéo nền để di chuyển · cuộn để zoom</span>
        {graph ? (
          <ReactFlow<WorkflowFlowNode, WorkflowFlowEdge>
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={workflowNodeTypes}
            onInit={setFlowInstance}
            onNodesChange={(changes) => setFlowNodes((nodes) => applyNodeChanges(changes, nodes))}
            onNodeClick={(event, node) => {
              setSelectedEdgeId(undefined);
              if (!(event.target as Element).closest("button, .react-flow__handle")) openNode(node.id);
            }}
            onEdgeClick={(_event, edge) => {
              setSelectedEdgeId(edge.id);
              setConnectionMessage(`Đã chọn kết nối ${edge.source} → ${edge.target}. Nhấn Delete để xóa.`);
            }}
            onPaneClick={() => setSelectedEdgeId(undefined)}
            onNodeDragStop={(_event, node) => saveNodePosition(node.id, node.position)}
            onConnect={connectFlowNodes}
            onConnectStart={(_event, params) => {
              if (!params.nodeId) return;
              const port = params.handleId ?? "success";
              setConnectionSource({ from: params.nodeId, port });
              setConnectionMessage(`Đang nối từ ${params.nodeId} qua cổng ${port}… đưa chuột tới mép để di chuyển canvas.`);
            }}
            onConnectEnd={() => setConnectionSource(undefined)}
            onEdgesDelete={removeFlowEdges}
            autoPanOnConnect
            autoPanOnNodeDrag
            autoPanSpeed={18}
            panOnDrag
            zoomOnScroll
            zoomOnPinch
            minZoom={0.2}
            maxZoom={1.8}
            fitView
            fitViewOptions={{ padding: 0.18, maxZoom: 1 }}
            nodesDraggable={!disabled}
            nodesConnectable={!disabled}
            elementsSelectable
            deleteKeyCode={disabled ? null : ["Backspace", "Delete"]}
            proOptions={{ hideAttribution: true }}
            aria-label="Canvas workflow kéo thả"
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cdd6d0" />
            <MiniMap pannable zoomable nodeColor="#78936b" maskColor="rgba(238, 241, 237, .72)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        ) : (
          <div className="nodes-empty builder-invalid">
            <strong>Không thể hiển thị workflow</strong>
            <span>{parsed.error}</span>
          </div>
        )}
      </div>

      {selectedNode && form && (
          <aside className="node-inspector node-drawer" aria-labelledby="node-drawer-title">
            <div className="panel-title node-drawer-title">
              <span id="node-drawer-title">Cấu hình · {typeof selectedNode.name === "string" ? selectedNode.name : selectedNode.id}</span>
              <code>{selectedNode.type}</code>
              <button type="button" aria-label="Đóng cửa sổ cấu hình node" onClick={closeNodeInspector}>×</button>
            </div>
            <div className="node-form">
              <label>ID<input value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} /></label>
              <label>Tên node<input value={form.name} maxLength={160} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label>Note<textarea className="compact-textarea" value={form.note} placeholder="Mô tả vai trò của node…" onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>

              {selectedNode.type === "input.schema" && (
                <SchemaBuilder label="Biến đầu vào ban đầu" value={form.schema} onChange={(schema) => setForm({ ...form, schema })} />
              )}

              {selectedNode.type === "output.schema" && (
                <>
                  <SchemaBuilder label="Biến workflow trả về" value={form.schema} onChange={(schema) => setForm({ ...form, schema })} />
                  <section className="output-binding-editor">
                    <div><strong>Nguồn dữ liệu trả về</strong><span>Ánh xạ mỗi biến End tới output của node trước hoặc biến từ Start.</span></div>
                    <datalist id={`output-references-${selectedNode.id}`}>
                      {outputReferences.map((reference) => <option value={reference} key={reference} />)}
                    </datalist>
                    {inputSchemaFields(form.schema).map((field) => (
                      <label key={field}>{field}<input
                        list={`output-references-${selectedNode.id}`}
                        value={form.outputBindings[field] ?? ""}
                        placeholder="$nodes.agent.output.result"
                        onChange={(event) => setForm({
                          ...form,
                          outputBindings: { ...form.outputBindings, [field]: event.target.value },
                        })}
                      /></label>
                    ))}
                    {!inputSchemaFields(form.schema).length && <p>Thêm biến trả về ở phần trên để cấu hình nguồn dữ liệu.</p>}
                  </section>
                </>
              )}

              {(selectedNode.type === "agent" || selectedNode.type === "llm.call") && (
                <>
                  {selectedNode.type === "agent" && <section className="agent-model-picker">
                    <div><strong>Agent runtime</strong><span>Runtime đã chọn trong src/agents/&lt;runtime-name&gt; được tạo mới và xóa sau mỗi lần thực thi node.</span></div>
                    <label>Runtime<input list="agent-runtime-options" value={form.runtime} placeholder="default" onChange={(event) => {
                      const runtime = event.target.value;
                      setForm({ ...form, runtime, ...(runtime === "direct" ? { mcpServerIds: [] } : {}) });
                    }} /></label>
                    <datalist id="agent-runtime-options">
                      <option value="default">Default container runtime</option>
                      <option value="codex">OpenAI Codex CLI runtime</option>
                      <option value="opencode">OpenCode SDK runtime</option>
                      <option value="agent">Legacy default alias</option>
                      <option value="direct">Direct provider</option>
                    </datalist>
                    <p>Nhập runtime id đã đăng ký trong <code>AGENT_RUNTIME_IMAGES</code>.</p>
                  </section>}
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
                      <option value="">Dùng provider/model mặc định đã đăng ký</option>
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
                    {selectedNode.type === "agent" && form.runtime !== "direct" && <p>Provider, model và API key đã lưu được truyền tạm thời vào container của lần chạy này; Agent không lấy cấu hình model từ .env.</p>}
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
                        if (event.target.value) setForm({ ...form, skillIds: [...agentSkillIds, event.target.value] });
                      }}
                    >
                      <option value="">+ Thêm skill…</option>
                      {skills.filter((skill) => !agentSkillIds.includes(skill.id)).map((skill) => <option value={skill.id} key={skill.id}>{skill.name}</option>)}
                    </select>
                    <div className="agent-skill-list">
                      {agentSkillIds.flatMap((skillId) => {
                        const skill = skills.find((item) => item.id === skillId);
                        return skill ? [<span key={skill.id}><b>{skill.name}</b><small>{skill.slug} · v{skill.version}</small><button type="button" aria-label={`Bỏ skill ${skill.name}`} onClick={() => setForm({ ...form, skillIds: agentSkillIds.filter((id) => id !== skill.id) })}>×</button></span>] : [];
                      })}
                      {!agentSkillIds.some((skillId) => skills.some((skill) => skill.id === skillId)) && <p>Chưa chọn skill. Hãy thêm skill trong trang quản lý rồi chọn tại đây.</p>}
                    </div>
                  </section>}
                  {selectedNode.type === "agent" && form.runtime !== "direct" && <section className="agent-skill-picker">
                    <div><strong>MCP của Agent</strong><span>Agent chỉ kết nối tới các MCP server được chọn cho node này.</span></div>
                    <select
                      value=""
                      disabled={!enabledMcpServers.some((server) => !agentMcpServerIds.includes(server.id))}
                      onChange={(event) => {
                        if (event.target.value) setForm({ ...form, mcpServerIds: [...agentMcpServerIds, event.target.value] });
                      }}
                    >
                      <option value="">+ Thêm MCP…</option>
                      {enabledMcpServers.filter((server) => !agentMcpServerIds.includes(server.id)).map((server) => <option value={server.id} key={server.id}>{server.name}</option>)}
                    </select>
                    <div className="agent-skill-list">
                      {agentMcpServerIds.flatMap((serverId) => {
                        const server = mcpServers.find((item) => item.id === serverId);
                        return server ? [<span key={server.id}><b>{server.name}</b><small>{server.transport === "streamable_http" ? "HTTP" : "SSE"} · {server.slug}</small><button type="button" aria-label={`Bỏ MCP ${server.name}`} onClick={() => setForm({ ...form, mcpServerIds: agentMcpServerIds.filter((id) => id !== server.id) })}>×</button></span>] : [];
                      })}
                      {!agentMcpServerIds.some((serverId) => mcpServers.some((server) => server.id === serverId)) && <p>Chưa chọn MCP. Hãy đăng ký server trong trang MCP rồi chọn tại đây.</p>}
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
              <div className="node-drawer-actions">
                <button type="button" className="cancel-node" onClick={closeNodeInspector}>Hủy</button>
                <button type="button" className="save-node" disabled={disabled} onClick={saveNode}>Lưu thay đổi node</button>
              </div>
            </div>
          </aside>
      )}

      {isJsonOpen && (
        <div className="workflow-tool-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) setIsJsonOpen(false);
        }}>
          <section className="workflow-tool-panel raw-json-panel" role="dialog" aria-modal="true" aria-labelledby="json-panel-title">
            <div className="workflow-tool-heading">
              <div><strong id="json-panel-title">Graph definition</strong><span>Chỉnh trực tiếp JSON nâng cao của workflow.</span></div>
              <button type="button" aria-label="Đóng trình chỉnh JSON" onClick={() => setIsJsonOpen(false)}>×</button>
            </div>
            <textarea
              value={value}
              onChange={(event) => onChange(event.target.value)}
              onBlur={() => onSave(value)}
              spellCheck={false}
            />
          </section>
        </div>
      )}
    </div>
  );
}

export const WorkflowEditor = memo(WorkflowEditorView);
