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
  const [connectionMessage, setConnectionMessage] = useState("Drag from a node's lower port to the target node; select an edge and press Delete to remove it.");
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
      setConnectionMessage("The source and target nodes must be different.");
      return false;
    }
    const source = graph.nodes.find((node) => node.id === from);
    const ports = portsForType(source?.type);
    const port = ports.includes(requestedPort) ? requestedPort : ports[0];
    if (!port) {
      setConnectionMessage("This source node has no output port.");
      return false;
    }
    if (graph.edges.some((edge) => edge.from === from && edge.to === to && (edge.port ?? "success") === port)) {
      setConnectionMessage(`The ${from} → ${to} connection through port ${port} already exists.`);
      return false;
    }
    if (createsCycle(graph, from, to)) {
      setConnectionMessage("This connection cannot be created because it would introduce a workflow cycle.");
      return false;
    }
    commit({ ...graph, edges: [...graph.edges, { from, to, port }] });
    setConnectionMessage(`Connected ${from} → ${to} through port ${port}.`);
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
      ? `Removed the ${graphEdges[0].from} → ${graphEdges[0].to} connection.`
      : `Removed ${graphEdges.length} connections.`);
  }

  function addNode(type: EditableNodeType) {
    if (!graph) return;
    const definition = nodeTypesByName.get(type);
    if (!definition) {
      setConnectionMessage(`Node type ${type} is not supported by the current system.`);
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
    setConnectionMessage(`Added ${id}. Drag a source node port to this node to create a connection.`);
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
    if (!graph || !window.confirm(`Delete node “${typeof node.name === "string" ? node.name : node.id}”?`)) return;
    const edges = graph.edges.filter((edge) => edge.from !== node.id && edge.to !== node.id);
    commit({ ...graph, nodes: graph.nodes.filter((item) => item.id !== node.id), edges });
    setConnectionMessage(`Deleted ${node.id} and its direct connections.`);
    setSelectedId(undefined);
  }

  function saveNode() {
    if (!graph || !selectedNode || !form) return;
    const id = form.id.trim();
    if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(id)) {
      setFormError("The ID must start with a letter and contain only letters, numbers, _ or -.");
      return;
    }
    if (graph.nodes.some((node) => node !== selectedNode && node.id === id)) {
      setFormError("This ID is already used by another node.");
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
          if (missing.length) throw new Error(`Select a return data source for: ${missing.join(", ")}.`);
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
          if (!form.instructions.trim()) throw new Error("The LLM Call prompt cannot be empty.");
          nextConfig.prompt = form.instructions.trim();
          delete nextConfig.instructions;
          delete nextConfig.skillIds;
        }
        if (form.providerId || form.model) {
          if (!selectedProvider) throw new Error("Select an active provider.");
          if (!selectedProviderModels.includes(form.model)) {
            throw new Error("Select a model registered for this provider.");
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
        if (!form.code.trim()) throw new Error("Python code cannot be empty.");
        updated.config = {
          ...config,
          language: "python",
          code: form.code,
          inputSchema: parseSchema(form.inputSchema, "Input schema"),
          outputSchema: parseSchema(form.outputSchema, "Output schema"),
        };
      }
      if (selectedNode.type === "if") {
        if (!form.conditionFrom.trim()) throw new Error("The condition data source cannot be empty.");
        const config = isRecord(selectedNode.config) ? selectedNode.config : {};
        const inputs = isRecord(selectedNode.inputs) ? selectedNode.inputs : {};
        const nextConfig: Record<string, unknown> = { ...config, operator: form.operator };
        if (form.operator === "truthy" || form.operator === "falsy") {
          delete nextConfig.expected;
        } else {
          try {
            nextConfig.expected = JSON.parse(form.expected);
          } catch {
            throw new Error("The comparison value must be valid JSON, for example true, 10, or \"done\".");
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
      setFormError(error instanceof Error ? error.message : "Unable to save the node.");
    }
  }

  return (
    <div className="workflow-editor">
      <div className="node-toolbar">
        <div>
          <strong>Nodes</strong>
          <span>Add and drag nodes into position; connect a lower port to the target node's upper port</span>
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
          <button disabled={disabled || !graph?.nodes.length} onClick={arrangeNodes}>Arrange</button>
          <button className="canvas-tool-button" onClick={() => setIsJsonOpen(true)}>JSON</button>
        </div>
      </div>

      <div className="builder-canvas" ref={canvasRef}>
        <span className={connectionSource ? "canvas-connection-hint active" : "canvas-connection-hint"}>{connectionMessage}</span>
        <span className="canvas-pan-hint" aria-hidden="true">Drag the canvas to pan · scroll to zoom</span>
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
              setConnectionMessage(`Selected connection ${edge.source} → ${edge.target}. Press Delete to remove it.`);
            }}
            onPaneClick={() => setSelectedEdgeId(undefined)}
            onNodeDragStop={(_event, node) => saveNodePosition(node.id, node.position)}
            onConnect={connectFlowNodes}
            onConnectStart={(_event, params) => {
              if (!params.nodeId) return;
              const port = params.handleId ?? "success";
              setConnectionSource({ from: params.nodeId, port });
              setConnectionMessage(`Connecting from ${params.nodeId} through port ${port}… move the pointer to an edge to pan the canvas.`);
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
            aria-label="Drag-and-drop workflow canvas"
          >
            <Background variant={BackgroundVariant.Dots} gap={22} size={1} color="#cdd6d0" />
            <MiniMap pannable zoomable nodeColor="#78936b" maskColor="rgba(238, 241, 237, .72)" />
            <Controls showInteractive={false} />
          </ReactFlow>
        ) : (
          <div className="nodes-empty builder-invalid">
            <strong>Unable to display the workflow</strong>
            <span>{parsed.error}</span>
          </div>
        )}
      </div>

      {selectedNode && form && (
          <aside className="node-inspector node-drawer" aria-labelledby="node-drawer-title">
            <div className="panel-title node-drawer-title">
              <span id="node-drawer-title">Configure · {typeof selectedNode.name === "string" ? selectedNode.name : selectedNode.id}</span>
              <code>{selectedNode.type}</code>
              <button type="button" aria-label="Close node configuration" onClick={closeNodeInspector}>×</button>
            </div>
            <div className="node-form">
              <label>ID<input value={form.id} onChange={(event) => setForm({ ...form, id: event.target.value })} /></label>
              <label>Node name<input value={form.name} maxLength={160} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label>Note<textarea className="compact-textarea" value={form.note} placeholder="Describe the node's role…" onChange={(event) => setForm({ ...form, note: event.target.value })} /></label>

              {selectedNode.type === "input.schema" && (
                <SchemaBuilder label="Initial input variables" value={form.schema} onChange={(schema) => setForm({ ...form, schema })} />
              )}

              {selectedNode.type === "output.schema" && (
                <>
                  <SchemaBuilder label="Workflow return variables" value={form.schema} onChange={(schema) => setForm({ ...form, schema })} />
                  <section className="output-binding-editor">
                    <div><strong>Return data sources</strong><span>Map each End variable to a previous node output or a Start variable.</span></div>
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
                    {!inputSchemaFields(form.schema).length && <p>Add a return variable above to configure its data source.</p>}
                  </section>
                </>
              )}

              {(selectedNode.type === "agent" || selectedNode.type === "llm.call") && (
                <>
                  {selectedNode.type === "agent" && <section className="agent-model-picker">
                    <div><strong>Agent runtime</strong><span>The selected runtime in src/agents/&lt;runtime-name&gt; is created and removed for each node execution.</span></div>
                    <label>Runtime<input list="agent-runtime-options" value={form.runtime} placeholder="default" onChange={(event) => {
                      const runtime = event.target.value;
                      setForm({ ...form, runtime, ...(runtime === "direct" ? { mcpServerIds: [] } : {}) });
                    }} /></label>
                    <datalist id="agent-runtime-options">
                      <option value="default">Default container runtime</option>
                      <option value="codex">OpenAI Codex CLI runtime</option>
                      <option value="opencode">OpenCode SDK runtime</option>
                      <option value="openhands">OpenHands Software Agent SDK runtime</option>
                      <option value="agent">Legacy default alias</option>
                      <option value="direct">Direct provider</option>
                    </datalist>
                    <p>Enter a runtime ID registered in <code>AGENT_RUNTIME_IMAGES</code>.</p>
                  </section>}
                  <section className="agent-model-picker">
                    <div><strong>{selectedNode.type === "agent" ? "Agent model" : "LLM Call model"}</strong><span>Select a model from the registered active providers.</span></div>
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
                      <option value="">Use the registered default provider/model</option>
                      {enabledProviders.map((provider) => (
                        <option value={provider.id} key={provider.id}>{provider.name} ({provider.kind})</option>
                      ))}
                    </select></label>
                    <label>Model<select
                      value={form.model}
                      disabled={!selectedProvider}
                      onChange={(event) => setForm({ ...form, model: event.target.value })}
                    >
                      <option value="">{selectedProvider ? "Select a model…" : "Select a provider first"}</option>
                      {selectedProviderModels.map((model) => <option value={model} key={model}>{model}</option>)}
                    </select></label>
                    {!enabledProviders.length && <p>No active provider is available. Register a provider and select a model on the Models page.</p>}
                    {form.providerId && !selectedProvider && <p>The saved provider is no longer active. Select another provider.</p>}
                    {selectedProvider && !selectedProviderModels.length && <p>This provider has no registered models.</p>}
                    {selectedProvider && form.model && !selectedProviderModels.includes(form.model) && <p>The saved model is no longer enabled for this provider. Select another model.</p>}
                    {selectedNode.type === "agent" && form.runtime !== "direct" && <p>The saved provider, model, and API key are passed temporarily to the run container; the agent does not load model configuration from .env.</p>}
                  </section>
                  <label>{selectedNode.type === "agent" ? "Instructions" : "Prompt"}<textarea className="compact-textarea instructions" value={form.instructions} placeholder={selectedNode.type === "agent" ? "What should the agent do?" : "How should the model process the input?"} onChange={(event) => setForm({ ...form, instructions: event.target.value })} /></label>
                  <section className="prompt-variable-picker">
                    <div><strong>Prompt input</strong><span>The previous node output becomes this node's input. Insert variables to use them directly in the prompt.</span></div>
                    <div className="prompt-variable-list">
                      {["input", ...promptInputFields.map((field) => `input.${field}`)].map((path) => {
                        const reference = `{{${path}}}`;
                        return <button type="button" key={path} onClick={() => {
                          const separator = form.instructions && !form.instructions.endsWith(" ") ? " " : "";
                          setForm({ ...form, instructions: `${form.instructions}${separator}${reference}` });
                        }}><code>{reference}</code></button>;
                      })}
                    </div>
                    <p>Use <code>{"{{input.field}}"}</code> for one field or <code>{"{{input}}"}</code> for the full JSON object. Nested paths such as <code>{"{{input.article.title}}"}</code> are supported.</p>
                  </section>
                  {selectedNode.type === "llm.call" && <p className="node-form-hint">This node sends one request to the model without loading skills or running an agent loop.</p>}
                  {selectedNode.type === "agent" && <section className="agent-skill-picker">
                    <div><strong>Agent skills</strong><span>Each agent receives only the skills selected here.</span></div>
                    <select
                      value=""
                      disabled={!skills.some((skill) => !agentSkillIds.includes(skill.id))}
                      onChange={(event) => {
                        if (event.target.value) setForm({ ...form, skillIds: [...agentSkillIds, event.target.value] });
                      }}
                    >
                      <option value="">+ Add skill…</option>
                      {skills.filter((skill) => !agentSkillIds.includes(skill.id)).map((skill) => <option value={skill.id} key={skill.id}>{skill.name}</option>)}
                    </select>
                    <div className="agent-skill-list">
                      {agentSkillIds.flatMap((skillId) => {
                        const skill = skills.find((item) => item.id === skillId);
                        return skill ? [<span key={skill.id}><b>{skill.name}</b><small>{skill.slug} · v{skill.version}</small><button type="button" aria-label={`Remove skill ${skill.name}`} onClick={() => setForm({ ...form, skillIds: agentSkillIds.filter((id) => id !== skill.id) })}>×</button></span>] : [];
                      })}
                      {!agentSkillIds.some((skillId) => skills.some((skill) => skill.id === skillId)) && <p>No skill selected. Add a skill on the management page, then select it here.</p>}
                    </div>
                  </section>}
                  {selectedNode.type === "agent" && form.runtime !== "direct" && <section className="agent-skill-picker">
                    <div><strong>Agent MCP servers</strong><span>The agent connects only to the MCP servers selected for this node.</span></div>
                    <select
                      value=""
                      disabled={!enabledMcpServers.some((server) => !agentMcpServerIds.includes(server.id))}
                      onChange={(event) => {
                        if (event.target.value) setForm({ ...form, mcpServerIds: [...agentMcpServerIds, event.target.value] });
                      }}
                    >
                      <option value="">+ Add MCP…</option>
                      {enabledMcpServers.filter((server) => !agentMcpServerIds.includes(server.id)).map((server) => <option value={server.id} key={server.id}>{server.name}</option>)}
                    </select>
                    <div className="agent-skill-list">
                      {agentMcpServerIds.flatMap((serverId) => {
                        const server = mcpServers.find((item) => item.id === serverId);
                        return server ? [<span key={server.id}><b>{server.name}</b><small>{server.transport === "streamable_http" ? "HTTP" : "SSE"} · {server.slug}</small><button type="button" aria-label={`Remove MCP ${server.name}`} onClick={() => setForm({ ...form, mcpServerIds: agentMcpServerIds.filter((id) => id !== server.id) })}>×</button></span>] : [];
                      })}
                      {!agentMcpServerIds.some((serverId) => mcpServers.some((server) => server.id === serverId)) && <p>No MCP server selected. Register one on the MCP page, then select it here.</p>}
                    </div>
                  </section>}
                  <SchemaBuilder label="Input schema" value={form.inputSchema} onChange={(inputSchema) => setForm({ ...form, inputSchema })} />
                  <SchemaBuilder label="Output schema" value={form.outputSchema} onChange={(outputSchema) => setForm({ ...form, outputSchema })} />
                </>
              )}

              {selectedNode.type === "if" && (
                <section className="branch-config">
                  <div><strong>Branch condition</strong><span>True uses the true port; false uses the false port.</span></div>
                  <label>Data source<input value={form.conditionFrom} placeholder="$input.approved" onChange={(event) => setForm({ ...form, conditionFrom: event.target.value })} /></label>
                  <label>Comparison<select value={form.operator} onChange={(event) => setForm({ ...form, operator: event.target.value as IfOperator })}>
                    <option value="truthy">Has a value / true</option>
                    <option value="falsy">No value / false</option>
                    <option value="equals">Equals</option>
                    <option value="notEquals">Does not equal</option>
                    <option value="greaterThan">Greater than</option>
                    <option value="greaterThanOrEqual">Greater than or equal</option>
                    <option value="lessThan">Less than</option>
                    <option value="lessThanOrEqual">Less than or equal</option>
                  </select></label>
                  {form.operator !== "truthy" && form.operator !== "falsy" && <label>Comparison value (JSON)<input value={form.expected} placeholder="true" onChange={(event) => setForm({ ...form, expected: event.target.value })} /></label>}
                </section>
              )}

              {selectedNode.type === "parallel" && (
                <section className="branch-config">
                  <div><strong>Parallel branches</strong><span>Create at least two parallel-port connections. Each branch receives the same output from this node.</span></div>
                </section>
              )}

              {selectedNode.type === "code.python" && (
                <>
                  <label>Python code<textarea className="code-textarea" value={form.code} spellCheck={false} onChange={(event) => setForm({ ...form, code: event.target.value })} /></label>
                  <p className="node-form-hint">Define a main(inputs) function that returns a JSON object.</p>
                  <SchemaBuilder label="Input schema" value={form.inputSchema} onChange={(inputSchema) => setForm({ ...form, inputSchema })} />
                  <SchemaBuilder label="Output schema" value={form.outputSchema} onChange={(outputSchema) => setForm({ ...form, outputSchema })} />
                </>
              )}

              {formError && <p className="form-error">{formError}</p>}
              <div className="node-drawer-actions">
                <button type="button" className="cancel-node" onClick={closeNodeInspector}>Cancel</button>
                <button type="button" className="save-node" disabled={disabled} onClick={saveNode}>Save node changes</button>
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
              <div><strong id="json-panel-title">Graph definition</strong><span>Edit the workflow's advanced JSON directly.</span></div>
              <button type="button" aria-label="Close JSON editor" onClick={() => setIsJsonOpen(false)}>×</button>
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
