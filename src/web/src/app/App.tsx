import { useEffect, useMemo, useRef, useState } from "react";

import { McpPage } from "../features/mcp/McpPage";
import { ModelsPage } from "../features/models/ModelsPage";
import { WorkflowEditor } from "../features/workflows/editor/WorkflowEditor";
import { workflowNodeLabel, workflowNodeTypes } from "../features/workflows/summary";
import { starterDraft } from "../features/workflows/templates";
import { api } from "../shared/api";
import { keycloak } from "../shared/auth";
import type {
  FlowRun,
  LlmProvider,
  McpServer,
  NodeTypeDefinition,
  RunStep,
  Skill,
  Workflow,
  WorkflowVersion,
} from "../shared/types";

type AppTab = "workflows" | "skills" | "mcp" | "models";

const emptySkill = { slug: "", name: "", description: "", instructions: "" };
const workflowQueryParameter = "workflow";

function workflowIdFromLocation() {
  return new URLSearchParams(window.location.search).get(workflowQueryParameter) ?? undefined;
}

function updateWorkflowLocation(workflowId?: string, replace = false) {
  const url = new URL(window.location.href);
  const currentWorkflowId = url.searchParams.get(workflowQueryParameter) ?? undefined;
  if (currentWorkflowId === workflowId) return;
  if (workflowId) {
    url.searchParams.set(workflowQueryParameter, workflowId);
  } else {
    url.searchParams.delete(workflowQueryParameter);
  }
  window.history[replace ? "replaceState" : "pushState"](
    { ...window.history.state, workflowId },
    "",
    url,
  );
}

function comparableGraph(value: Record<string, unknown>) {
  const graph = structuredClone(value);
  delete graph.skills;
  delete graph.mcpServers;
  if (Array.isArray(graph.nodes)) {
    graph.nodes = graph.nodes.map((rawNode) => {
      if (!rawNode || typeof rawNode !== "object" || Array.isArray(rawNode)) return rawNode;
      const node = { ...rawNode } as Record<string, unknown>;
      delete node.typeVersion;
      return node;
    });
  }
  return graph;
}

function canonicalJson(value: unknown): string {
  if (value === undefined) return "undefined";
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "undefined";
}

function isCurrentDraftPublished(workflow: Workflow, version: WorkflowVersion) {
  return canonicalJson(comparableGraph(workflow.draft))
    === canonicalJson(comparableGraph(version.graph));
}

export function App() {
  const [activeTab, setActiveTab] = useState<AppTab>("workflows");
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string | undefined>(workflowIdFromLocation);
  const [editor, setEditor] = useState(JSON.stringify(starterDraft, null, 2));
  const [name, setName] = useState("");
  const [message, setMessage] = useState("Loading data…");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState<WorkflowVersion>();
  const [run, setRun] = useState<FlowRun>();
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [runSteps, setRunSteps] = useState<RunStep[]>([]);
  const [runStepsLoading, setRunStepsLoading] = useState(false);
  const [isRunInputOpen, setIsRunInputOpen] = useState(false);
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const [runHistoryLoading, setRunHistoryLoading] = useState(false);
  const [stoppingRunIds, setStoppingRunIds] = useState<Set<string>>(() => new Set());
  const [runDetailsRefreshKey, setRunDetailsRefreshKey] = useState(0);
  const [runInput, setRunInput] = useState("{}");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [mcpServers, setMcpServers] = useState<McpServer[]>([]);
  const [nodeTypes, setNodeTypes] = useState<NodeTypeDefinition[]>([]);
  const [skillForm, setSkillForm] = useState(emptySkill);
  const [isAddingSkill, setIsAddingSkill] = useState(false);
  const [editingSkill, setEditingSkill] = useState<Skill>();
  const draftRevisions = useRef(new Map<string, number>());
  const queuedDrafts = useRef(new Map<string, string>());
  const draftSaveOperations = useRef(new Map<string, Promise<void>>());
  const draftSaveQueue = useRef<Promise<void>>(Promise.resolve());
  const selectedIdRef = useRef<string | undefined>(undefined);

  selectedIdRef.current = selectedId;

  const selected = useMemo(
    () => workflows.find((workflow) => workflow.id === selectedId),
    [selectedId, workflows],
  );
  const enabledSkills = useMemo(() => skills.filter((skill) => skill.enabled), [skills]);
  const enabledMcpServers = useMemo(() => mcpServers.filter((server) => server.enabled), [mcpServers]);
  const latestCompletedOutput = useMemo(
    () => runs.find((item) => item.status === "succeeded" && item.output)?.output,
    [runs],
  );

  async function refreshWorkflows(select?: string) {
    const items = await api.listWorkflows();
    setWorkflows(items);
    const requestedId = select ?? selectedIdRef.current;
    const nextId = items.some((workflow) => workflow.id === requestedId) ? requestedId : undefined;
    setSelectedId(nextId);
    if (select && nextId) {
      updateWorkflowLocation(nextId);
    } else if (requestedId && !nextId) {
      updateWorkflowLocation(undefined, true);
    }
    setMessage(items.length ? "Ready" : "Create your first workflow to get started");
  }

  async function refreshSkills() {
    setSkills(await api.listSkills());
  }

  async function refreshProviders() {
    setProviders(await api.listLlmProviders());
  }

  async function refreshMcpServers() {
    setMcpServers(await api.listMcpServers());
  }

  async function refreshNodeTypes() {
    setNodeTypes(await api.listNodeTypes());
  }

  useEffect(() => {
    Promise.all([refreshWorkflows(), refreshSkills(), refreshProviders(), refreshMcpServers(), refreshNodeTypes()]).catch((error: Error) =>
      setMessage(error.message),
    );
  }, []);

  useEffect(() => {
    const restoreLocation = () => {
      setSelectedId(workflowIdFromLocation());
      setActiveTab("workflows");
      setIsRunHistoryOpen(false);
    };
    window.addEventListener("popstate", restoreLocation);
    return () => window.removeEventListener("popstate", restoreLocation);
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const serializedDraft = JSON.stringify(selected.draft, null, 2);
    draftRevisions.current.set(selected.id, selected.revision);
    queuedDrafts.current.set(selected.id, canonicalJson(selected.draft));
    setEditor(serializedDraft);
    setVersion(undefined);
    setRun(undefined);
    setStoppingRunIds(new Set());
    setRuns([]);
    setIsRunInputOpen(false);
    setIsRunHistoryOpen(false);
    const nodes = selected.draft.nodes;
    const isSumWorkflow = Array.isArray(nodes) && nodes.some((node) => (
      node && typeof node === "object" && "type" in node && node.type === "math.add"
    ));
    const isAgentPythonDemo = Array.isArray(nodes) && nodes.some((node) => (
      node && typeof node === "object" && "type" in node && node.type === "code.python"
    ));
    const isSimpleSummaryWorkflow = Array.isArray(nodes) && nodes.some((node) => (
      node && typeof node === "object" && "id" in node && node.id === "summary_agent"
    ));
    setRunInput(isSumWorkflow
      ? JSON.stringify({ num1: 1, num2: 3 }, null, 2)
      : isAgentPythonDemo
        ? JSON.stringify({ topic: "Applications of AI in education" }, null, 2)
        : isSimpleSummaryWorkflow
          ? JSON.stringify({
              text: "AgentFlow helps teams design, run, and monitor structured AI workflows.",
            }, null, 2)
          : "{}");
    api.getCurrentWorkflowVersion(selected.id)
      .then((currentVersion) => {
        if (!cancelled) {
          const published = currentVersion
            && isCurrentDraftPublished(selected, currentVersion)
            ? currentVersion
            : undefined;
          setVersion(published);
          if (currentVersion && !published) {
            setMessage("The draft is saved but contains unpublished changes");
          }
        }
      })
      .catch((error: Error) => {
        if (!cancelled) setMessage(error.message);
      });
    return () => {
      cancelled = true;
    };
  }, [selected?.id]);

  useEffect(() => {
    if (!run) {
      setRunSteps([]);
      setRunStepsLoading(false);
      return;
    }
    let cancelled = false;
    let polling: number | undefined;
    const runId = run.id;
    let previousStatus = run.status;
    const showInitialStepLoading = isRunHistoryOpen;
    if (showInitialStepLoading) {
      setRunSteps([]);
      setRunStepsLoading(true);
    } else {
      setRunStepsLoading(false);
    }

    const loadRun = async (initial: boolean) => {
      try {
        const [latest, steps] = await Promise.all([
          api.getRun(runId),
          isRunHistoryOpen ? api.listRunSteps(runId) : Promise.resolve(undefined),
        ]);
        if (cancelled) return;

        setRun((current) => current
          && current.id === latest.id
          && current.status === latest.status
          && current.updated_at === latest.updated_at
          ? current
          : latest);
        setRuns((items) => {
          let changed = false;
          const next = items.map((item) => {
            if (item.id !== latest.id) return item;
            if (item.status === latest.status && item.updated_at === latest.updated_at) return item;
            changed = true;
            return latest;
          });
          return changed ? next : items;
        });
        if (steps) {
          setRunSteps((current) => JSON.stringify(current) === JSON.stringify(steps) ? current : steps);
        }

        if (latest.status !== previousStatus) {
          if (latest.status === "succeeded") setMessage("The worker completed the workflow");
          if (latest.status === "failed") setMessage("The workflow failed — open run history to review each step error");
          if (latest.status === "cancelled") {
            setStoppingRunIds((current) => {
              if (!current.has(latest.id)) return current;
              const next = new Set(current);
              next.delete(latest.id);
              return next;
            });
            setMessage("Workflow stopped");
          }
          previousStatus = latest.status;
        }
        if (latest.status === "pending" || latest.status === "running") {
          polling = window.setTimeout(() => void loadRun(false), 1200);
        }
      } catch (error) {
        if (cancelled) return;
        setMessage(error instanceof Error ? error.message : "Could not update the run status");
        if (previousStatus === "pending" || previousStatus === "running") {
          polling = window.setTimeout(() => void loadRun(false), 2500);
        }
      } finally {
        if (!cancelled && initial && showInitialStepLoading) setRunStepsLoading(false);
      }
    };
    void loadRun(true);
    return () => {
      cancelled = true;
      if (polling !== undefined) window.clearTimeout(polling);
    };
  }, [run?.id, isRunHistoryOpen, runDetailsRefreshKey]);

  useEffect(() => {
    if (!isRunHistoryOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsRunHistoryOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [isRunHistoryOpen]);

  useEffect(() => {
    if (!isRunHistoryOpen || !selected) return;
    let cancelled = false;
    let polling: number | undefined;

    const pollRunHistory = async () => {
      try {
        const items = await api.listWorkflowRuns(selected.id);
        if (cancelled) return;
        setRuns(items);
        setRun((current) => current
          ? items.find((item) => item.id === current.id) ?? items[0]
          : items[0]);
        setStoppingRunIds((current) => {
          const activeIds = new Set(
            items
              .filter((item) => ["pending", "running"].includes(item.status))
              .map((item) => item.id),
          );
          const next = new Set([...current].filter((id) => activeIds.has(id)));
          return next.size === current.size ? current : next;
        });
      } catch (error) {
        if (!cancelled) {
          setMessage(error instanceof Error ? error.message : "Could not update run history");
        }
      } finally {
        if (!cancelled) polling = window.setTimeout(() => void pollRunHistory(), 1500);
      }
    };

    polling = window.setTimeout(() => void pollRunHistory(), 1500);
    return () => {
      cancelled = true;
      if (polling !== undefined) window.clearTimeout(polling);
    };
  }, [isRunHistoryOpen, selected?.id]);

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  async function stopRun(flowRun: FlowRun) {
    setStoppingRunIds((current) => new Set(current).add(flowRun.id));
    try {
      const stopped = await api.cancelRun(flowRun.id);
      setRun((current) => current?.id === stopped.id ? stopped : current);
      setRuns((items) => items.map((item) => item.id === stopped.id ? stopped : item));
      setRunDetailsRefreshKey((key) => key + 1);
      setMessage(stopped.status === "cancelled"
        ? `Stopped session ${stopped.session_id}`
        : `Stopping session ${stopped.session_id}…`);
      if (!["pending", "running"].includes(stopped.status)) {
        setStoppingRunIds((current) => {
          const next = new Set(current);
          next.delete(stopped.id);
          return next;
        });
      }
    } catch (error) {
      setStoppingRunIds((current) => {
        const next = new Set(current);
        next.delete(flowRun.id);
        return next;
      });
      setMessage(error instanceof Error ? error.message : "Could not stop the workflow");
    }
  }

  function parseDraft(value = editor) {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("The draft must be a JSON object");
    }
    return parsed as Record<string, unknown>;
  }

  function changeEditor(value: string) {
    setEditor(value);
    setVersion(undefined);
    setMessage("Changes are waiting to be autosaved…");
  }

  function persistDraft(value: string): Promise<void> {
    if (!selected) return Promise.resolve();

    const workflowId = selected.id;
    const fallbackRevision = selected.revision;
    const draft = parseDraft(value);
    const fingerprint = canonicalJson(draft);
    if (queuedDrafts.current.get(workflowId) === fingerprint) {
      return draftSaveOperations.current.get(workflowId) ?? Promise.resolve();
    }

    queuedDrafts.current.set(workflowId, fingerprint);
    if (selectedIdRef.current === workflowId) {
      setVersion(undefined);
      setMessage("Autosaving draft…");
    }

    const operation = draftSaveQueue.current
      .catch(() => undefined)
      .then(async () => {
        if (queuedDrafts.current.get(workflowId) !== fingerprint) return;
        const expectedRevision = draftRevisions.current.get(workflowId) ?? fallbackRevision;
        try {
          const saved = await api.updateDraft(workflowId, expectedRevision, draft);
          draftRevisions.current.set(workflowId, saved.revision);
          setWorkflows((items) => items.map((item) => (
            item.id === saved.id ? saved : item
          )));
          if (selectedIdRef.current === workflowId) {
            setMessage(`Autosaved revision ${saved.revision} · publish again to apply it`);
          }
        } catch (error) {
          if (queuedDrafts.current.get(workflowId) === fingerprint) {
            queuedDrafts.current.delete(workflowId);
          }
          throw error;
        }
      });

    draftSaveOperations.current.set(workflowId, operation);
    draftSaveQueue.current = operation.catch(() => undefined);
    return operation;
  }

  function queueDraftSave(value: string) {
    try {
      void persistDraft(value).catch((error: unknown) => {
        setMessage(error instanceof Error ? error.message : "Could not autosave the draft");
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not autosave the draft");
    }
  }

  async function validateCurrentDraft() {
    if (!selected) return;
    await persistDraft(editor);
    const result = await api.validate(selected.id);
    setMessage(result.valid ? "The graph is valid" : result.errors.join(" · "));
  }

  async function publishCurrentDraft() {
    if (!selected) return;
    await persistDraft(editor);
    const published = await api.publish(selected.id);
    setVersion(published);
    setMessage(`Saved the draft and published version ${published.version}`);
  }

  function parseRunInput() {
    const parsed: unknown = JSON.parse(runInput);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Run input must be a JSON object");
    }
    return parsed as Record<string, unknown>;
  }

  function openWorkflow(workflowId: string) {
    setSelectedId(workflowId);
    setActiveTab("workflows");
    updateWorkflowLocation(workflowId);
  }

  function showWorkflowList() {
    setSelectedId(undefined);
    setActiveTab("workflows");
    setIsRunHistoryOpen(false);
    updateWorkflowLocation();
  }

  function openRunHistory() {
    if (!selected) return;
    setIsRunHistoryOpen(true);
    void perform(async () => {
      await loadRunHistory();
    });
  }

  async function loadRunHistory(isRefresh = false) {
    if (!selected) return;
    setRunHistoryLoading(true);
    try {
      const items = await api.listWorkflowRuns(selected.id);
      setRuns(items);
      setRun((current) => current ? items.find((item) => item.id === current.id) ?? items[0] : items[0]);
      setStoppingRunIds((current) => {
        const activeIds = new Set(
          items
            .filter((item) => ["pending", "running"].includes(item.status))
            .map((item) => item.id),
        );
        const next = new Set([...current].filter((id) => activeIds.has(id)));
        return next.size === current.size ? current : next;
      });
      if (isRefresh) setRunDetailsRefreshKey((current) => current + 1);
      setMessage(items.length
        ? isRefresh ? "Run history refreshed" : `Loaded the latest ${items.length} runs`
        : "This workflow has no run history");
    } finally {
      setRunHistoryLoading(false);
    }
  }

  async function copySessionId(sessionId: string) {
    await navigator.clipboard.writeText(sessionId);
    setMessage(`Copied Langfuse session ID ${sessionId}`);
  }

  function closeSkillForm() {
    if (busy) return;
    setIsAddingSkill(false);
    setEditingSkill(undefined);
    setSkillForm(emptySkill);
  }

  function openNewSkillForm() {
    setEditingSkill(undefined);
    setSkillForm(emptySkill);
    setIsAddingSkill(true);
    setMessage("Enter the details for the new skill");
  }

  function openEditSkillForm(skill: Skill) {
    setIsAddingSkill(false);
    setEditingSkill(skill);
    setSkillForm({
      slug: skill.slug,
      name: skill.name,
      description: skill.description,
      instructions: skill.instructions,
    });
    setMessage(`Editing “${skill.name}”`);
  }

  function removeWorkflow(workflow: Workflow) {
    if (!window.confirm(`Delete workflow “${workflow.name}” and all its versions and run history?`)) return;
    void perform(async () => {
      await api.deleteWorkflow(workflow.id);
      setSelectedId((current) => current === workflow.id ? undefined : current);
      await refreshWorkflows();
      setMessage(`Deleted workflow “${workflow.name}”`);
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>AF</span>
          <div><strong>AgentFlow</strong><small>Control plane</small></div>
        </div>

        <nav className="primary-nav" aria-label="Primary navigation">
          <button className={activeTab === "workflows" ? "nav-tab active" : "nav-tab"} onClick={showWorkflowList}>
            <span className="nav-icon">⌘</span><span><strong>Workflows</strong><small>Design and run flows</small></span><b>{workflows.length}</b>
          </button>
          <button className={activeTab === "skills" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("skills")}>
            <span className="nav-icon">◆</span><span><strong>Skills</strong><small>Manage agent skills</small></span><b>{enabledSkills.length}</b>
          </button>
          <button className={activeTab === "mcp" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("mcp")}>
            <span className="nav-icon">M</span><span><strong>MCP</strong><small>External tools for agents</small></span><b>{enabledMcpServers.length}</b>
          </button>
          <button className={activeTab === "models" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("models")}>
            <span className="nav-icon">◉</span><span><strong>Models</strong><small>LLM providers and models</small></span><b>{providers.filter((item) => item.enabled).length}</b>
          </button>
        </nav>

        {activeTab === "workflows" ? (
          <>
            <div className="sidebar-note workflow-sidebar-note">
              <strong>{workflows.length} workflows created</strong>
              <p>Open Workflows to view, edit, or delete a workflow.</p>
              <button onClick={showWorkflowList}>View all workflows</button>
            </div>
            <form className="new-flow" onSubmit={(event) => {
              event.preventDefault();
              if (!name.trim()) return;
              void perform(async () => {
                const created = await api.createWorkflow(name.trim());
                setName("");
                await refreshWorkflows(created.id);
                setMessage("Workflow created");
              });
            }}>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="New workflow name" maxLength={160} />
              <button disabled={busy || !name.trim()}>Create</button>
            </form>
          </>
        ) : activeTab === "skills" ? (
          <div className="sidebar-note">
            <strong>Skill library</strong>
            <p>Skills installed here appear in each workflow's skill picker.</p>
          </div>
        ) : activeTab === "mcp" ? (
          <div className="sidebar-note">
            <strong>MCP registry</strong>
            <p>Register remote MCP servers and grant each Agent only the selected tools.</p>
          </div>
        ) : (
          <div className="sidebar-note">
            <strong>Model providers</strong>
            <p>Connect model providers and load their catalogs for use by agents.</p>
          </div>
        )}
        <div className="sidebar-account">
          <span className="status-dot" />
          <div>
            <strong>{keycloak.tokenParsed?.preferred_username ?? "developer"}</strong>
            <small>Authenticated</small>
          </div>
          <button
            type="button"
            onClick={() => void keycloak.logout({ redirectUri: window.location.origin })}
          >
            Sign out
          </button>
        </div>
      </aside>

      <main className={activeTab === "workflows" ? selected ? "workspace workflow-studio-workspace" : "workspace" : "workspace skills-workspace"}>
        <header>
          <div className="page-heading">
            {activeTab === "workflows" && selected && <button className="back-to-workflows" onClick={showWorkflowList}>← All workflows</button>}
            <p className="eyebrow">{activeTab === "workflows" ? "WORKFLOW STUDIO" : activeTab === "skills" ? "SKILL LIBRARY" : activeTab === "mcp" ? "MCP REGISTRY" : "MODEL CATALOG"}</p>
            <h1>{activeTab === "workflows" ? selected?.name ?? "Workflows" : activeTab === "skills" ? "Skills" : activeTab === "mcp" ? "MCP" : "Models"}</h1>
          </div>
        </header>

        {activeTab === "workflows" ? !selected ? (
          <section className="workflow-catalog-page">
            <div className="workflow-catalog-hero">
              <div>
                <p className="eyebrow">YOUR AUTOMATIONS</p>
                <h2>Your workflows</h2>
                <p>Select a workflow to open the canvas, configure nodes, publish it, and view run history.</p>
              </div>
              <span className="workflow-total"><strong>{workflows.length}</strong> workflow</span>
            </div>

            <div className="workflow-card-grid">
              {workflows.map((workflow) => {
                const nodeTypes = workflowNodeTypes(workflow);
                return (
                  <article className="workflow-card" key={workflow.id}>
                    <button className="workflow-card-main" onClick={() => openWorkflow(workflow.id)}>
                      <span className="workflow-card-icon">↗</span>
                      <span className="workflow-card-content">
                        <span className="workflow-card-topline"><b>Revision {workflow.revision}</b><small>{nodeTypes.length} node</small></span>
                        <strong>{workflow.name}</strong>
                        <span className="workflow-node-chips">
                          {nodeTypes.slice(0, 5).map((type, index) => <i key={`${type}-${index}`}>{workflowNodeLabel(type)}</i>)}
                          {nodeTypes.length > 5 && <i>+{nodeTypes.length - 5}</i>}
                        </span>
                        <small>Updated {new Date(workflow.updated_at).toLocaleString("en-US")}</small>
                      </span>
                      <span className="workflow-open">Open →</span>
                    </button>
                    <button className="workflow-delete" disabled={busy} onClick={() => removeWorkflow(workflow)} aria-label={`Delete workflow ${workflow.name}`}>Delete</button>
                  </article>
                );
              })}
              {!workflows.length && (
                <div className="workflow-catalog-empty">
                  <span>⌁</span>
                  <h3>No workflows yet</h3>
                  <p>Create your first workflow using the form on the left.</p>
                </div>
              )}
            </div>
          </section>
        ) : (
          <>
            <section className="canvas-card">
              <div className="canvas-toolbar"><div><span className="pill">Draft</span><span>Revision {selected.revision}</span></div><span className="save-state">{message}</span></div>
              <WorkflowEditor
                key={selected.id}
                value={editor}
                onChange={changeEditor}
                onSave={queueDraftSave}
                disabled={busy}
                skills={enabledSkills}
                providers={providers}
                mcpServers={enabledMcpServers}
                nodeTypes={nodeTypes}
              />
            </section>

            {selected && isRunInputOpen && (
              <div
                className="workflow-modal-backdrop"
                role="presentation"
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) setIsRunInputOpen(false);
                }}
              >
                <section className="run-input-card run-input-modal" role="dialog" aria-modal="true" aria-labelledby="run-input-title">
                  <div className="run-input-modal-heading">
                    <div><p className="eyebrow">RUN DATA</p><h2 id="run-input-title">Run data</h2><p>Enter JSON that matches the input schema before running the workflow.</p></div>
                    <button type="button" aria-label="Close run data" onClick={() => setIsRunInputOpen(false)}>×</button>
                  </div>
                  <label>Workflow input<textarea autoFocus value={runInput} onChange={(event) => setRunInput(event.target.value)} spellCheck={false} /></label>
                  <div className="run-output">
                    <strong>Latest result in this session</strong>
                    <code>{latestCompletedOutput ? JSON.stringify(latestCompletedOutput, null, 2) : "No results in this session yet"}</code>
                  </div>
                </section>
              </div>
            )}

            {selected && isRunHistoryOpen && (
              <div
                className="workflow-modal-backdrop"
                role="presentation"
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) setIsRunHistoryOpen(false);
                }}
              >
              <section className="run-history-card run-history-modal" role="dialog" aria-modal="true" aria-labelledby="run-history-title">
                <div className="run-history-heading">
                  <div>
                    <p className="eyebrow">RUN HISTORY</p>
                    <h2 id="run-history-title">Run history</h2>
                    <p>Each run is saved as a separate record.</p>
                  </div>
                  <div className="run-history-heading-actions">
                    <div className="run-history-summary">
                      <strong>{runs.length}</strong>
                      <span>recent runs</span>
                      <button disabled={busy || runHistoryLoading} onClick={() => void perform(async () => loadRunHistory(true))}>{runHistoryLoading ? "Loading…" : "Refresh"}</button>
                    </div>
                    <button className="workflow-modal-close" type="button" aria-label="Close run history" onClick={() => setIsRunHistoryOpen(false)}>×</button>
                  </div>
                </div>
                <div className="run-history-body">
                  <div className="run-history-list">
                    {runs.map((item) => {
                      const isActive = ["pending", "running"].includes(item.status);
                      const isStopping = stoppingRunIds.has(item.id);
                      return (
                        <div className={run?.id === item.id ? "run-history-entry selected" : "run-history-entry"} key={item.id}>
                          <button
                            className="run-history-row"
                            onClick={() => {
                              setRun(item);
                              setRunInput(JSON.stringify(item.input, null, 2));
                            }}
                          >
                            <span className={`run-status ${item.status}`}>{item.status}</span>
                            <span>
                              <strong>Run {item.id.slice(0, 8)}</strong>
                              <small>{new Date(item.created_at).toLocaleString("en-US")}</small>
                              <small className="run-session-id" title={item.session_id}>Session {item.session_id}</small>
                            </span>
                            {item.status === "succeeded" && item.output && <code>{JSON.stringify(item.output)}</code>}
                          </button>
                          {isActive && (
                            <button
                              className="run-history-stop"
                              type="button"
                              disabled={isStopping}
                              aria-label={`Stop session ${item.session_id}`}
                              onClick={() => void stopRun(item)}
                            >
                              {isStopping ? "Stopping…" : "■ Stop"}
                            </button>
                          )}
                        </div>
                      );
                    })}
                    {!runs.length && <div className="run-history-empty">{runHistoryLoading ? "Loading run history…" : "No run history yet. Publish the workflow, then select Run to create the first record."}</div>}
                  </div>

                  <aside className="run-detail-panel">
                    {run ? (
                      <>
                        <div className="run-detail-title">
                          <div><span className={`run-status ${run.status}`}>{run.status}</span><h3>Run {run.id.slice(0, 8)}</h3></div>
                          <small>{new Date(run.created_at).toLocaleString("en-US")}</small>
                        </div>
                        <div className="run-session">
                          <span><strong>Langfuse session ID</strong><code>{run.session_id}</code></span>
                          <button type="button" onClick={() => void perform(() => copySessionId(run.session_id))}>Copy</button>
                        </div>
                        <div className="run-io-summary">
                          <div><strong>Workflow input</strong><pre>{JSON.stringify(run.input, null, 2)}</pre></div>
                          {run.status === "succeeded" && run.output && (
                            <div><strong>Workflow output</strong><pre>{JSON.stringify(run.output, null, 2)}</pre></div>
                          )}
                        </div>
                        <div className="run-step-heading">
                          <strong>Step details</strong>
                          <span>{runSteps.filter((step) => step.status === "succeeded").length}/{runSteps.length} steps completed</span>
                        </div>
                        <div className="run-step-list">
                          {runSteps.map((step) => (
                            <details className="run-step" key={step.id} open={runSteps.length <= 3}>
                              <summary>
                                <b>{step.sequence}</b>
                                <span><strong>{step.node_name}</strong><small>{step.node_type} · {step.node_id}</small></span>
                                <i className={`run-status ${step.status}`}>{step.status}</i>
                              </summary>
                              {step.status === "succeeded" && (
                                <div className="run-step-io">
                                  <div><strong>Input</strong><pre>{JSON.stringify(step.input, null, 2)}</pre></div>
                                  <div><strong>Output</strong><pre>{JSON.stringify(step.output, null, 2)}</pre></div>
                                </div>
                              )}
                              {step.status === "failed" && step.input && (
                                <div className="run-step-io"><div><strong>Input</strong><pre>{JSON.stringify(step.input, null, 2)}</pre></div></div>
                              )}
                              {step.error && <div className="run-step-error"><strong>Error</strong><pre>{JSON.stringify(step.error, null, 2)}</pre></div>}
                            </details>
                          ))}
                          {runStepsLoading && <div className="run-steps-empty">Loading step details…</div>}
                          {!runStepsLoading && !runSteps.length && <div className="run-steps-empty">No step-level data is available for this older run.</div>}
                        </div>
                      </>
                    ) : <div className="run-steps-empty">Select a run to view each step's input and output.</div>}
                  </aside>
                </div>
              </section>
              </div>
            )}

          </>
        ) : activeTab === "skills" ? (
          <section className="skills-page">
            <div className="library-intro">
              <div><p className="eyebrow">AVAILABLE TO AGENTS</p><h2>Installed skills</h2><p>Every active skill can be selected directly in an Agent node. Selected skills are snapshotted into the version when you publish.</p></div>
              <div className="library-summary-actions">
                <span className="library-count"><strong>{enabledSkills.length}</strong> active</span>
                <button className="add-provider-button" type="button" onClick={openNewSkillForm}>+ Add skill</button>
              </div>
            </div>

            <div className="provider-feedback" role="status">{message}</div>

            <div className="skill-catalog">
              {skills.map((skill) => (
                <article className={skill.enabled ? "skill-library-card" : "skill-library-card disabled"} key={skill.id}>
                  <div className="provider-heading">
                    <div>
                      <span className={skill.source === "builtin" ? "provider-logo" : "provider-logo user-skill-logo"}>{skill.source === "builtin" ? "AF" : "US"}</span>
                      <span><strong>{skill.name}</strong><small>{skill.slug} · version {skill.version}</small></span>
                    </div>
                    <span className={skill.enabled ? "provider-status enabled" : "provider-status"}>{skill.enabled ? "Active" : "Inactive"}</span>
                  </div>

                  <dl className="provider-details skill-details">
                    <div><dt>Source</dt><dd>{skill.source === "builtin" ? "AgentFlow (system)" : "User"}</dd></div>
                    <div><dt>Updated</dt><dd>{new Date(skill.updated_at).toLocaleString("en-US")}</dd></div>
                  </dl>

                  <p className="skill-description">{skill.description || "No description is available for this skill."}</p>

                  <div className="provider-actions skill-actions">
                    {skill.source === "user" ? <>
                      <button className="danger" type="button" disabled={busy} onClick={() => {
                        if (!window.confirm(`Delete skill “${skill.name}”? Agents that reference it must be saved again before publishing.`)) return;
                        void perform(async () => {
                          await api.deleteSkill(skill.id);
                          await refreshSkills();
                          if (selected) setVersion((await api.getCurrentWorkflowVersion(selected.id)) ?? undefined);
                          setMessage(`Deleted skill “${skill.name}”`);
                        });
                      }}>Delete</button>
                      <button className="primary" type="button" disabled={busy} onClick={() => openEditSkillForm(skill)}>Edit</button>
                    </> : <span className="managed-skill-note">Managed by the system</span>}
                  </div>
                </article>
              ))}
              {!skills.length && <div className="catalog-empty">No skills yet. Select “Add skill” to create the first one.</div>}
            </div>

            {(isAddingSkill || editingSkill) && (
              <div
                className="provider-modal-backdrop"
                role="presentation"
                onMouseDown={(event) => {
                  if (event.target === event.currentTarget) closeSkillForm();
                }}
              >
                <div className="provider-modal skill-modal" role="dialog" aria-modal="true" aria-labelledby="skill-form-title">
                  <div className="provider-modal-heading">
                    <div>
                      <p className="eyebrow">{editingSkill ? "EDIT SKILL" : "INSTALL A SKILL"}</p>
                      <h2 id="skill-form-title">{editingSkill ? "Edit skill" : "Add skill"}</h2>
                      <p>{editingSkill ? "Update the shared instructions. Published workflows retain their previous snapshot until they are published again." : "Create reusable instructions that can be selected directly in each Agent."}</p>
                    </div>
                    <button type="button" aria-label="Close skill form" onClick={closeSkillForm}>×</button>
                  </div>

                  <form className="skill-form skill-modal-form" onSubmit={(event) => {
                    event.preventDefault();
                    if (!skillForm.slug.trim() || !skillForm.name.trim() || !skillForm.instructions.trim()) return;
                    void perform(async () => {
                      if (editingSkill) {
                        await api.updateSkill(editingSkill, {
                          name: skillForm.name.trim(),
                          description: skillForm.description.trim(),
                          instructions: skillForm.instructions.trim(),
                          enabled: editingSkill.enabled,
                        });
                      } else {
                        await api.createSkill({
                          slug: skillForm.slug.trim(),
                          name: skillForm.name.trim(),
                          description: skillForm.description.trim(),
                          instructions: skillForm.instructions.trim(),
                        });
                      }
                      setSkillForm(emptySkill);
                      await refreshSkills();
                      setIsAddingSkill(false);
                      setEditingSkill(undefined);
                      setMessage(editingSkill ? `Updated skill “${skillForm.name.trim()}”` : "Installed the new skill");
                    });
                  }}>
                    <div className="provider-config-heading">
                      <span className="provider-logo user-skill-logo">US</span>
                      <span><strong>{editingSkill ? `Current version: v${editingSkill.version}` : "User skill configuration"}</strong><small>{editingSkill ? "Saving changes creates a new skill version." : "The skill will appear in workflow pickers after you save it."}</small></span>
                    </div>
                    <label>Slug<input autoFocus={!editingSkill} required disabled={Boolean(editingSkill)} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" maxLength={80} placeholder="research-assistant" value={skillForm.slug} onChange={(event) => setSkillForm({ ...skillForm, slug: event.target.value.toLowerCase() })} /></label>
                    <label>Skill name<input autoFocus={Boolean(editingSkill)} required maxLength={160} placeholder="Research assistant" value={skillForm.name} onChange={(event) => setSkillForm({ ...skillForm, name: event.target.value })} /></label>
                    <label>Description<input maxLength={500} placeholder="What does this skill help the agent do?" value={skillForm.description} onChange={(event) => setSkillForm({ ...skillForm, description: event.target.value })} /></label>
                    <label>Instructions<textarea required maxLength={100000} placeholder="Write the instructions the agent must follow…" value={skillForm.instructions} onChange={(event) => setSkillForm({ ...skillForm, instructions: event.target.value })} /></label>
                    <div className="provider-form-actions">
                      <button type="button" onClick={closeSkillForm} disabled={busy}>Cancel</button>
                      <button className="install-button" disabled={busy || !skillForm.slug.trim() || !skillForm.name.trim() || !skillForm.instructions.trim()}>{busy ? "Saving…" : editingSkill ? "Save changes" : "Add skill"}</button>
                    </div>
                    <span className="form-message" role="status">{message}</span>
                  </form>
                </div>
              </div>
            )}
          </section>
        ) : activeTab === "mcp" ? (
          <McpPage servers={mcpServers} refresh={refreshMcpServers} />
        ) : (
          <ModelsPage providers={providers} refresh={refreshProviders} />
        )}

        {activeTab === "workflows" && selected && (
          <footer className="action-bar">
            <div className="run-state">{run ? <><span className="run-dot" />Run <code>{run.id.slice(0, 8)}</code> · {run.status}</> : "No run in this session yet"}</div>
            <div className="actions">
              <button disabled={!selected || busy} onClick={() => setIsRunInputOpen(true)}>Run data</button>
              <button disabled={!selected || busy} onClick={openRunHistory}>Run history</button>
              <button disabled={!selected || busy} onClick={() => void perform(validateCurrentDraft)}>Validate</button>
              <button className="primary" disabled={!selected || busy} onClick={() => void perform(publishCurrentDraft)}>{version ? `Published v${version.version}` : "Publish"}</button>
              <button className="run-button" disabled={!selected || !version || busy} onClick={() => void perform(async () => { const created = await api.createRun(selected!.id, version!.id, parseRunInput()); setRun(created); setRuns((items) => [created, ...items.filter((item) => item.id !== created.id)]); setMessage(created.status === "succeeded" ? `Workflow completed: ${JSON.stringify(created.output)}` : `Run status: ${created.status}`); })}>▶ Run</button>
            </div>
          </footer>
        )}
      </main>
    </div>
  );
}
