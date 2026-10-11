import { useEffect, useMemo, useRef, useState } from "react";

import { McpPage } from "../features/mcp/McpPage";
import { ModelsPage } from "../features/models/ModelsPage";
import { WorkflowEditor } from "../features/workflows/editor/WorkflowEditor";
import { workflowNodeLabel, workflowNodeTypes } from "../features/workflows/summary";
import {
  agentPythonMcpWorkflowDraft,
  FILESYSTEM_DEMO_MCP,
  starterDraft,
  sumWorkflowDraft,
} from "../features/workflows/templates";
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
  const [selectedId, setSelectedId] = useState<string>();
  const [editor, setEditor] = useState(JSON.stringify(starterDraft, null, 2));
  const [name, setName] = useState("");
  const [message, setMessage] = useState("Đang tải dữ liệu…");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState<WorkflowVersion>();
  const [run, setRun] = useState<FlowRun>();
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [runSteps, setRunSteps] = useState<RunStep[]>([]);
  const [runStepsLoading, setRunStepsLoading] = useState(false);
  const [isRunInputOpen, setIsRunInputOpen] = useState(false);
  const [isRunHistoryOpen, setIsRunHistoryOpen] = useState(false);
  const [runHistoryLoading, setRunHistoryLoading] = useState(false);
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
    setSelectedId((current) => {
      const next = select ?? current;
      return items.some((workflow) => workflow.id === next) ? next : undefined;
    });
    setMessage(items.length ? "Sẵn sàng" : "Tạo workflow đầu tiên để bắt đầu");
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

  async function ensureFilesystemDemoMcp(): Promise<McpServer> {
    const existing = mcpServers.find((server) => server.slug === FILESYSTEM_DEMO_MCP.slug);
    if (existing) {
      if (!existing.enabled) {
        throw new Error("MCP filesystem-demo đang bị tắt. Hãy bật lại trong trang MCP.");
      }
      if (existing.transport !== FILESYSTEM_DEMO_MCP.transport || existing.url !== FILESYSTEM_DEMO_MCP.url) {
        throw new Error(`MCP filesystem-demo phải dùng URL ${FILESYSTEM_DEMO_MCP.url}`);
      }
      return existing;
    }
    const created = await api.createMcpServer({
      ...FILESYSTEM_DEMO_MCP,
      headers: {},
      enabled: true,
    });
    setMcpServers((items) => [...items, created].sort((left, right) => left.name.localeCompare(right.name)));
    return created;
  }

  useEffect(() => {
    Promise.all([refreshWorkflows(), refreshSkills(), refreshProviders(), refreshMcpServers(), refreshNodeTypes()]).catch((error: Error) =>
      setMessage(error.message),
    );
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
    setRunInput(isSumWorkflow
      ? JSON.stringify({ num1: 1, num2: 3 }, null, 2)
      : isAgentPythonDemo
        ? JSON.stringify({ topic: "Ứng dụng AI trong giáo dục" }, null, 2)
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
            setMessage("Draft đã lưu nhưng có thay đổi chưa được publish");
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
          if (latest.status === "succeeded") setMessage("Worker đã chạy xong workflow");
          if (latest.status === "failed") setMessage("Workflow thất bại — mở lịch sử chạy để xem lỗi từng bước");
          previousStatus = latest.status;
        }
        if (latest.status === "pending" || latest.status === "running") {
          polling = window.setTimeout(() => void loadRun(false), 1200);
        }
      } catch (error) {
        if (cancelled) return;
        setMessage(error instanceof Error ? error.message : "Không thể cập nhật trạng thái run");
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

  async function perform(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Có lỗi xảy ra");
    } finally {
      setBusy(false);
    }
  }

  function parseDraft(value = editor) {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Draft phải là một JSON object");
    }
    return parsed as Record<string, unknown>;
  }

  function changeEditor(value: string) {
    setEditor(value);
    setVersion(undefined);
    setMessage("Có thay đổi đang chờ tự động lưu…");
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
      setMessage("Đang tự động lưu draft…");
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
            setMessage(`Đã tự động lưu revision ${saved.revision} · cần publish lại`);
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
        setMessage(error instanceof Error ? error.message : "Không thể tự động lưu draft");
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Không thể tự động lưu draft");
    }
  }

  async function validateCurrentDraft() {
    if (!selected) return;
    await persistDraft(editor);
    const result = await api.validate(selected.id);
    setMessage(result.valid ? "Graph hợp lệ" : result.errors.join(" · "));
  }

  async function publishCurrentDraft() {
    if (!selected) return;
    await persistDraft(editor);
    const published = await api.publish(selected.id);
    setVersion(published);
    setMessage(`Đã lưu draft và publish version ${published.version}`);
  }

  function parseRunInput() {
    const parsed: unknown = JSON.parse(runInput);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Input chạy phải là một JSON object");
    }
    return parsed as Record<string, unknown>;
  }

  function openWorkflow(workflowId: string) {
    setSelectedId(workflowId);
    setActiveTab("workflows");
  }

  function showWorkflowList() {
    setSelectedId(undefined);
    setActiveTab("workflows");
    setIsRunHistoryOpen(false);
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
      if (isRefresh) setRunDetailsRefreshKey((current) => current + 1);
      setMessage(items.length
        ? isRefresh ? "Đã tải lại lịch sử chạy" : `Đã tải ${items.length} lần chạy gần nhất`
        : "Workflow chưa có lịch sử chạy");
    } finally {
      setRunHistoryLoading(false);
    }
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
    setMessage("Điền thông tin để tạo skill mới");
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
    setMessage(`Đang chỉnh sửa “${skill.name}”`);
  }

  function removeWorkflow(workflow: Workflow) {
    if (!window.confirm(`Xóa workflow “${workflow.name}” cùng toàn bộ version và lịch sử chạy?`)) return;
    void perform(async () => {
      await api.deleteWorkflow(workflow.id);
      setSelectedId((current) => current === workflow.id ? undefined : current);
      await refreshWorkflows();
      setMessage(`Đã xóa workflow “${workflow.name}”`);
    });
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>AF</span>
          <div><strong>AgentFlow</strong><small>Control plane</small></div>
        </div>

        <nav className="primary-nav" aria-label="Điều hướng chính">
          <button className={activeTab === "workflows" ? "nav-tab active" : "nav-tab"} onClick={showWorkflowList}>
            <span className="nav-icon">⌘</span><span><strong>Workflows</strong><small>Thiết kế và chạy flow</small></span><b>{workflows.length}</b>
          </button>
          <button className={activeTab === "skills" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("skills")}>
            <span className="nav-icon">◆</span><span><strong>Skills</strong><small>Quản lý kỹ năng agent</small></span><b>{enabledSkills.length}</b>
          </button>
          <button className={activeTab === "mcp" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("mcp")}>
            <span className="nav-icon">M</span><span><strong>MCP</strong><small>Công cụ ngoài cho agent</small></span><b>{enabledMcpServers.length}</b>
          </button>
          <button className={activeTab === "models" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("models")}>
            <span className="nav-icon">◉</span><span><strong>Models</strong><small>LLM providers và models</small></span><b>{providers.filter((item) => item.enabled).length}</b>
          </button>
        </nav>

        {activeTab === "workflows" ? (
          <>
            <div className="sidebar-note workflow-sidebar-note">
              <strong>{workflows.length} workflow đã tạo</strong>
              <p>Mở trang Workflows để xem, chỉnh sửa hoặc xóa workflow.</p>
              <button onClick={showWorkflowList}>Xem tất cả workflow</button>
            </div>
            <form className="new-flow" onSubmit={(event) => {
              event.preventDefault();
              if (!name.trim()) return;
              void perform(async () => {
                const created = await api.createWorkflow(name.trim());
                setName("");
                await refreshWorkflows(created.id);
                setMessage("Đã tạo workflow");
              });
            }}>
              <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Tên workflow mới" maxLength={160} />
              <button disabled={busy || !name.trim()}>Tạo mới</button>
            </form>
            <button className="sample-flow-button" disabled={busy} onClick={() => void perform(async () => {
              const demoMcp = await ensureFilesystemDemoMcp();
              const created = await api.createWorkflow("Demo Agent → Parallel → MCP → Join", agentPythonMcpWorkflowDraft(demoMcp.id));
              await refreshWorkflows(created.id);
              setRunInput(JSON.stringify({ topic: "Ứng dụng AI trong giáo dục" }, null, 2));
              setMessage("Đã đăng ký MCP filesystem và tạo demo dùng tool ghi/đọc file");
            })}>＋ Tạo demo Parallel + MCP</button>
            <button className="sample-flow-button" disabled={busy} onClick={() => void perform(async () => {
              const created = await api.createWorkflow("Tính tổng hai số", sumWorkflowDraft);
              await refreshWorkflows(created.id);
              setRunInput(JSON.stringify({ num1: 1, num2: 3 }, null, 2));
              setMessage("Đã tạo workflow mẫu tính tổng");
            })}>＋ Tạo mẫu tính tổng</button>
          </>
        ) : activeTab === "skills" ? (
          <div className="sidebar-note">
            <strong>Skill library</strong>
            <p>Các skill được cài ở đây sẽ xuất hiện trong bộ chọn của từng workflow.</p>
          </div>
        ) : activeTab === "mcp" ? (
          <div className="sidebar-note">
            <strong>MCP registry</strong>
            <p>Đăng ký MCP từ xa và chỉ cấp đúng các tool được chọn cho từng Agent.</p>
          </div>
        ) : (
          <div className="sidebar-note">
            <strong>Model providers</strong>
            <p>Kết nối OpenRouter và tải danh sách model để chuẩn bị gán cho agent.</p>
          </div>
        )}
        <div className="sidebar-account">
          <span className="status-dot" />
          <div>
            <strong>{keycloak.tokenParsed?.preferred_username ?? "developer"}</strong>
            <small>Đã xác thực</small>
          </div>
          <button
            type="button"
            onClick={() => void keycloak.logout({ redirectUri: window.location.origin })}
          >
            Đăng xuất
          </button>
        </div>
      </aside>

      <main className={activeTab === "workflows" ? selected ? "workspace workflow-studio-workspace" : "workspace" : "workspace skills-workspace"}>
        <header>
          <div className="page-heading">
            {activeTab === "workflows" && selected && <button className="back-to-workflows" onClick={showWorkflowList}>← Tất cả workflow</button>}
            <p className="eyebrow">{activeTab === "workflows" ? "WORKFLOW STUDIO" : activeTab === "skills" ? "SKILL LIBRARY" : activeTab === "mcp" ? "MCP REGISTRY" : "MODEL CATALOG"}</p>
            <h1>{activeTab === "workflows" ? selected?.name ?? "Workflows" : activeTab === "skills" ? "Skills" : activeTab === "mcp" ? "MCP" : "Models"}</h1>
          </div>
        </header>

        {activeTab === "workflows" ? !selected ? (
          <section className="workflow-catalog-page">
            <div className="workflow-catalog-hero">
              <div>
                <p className="eyebrow">YOUR AUTOMATIONS</p>
                <h2>Workflow đã tạo</h2>
                <p>Chọn một workflow để mở canvas, cấu hình node, publish và xem lịch sử chạy.</p>
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
                        <small>Cập nhật {new Date(workflow.updated_at).toLocaleString("vi-VN")}</small>
                      </span>
                      <span className="workflow-open">Mở →</span>
                    </button>
                    <button className="workflow-delete" disabled={busy} onClick={() => removeWorkflow(workflow)} aria-label={`Xóa workflow ${workflow.name}`}>Xóa</button>
                  </article>
                );
              })}
              {!workflows.length && (
                <div className="workflow-catalog-empty">
                  <span>⌁</span>
                  <h3>Chưa có workflow</h3>
                  <p>Tạo workflow đầu tiên từ biểu mẫu bên trái.</p>
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
                    <div><p className="eyebrow">RUN DATA</p><h2 id="run-input-title">Dữ liệu chạy</h2><p>Nhập JSON đúng với Input schema trước khi chạy workflow.</p></div>
                    <button type="button" aria-label="Đóng dữ liệu chạy" onClick={() => setIsRunInputOpen(false)}>×</button>
                  </div>
                  <label>Workflow input<textarea autoFocus value={runInput} onChange={(event) => setRunInput(event.target.value)} spellCheck={false} /></label>
                  <div className="run-output">
                    <strong>Kết quả gần nhất trong phiên</strong>
                    <code>{latestCompletedOutput ? JSON.stringify(latestCompletedOutput, null, 2) : "Chưa có kết quả trong phiên này"}</code>
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
                    <h2 id="run-history-title">Lịch sử chạy</h2>
                    <p>Mỗi lần bấm Chạy được lưu thành một bản ghi riêng.</p>
                  </div>
                  <div className="run-history-heading-actions">
                    <div className="run-history-summary">
                      <strong>{runs.length}</strong>
                      <span>lần chạy gần nhất</span>
                      <button disabled={busy || runHistoryLoading} onClick={() => void perform(async () => loadRunHistory(true))}>{runHistoryLoading ? "Đang tải…" : "Tải lại"}</button>
                    </div>
                    <button className="workflow-modal-close" type="button" aria-label="Đóng lịch sử chạy" onClick={() => setIsRunHistoryOpen(false)}>×</button>
                  </div>
                </div>
                <div className="run-history-body">
                  <div className="run-history-list">
                    {runs.map((item) => (
                      <button
                        className={run?.id === item.id ? "run-history-row selected" : "run-history-row"}
                        key={item.id}
                        onClick={() => {
                          setRun(item);
                          setRunInput(JSON.stringify(item.input, null, 2));
                        }}
                      >
                        <span className={`run-status ${item.status}`}>{item.status}</span>
                        <span><strong>Run {item.id.slice(0, 8)}</strong><small>{new Date(item.created_at).toLocaleString("vi-VN")}</small></span>
                        {item.status === "succeeded" && item.output && <code>{JSON.stringify(item.output)}</code>}
                        <b>Xem</b>
                      </button>
                    ))}
                    {!runs.length && <div className="run-history-empty">{runHistoryLoading ? "Đang tải lịch sử chạy…" : "Chưa có lịch sử. Publish workflow rồi bấm Chạy để tạo lần chạy đầu tiên."}</div>}
                  </div>

                  <aside className="run-detail-panel">
                    {run ? (
                      <>
                        <div className="run-detail-title">
                          <div><span className={`run-status ${run.status}`}>{run.status}</span><h3>Run {run.id.slice(0, 8)}</h3></div>
                          <small>{new Date(run.created_at).toLocaleString("vi-VN")}</small>
                        </div>
                        <div className="run-io-summary">
                          <div><strong>Workflow input</strong><pre>{JSON.stringify(run.input, null, 2)}</pre></div>
                          {run.status === "succeeded" && run.output && (
                            <div><strong>Workflow output</strong><pre>{JSON.stringify(run.output, null, 2)}</pre></div>
                          )}
                        </div>
                        <div className="run-step-heading">
                          <strong>Chi tiết từng bước</strong>
                          <span>{runSteps.filter((step) => step.status === "succeeded").length}/{runSteps.length} bước hoàn tất</span>
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
                                  <div><strong>Kết quả</strong><pre>{JSON.stringify(step.output, null, 2)}</pre></div>
                                </div>
                              )}
                              {step.status === "failed" && step.input && (
                                <div className="run-step-io"><div><strong>Input</strong><pre>{JSON.stringify(step.input, null, 2)}</pre></div></div>
                              )}
                              {step.error && <div className="run-step-error"><strong>Error</strong><pre>{JSON.stringify(step.error, null, 2)}</pre></div>}
                            </details>
                          ))}
                          {runStepsLoading && <div className="run-steps-empty">Đang tải chi tiết các bước…</div>}
                          {!runStepsLoading && !runSteps.length && <div className="run-steps-empty">Run cũ chưa có dữ liệu từng bước.</div>}
                        </div>
                      </>
                    ) : <div className="run-steps-empty">Chọn một run để xem input và output từng bước.</div>}
                  </aside>
                </div>
              </section>
              </div>
            )}

          </>
        ) : activeTab === "skills" ? (
          <section className="skills-page">
            <div className="library-intro">
              <div><p className="eyebrow">AVAILABLE TO AGENTS</p><h2>Skill đã cài</h2><p>Mọi skill đang Active đều có thể được chọn trực tiếp trong từng node Agent. Khi publish, các skill được Agent chọn sẽ được snapshot vào version.</p></div>
              <div className="library-summary-actions">
                <span className="library-count"><strong>{enabledSkills.length}</strong> đang hoạt động</span>
                <button className="add-provider-button" type="button" onClick={openNewSkillForm}>+ Thêm skill</button>
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
                    <div><dt>Nguồn</dt><dd>{skill.source === "builtin" ? "AgentFlow (hệ thống)" : "Người dùng"}</dd></div>
                    <div><dt>Cập nhật</dt><dd>{new Date(skill.updated_at).toLocaleString("vi-VN")}</dd></div>
                  </dl>

                  <p className="skill-description">{skill.description || "Chưa có mô tả cho skill này."}</p>

                  <div className="provider-actions skill-actions">
                    {skill.source === "user" ? <>
                      <button className="danger" type="button" disabled={busy} onClick={() => {
                        if (!window.confirm(`Xóa skill “${skill.name}”? Các Agent đang tham chiếu skill này sẽ cần được lưu lại trước khi publish.`)) return;
                        void perform(async () => {
                          await api.deleteSkill(skill.id);
                          await refreshSkills();
                          if (selected) setVersion((await api.getCurrentWorkflowVersion(selected.id)) ?? undefined);
                          setMessage(`Đã xóa skill “${skill.name}”`);
                        });
                      }}>Xóa</button>
                      <button className="primary" type="button" disabled={busy} onClick={() => openEditSkillForm(skill)}>Chỉnh sửa</button>
                    </> : <span className="managed-skill-note">Được quản lý bởi hệ thống</span>}
                  </div>
                </article>
              ))}
              {!skills.length && <div className="catalog-empty">Chưa có skill. Hãy chọn “Thêm skill” để tạo skill đầu tiên.</div>}
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
                      <h2 id="skill-form-title">{editingSkill ? "Chỉnh sửa skill" : "Thêm skill"}</h2>
                      <p>{editingSkill ? "Cập nhật nội dung dùng chung. Các workflow đã publish vẫn giữ snapshot cũ cho đến lần publish tiếp theo." : "Tạo bộ hướng dẫn dùng chung để chọn trực tiếp trong từng Agent."}</p>
                    </div>
                    <button type="button" aria-label="Đóng form skill" onClick={closeSkillForm}>×</button>
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
                      setMessage(editingSkill ? `Đã cập nhật skill “${skillForm.name.trim()}”` : "Đã cài skill mới");
                    });
                  }}>
                    <div className="provider-config-heading">
                      <span className="provider-logo user-skill-logo">US</span>
                      <span><strong>{editingSkill ? `Phiên bản hiện tại: v${editingSkill.version}` : "Cấu hình skill người dùng"}</strong><small>{editingSkill ? "Lưu thay đổi sẽ tạo phiên bản skill mới." : "Skill sẽ sẵn sàng trong bộ chọn của workflow sau khi lưu."}</small></span>
                    </div>
                    <label>Slug<input autoFocus={!editingSkill} required disabled={Boolean(editingSkill)} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" maxLength={80} placeholder="research-assistant" value={skillForm.slug} onChange={(event) => setSkillForm({ ...skillForm, slug: event.target.value.toLowerCase() })} /></label>
                    <label>Tên skill<input autoFocus={Boolean(editingSkill)} required maxLength={160} placeholder="Research assistant" value={skillForm.name} onChange={(event) => setSkillForm({ ...skillForm, name: event.target.value })} /></label>
                    <label>Mô tả<input maxLength={500} placeholder="Skill này giúp agent làm gì?" value={skillForm.description} onChange={(event) => setSkillForm({ ...skillForm, description: event.target.value })} /></label>
                    <label>Instructions<textarea required maxLength={100000} placeholder="Viết các chỉ dẫn mà agent phải tuân theo…" value={skillForm.instructions} onChange={(event) => setSkillForm({ ...skillForm, instructions: event.target.value })} /></label>
                    <div className="provider-form-actions">
                      <button type="button" onClick={closeSkillForm} disabled={busy}>Hủy</button>
                      <button className="install-button" disabled={busy || !skillForm.slug.trim() || !skillForm.name.trim() || !skillForm.instructions.trim()}>{busy ? "Đang lưu…" : editingSkill ? "Lưu thay đổi" : "Thêm skill"}</button>
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
            <div className="run-state">{run ? <><span className="run-dot" />Run <code>{run.id.slice(0, 8)}</code> · {run.status}</> : "Chưa có run trong phiên này"}</div>
            <div className="actions">
              <button disabled={!selected || busy} onClick={() => setIsRunInputOpen(true)}>Dữ liệu chạy</button>
              <button disabled={!selected || busy} onClick={openRunHistory}>Lịch sử chạy</button>
              <button disabled={!selected || busy} onClick={() => void perform(validateCurrentDraft)}>Kiểm tra</button>
              <button className="primary" disabled={!selected || busy} onClick={() => void perform(publishCurrentDraft)}>{version ? `Published v${version.version}` : "Publish"}</button>
              <button className="run-button" disabled={!selected || !version || busy} onClick={() => void perform(async () => { const created = await api.createRun(selected!.id, version!.id, parseRunInput()); setRun(created); setRuns((items) => [created, ...items.filter((item) => item.id !== created.id)]); setMessage(created.status === "succeeded" ? `Đã chạy xong workflow: ${JSON.stringify(created.output)}` : `Run đang ở trạng thái ${created.status}`); })}>▶ Chạy</button>
            </div>
          </footer>
        )}
      </main>
    </div>
  );
}
