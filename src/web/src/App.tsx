import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { keycloak } from "./auth";
import { ModelsPage } from "./ModelsPage";
import type { FlowRun, LlmProvider, RunStep, Skill, Workflow, WorkflowVersion } from "./types";
import { WorkflowEditor } from "./WorkflowEditor";

type AppTab = "workflows" | "skills" | "models";

const starterDraft = {
  nodes: [
    {
      id: "input",
      type: "input.schema",
      name: "Dữ liệu đầu vào",
      note: "Định nghĩa dữ liệu workflow tiếp nhận.",
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
    {
      id: "agent",
      type: "agent",
      name: "Agent",
      note: "Mô tả ngắn nhiệm vụ của agent.",
      config: {
        instructions: "",
        inputSchema: { type: "object", properties: {}, additionalProperties: false },
        outputSchema: { type: "object", properties: {}, additionalProperties: false },
      },
    },
    {
      id: "output",
      type: "output.schema",
      name: "Dữ liệu đầu ra",
      note: "Định nghĩa kết quả workflow trả về.",
      schema: { type: "object", properties: {}, additionalProperties: false },
    },
  ],
  edges: [
    { from: "input", to: "agent", port: "success" },
    { from: "agent", to: "output", port: "success" },
  ],
};

const agentPythonWorkflowDraft = {
  nodes: [
    {
      id: "research_agent",
      type: "agent",
      name: "Agent phân tích",
      note: "Phân tích chủ đề và tạo nội dung nháp có cấu trúc.",
      inputs: { topic: { from: "$input.topic" } },
      config: {
        instructions: "Phân tích chủ đề người dùng cung cấp. Trả về draft là một đoạn nội dung ngắn, rõ ràng và có các ý chính.",
        inputSchema: {
          type: "object",
          properties: { topic: { type: "string" } },
          required: ["topic"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: { draft: { type: "string" } },
          required: ["draft"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "python_formatter",
      type: "code.python",
      name: "Python chuẩn hóa",
      note: "Chuẩn hóa khoảng trắng và thống kê số từ trong bản nháp.",
      inputs: { draft: { from: "$nodes.research_agent.output.draft" } },
      config: {
        language: "python",
        code: [
          "def main(inputs):",
          "    cleaned_text = ' '.join(inputs['draft'].split())",
          "    return {",
          "        'cleaned_text': cleaned_text,",
          "        'word_count': len(cleaned_text.split()),",
          "    }",
        ].join("\n"),
        inputSchema: {
          type: "object",
          properties: { draft: { type: "string" } },
          required: ["draft"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: {
            cleaned_text: { type: "string" },
            word_count: { type: "integer" },
          },
          required: ["cleaned_text", "word_count"],
          additionalProperties: false,
        },
      },
    },
    {
      id: "review_agent",
      type: "agent",
      name: "Agent biên tập",
      note: "Biên tập bản nháp đã chuẩn hóa thành câu trả lời cuối cùng.",
      inputs: {
        content: { from: "$nodes.python_formatter.output.cleaned_text" },
        word_count: { from: "$nodes.python_formatter.output.word_count" },
      },
      config: {
        instructions: "Biên tập content thành câu trả lời hoàn chỉnh bằng tiếng Việt. Giữ thông tin chính xác, dễ đọc và trả về trường final_answer.",
        inputSchema: {
          type: "object",
          properties: {
            content: { type: "string" },
            word_count: { type: "integer" },
          },
          required: ["content", "word_count"],
          additionalProperties: false,
        },
        outputSchema: {
          type: "object",
          properties: { final_answer: { type: "string" } },
          required: ["final_answer"],
          additionalProperties: false,
        },
      },
    },
  ],
  edges: [
    { from: "research_agent", to: "python_formatter", port: "success" },
    { from: "python_formatter", to: "review_agent", port: "success" },
  ],
};

const sumWorkflowDraft = {
  nodes: [
    {
      id: "input",
      type: "input.schema",
      name: "Hai số đầu vào",
      note: "Nhận num1 và num2 từ dữ liệu chạy.",
      schema: {
        type: "object",
        properties: { num1: { type: "number" }, num2: { type: "number" } },
        required: ["num1", "num2"],
        additionalProperties: false,
      },
    },
    {
      id: "sum",
      type: "math.add",
      name: "Cộng hai số",
      note: "Tính num1 + num2 mà không cần gọi LLM.",
      inputs: { left: { from: "$input.num1" }, right: { from: "$input.num2" } },
      config: { outputKey: "sum" },
    },
    {
      id: "output",
      type: "output.schema",
      name: "Kết quả",
      note: "Trả về tổng của hai số.",
      inputs: { sum: { from: "$nodes.sum.output.sum" } },
      schema: {
        type: "object",
        properties: { sum: { type: "number" } },
        required: ["sum"],
        additionalProperties: false,
      },
    },
  ],
  edges: [
    { from: "input", to: "sum", port: "success" },
    { from: "sum", to: "output", port: "success" },
  ],
};

const emptySkill = { slug: "", name: "", description: "", instructions: "" };

function workflowNodeTypes(workflow: Workflow) {
  const nodes = workflow.draft.nodes;
  if (!Array.isArray(nodes)) return [];
  return nodes.flatMap((node) => (
    node && typeof node === "object" && "type" in node && typeof node.type === "string"
      ? [node.type]
      : []
  ));
}

function workflowNodeLabel(type: string) {
  const labels: Record<string, string> = {
    agent: "Agent",
    "code.python": "Python",
    "input.schema": "Input",
    "output.schema": "Output",
    "math.add": "Math",
  };
  return labels[type] ?? type;
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
  const [runInput, setRunInput] = useState("{}");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [providers, setProviders] = useState<LlmProvider[]>([]);
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([]);
  const [skillForm, setSkillForm] = useState(emptySkill);

  const selected = useMemo(
    () => workflows.find((workflow) => workflow.id === selectedId),
    [selectedId, workflows],
  );
  const enabledSkills = useMemo(() => skills.filter((skill) => skill.enabled), [skills]);

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

  useEffect(() => {
    Promise.all([refreshWorkflows(), refreshSkills(), refreshProviders()]).catch((error: Error) =>
      setMessage(error.message),
    );
  }, []);

  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    setEditor(JSON.stringify(selected.draft, null, 2));
    setVersion(undefined);
    setRun(undefined);
    setRuns([]);
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
    api
      .listWorkflowSkills(selected.id)
      .then((items) => {
        if (!cancelled) setSelectedSkillIds(items.map((skill) => skill.id));
      })
      .catch((error: Error) => {
        if (!cancelled) setMessage(error.message);
      });
    api
      .listWorkflowRuns(selected.id)
      .then((items) => {
        if (!cancelled) {
          setRuns(items);
          setRun(items[0]);
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
    const runId = run.id;
    const loadRun = () => {
      setRunStepsLoading(true);
      Promise.all([api.getRun(runId), api.listRunSteps(runId)])
        .then(([latest, steps]) => {
          if (cancelled) return;
          setRun(latest);
          setRuns((items) => items.map((item) => item.id === latest.id ? latest : item));
          setRunSteps(steps);
          if (latest.status === "succeeded") setMessage("Worker đã chạy xong workflow");
          if (latest.status === "failed") setMessage("Workflow thất bại — xem lỗi từng bước bên dưới");
        })
        .catch((error: Error) => {
          if (!cancelled) setMessage(error.message);
        })
        .finally(() => {
          if (!cancelled) setRunStepsLoading(false);
        });
    };
    loadRun();
    const polling = run.status === "pending" || run.status === "running"
      ? window.setInterval(loadRun, 1000)
      : undefined;
    return () => {
      cancelled = true;
      if (polling !== undefined) window.clearInterval(polling);
    };
  }, [run?.id, run?.status]);

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

  function parseDraft() {
    const parsed: unknown = JSON.parse(editor);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error("Draft phải là một JSON object");
    }
    return parsed as Record<string, unknown>;
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
              const created = await api.createWorkflow("Demo Agent → Python → Agent", agentPythonWorkflowDraft);
              await refreshWorkflows(created.id);
              setRunInput(JSON.stringify({ topic: "Ứng dụng AI trong giáo dục" }, null, 2));
              setMessage("Đã tạo workflow demo gồm 2 Agent và 1 Python node");
            })}>＋ Tạo demo 2 Agent + Python</button>
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
        ) : (
          <div className="sidebar-note">
            <strong>Model providers</strong>
            <p>Kết nối OpenRouter và tải danh sách model để chuẩn bị gán cho agent.</p>
          </div>
        )}
      </aside>

      <main className={activeTab === "workflows" ? "workspace" : "workspace skills-workspace"}>
        <header>
          <div className="page-heading">
            {activeTab === "workflows" && selected && <button className="back-to-workflows" onClick={showWorkflowList}>← Tất cả workflow</button>}
            <p className="eyebrow">{activeTab === "workflows" ? "WORKFLOW STUDIO" : activeTab === "skills" ? "SKILL LIBRARY" : "MODEL CATALOG"}</p>
            <h1>{activeTab === "workflows" ? selected?.name ?? "Workflows" : activeTab === "skills" ? "Skills" : "Models"}</h1>
          </div>
          <div className="account"><span className="status-dot" /><div><strong>{keycloak.tokenParsed?.preferred_username ?? "developer"}</strong><small>Đã xác thực</small></div><button onClick={() => void keycloak.logout()}>Đăng xuất</button></div>
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
              <WorkflowEditor value={editor} onChange={setEditor} disabled={busy} />
            </section>

            {selected && (
              <section className="run-input-card">
                <div><p className="eyebrow">RUN DATA</p><h2>Input chạy thử</h2><p>Nhập JSON đúng với Input schema. Demo Agent/Python dùng topic; mẫu tính tổng dùng num1 và num2.</p></div>
                <textarea value={runInput} onChange={(event) => setRunInput(event.target.value)} spellCheck={false} />
                <div className="run-output">
                  <strong>Kết quả gần nhất</strong>
                  <code>{run?.output ? JSON.stringify(run.output, null, 2) : "Chưa có kết quả"}</code>
                </div>
              </section>
            )}

            {selected && (
              <section className="run-history-card">
                <div className="run-history-heading">
                  <div>
                    <p className="eyebrow">RUN HISTORY</p>
                    <h2>Lịch sử chạy</h2>
                    <p>Mỗi lần bấm Chạy được lưu thành một bản ghi riêng.</p>
                  </div>
                  <div className="run-history-summary">
                    <strong>{runs.length}</strong>
                    <span>lần chạy gần nhất</span>
                    <button disabled={busy} onClick={() => void perform(async () => {
                      const items = await api.listWorkflowRuns(selected.id);
                      setRuns(items);
                      if (run) setRun(items.find((item) => item.id === run.id) ?? items[0]);
                      else setRun(items[0]);
                      setMessage("Đã tải lại lịch sử chạy");
                    })}>Tải lại</button>
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
                        <code>{item.output ? JSON.stringify(item.output) : "Chưa có output"}</code>
                        <b>Xem</b>
                      </button>
                    ))}
                    {!runs.length && <div className="run-history-empty">Chưa có lịch sử. Publish workflow rồi bấm Chạy để tạo lần chạy đầu tiên.</div>}
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
                          <div><strong>Workflow output</strong><pre>{run.output ? JSON.stringify(run.output, null, 2) : "Chưa có output"}</pre></div>
                        </div>
                        <div className="run-step-heading"><strong>Chi tiết từng bước</strong><span>{runSteps.length} bước</span></div>
                        <div className="run-step-list">
                          {runSteps.map((step) => (
                            <details className="run-step" key={step.id} open={runSteps.length <= 3}>
                              <summary>
                                <b>{step.sequence}</b>
                                <span><strong>{step.node_name}</strong><small>{step.node_type} · {step.node_id}</small></span>
                                <i className={`run-status ${step.status}`}>{step.status}</i>
                              </summary>
                              <div className="run-step-io">
                                <div><strong>Input</strong><pre>{step.input ? JSON.stringify(step.input, null, 2) : "Chưa có input thực thi"}</pre></div>
                                <div><strong>Output</strong><pre>{step.output ? JSON.stringify(step.output, null, 2) : "Chưa có output"}</pre></div>
                              </div>
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
            )}

            {selected && (
              <section className="skills-card">
                <div><p className="eyebrow">INSTALLED SKILLS</p><h2>Chọn skill cho workflow</h2><p>Chỉ các skill đang được bật trong Skill library mới xuất hiện tại đây.</p></div>
                <div className="skill-list">
                  {enabledSkills.length ? enabledSkills.map((skill) => {
                    const checked = selectedSkillIds.includes(skill.id);
                    return <label className={checked ? "skill-chip selected" : "skill-chip"} key={skill.id}>
                      <input type="checkbox" checked={checked} onChange={() => setSelectedSkillIds((ids) => checked ? ids.filter((id) => id !== skill.id) : [...ids, skill.id])} />
                      <span><strong>{skill.name}</strong><small>{skill.slug} · v{skill.version}</small></span>
                    </label>;
                  }) : <p className="muted">Chưa có skill nào được cài.</p>}
                </div>
                <button disabled={busy} onClick={() => void perform(async () => {
                  const chosen = await api.selectWorkflowSkills(selected.id, selectedSkillIds);
                  setSelectedSkillIds(chosen.map((skill) => skill.id));
                  setMessage(`Đã gắn ${chosen.length} skill vào workflow`);
                })}>Lưu lựa chọn</button>
              </section>
            )}
          </>
        ) : activeTab === "skills" ? (
          <section className="skills-page">
            <div className="library-intro">
              <div><p className="eyebrow">AVAILABLE TO AGENTS</p><h2>Skill đã cài</h2><p>Quản lý hướng dẫn dùng chung. Khi publish workflow, nội dung của các skill được chọn sẽ được snapshot vào version.</p></div>
              <span className="library-count"><strong>{enabledSkills.length}</strong> đang bật</span>
            </div>

            <div className="skills-layout">
              <div className="skill-catalog">
                {skills.map((skill) => (
                  <article className={skill.enabled ? "skill-library-card" : "skill-library-card disabled"} key={skill.id}>
                    <div className="skill-card-heading"><span className={`source-badge ${skill.source}`}>{skill.source === "builtin" ? "Hệ thống" : "Người dùng"}</span><code>v{skill.version}</code></div>
                    <h3>{skill.name}</h3><p>{skill.description || "Chưa có mô tả."}</p>
                    <div className="skill-meta"><code>{skill.slug}</code><span>{skill.enabled ? "Đang bật" : "Đã tắt"}</span></div>
                  </article>
                ))}
                {!skills.length && <div className="catalog-empty">Chưa có skill. Hãy tạo skill đầu tiên ở biểu mẫu bên cạnh.</div>}
              </div>

              <form className="skill-form" onSubmit={(event) => {
                event.preventDefault();
                if (!skillForm.slug.trim() || !skillForm.name.trim() || !skillForm.instructions.trim()) return;
                void perform(async () => {
                  await api.createSkill({
                    slug: skillForm.slug.trim(),
                    name: skillForm.name.trim(),
                    description: skillForm.description.trim(),
                    instructions: skillForm.instructions.trim(),
                  });
                  setSkillForm(emptySkill);
                  await refreshSkills();
                  setMessage("Đã cài skill mới");
                });
              }}>
                <div><p className="eyebrow">INSTALL A SKILL</p><h2>Tạo skill mới</h2><p>Skill sau khi tạo sẽ sẵn sàng để chọn trong workflow.</p></div>
                <label>Slug<input required pattern="[a-z0-9]+(?:-[a-z0-9]+)*" placeholder="research-assistant" value={skillForm.slug} onChange={(event) => setSkillForm({ ...skillForm, slug: event.target.value.toLowerCase() })} /></label>
                <label>Tên skill<input required maxLength={160} placeholder="Research assistant" value={skillForm.name} onChange={(event) => setSkillForm({ ...skillForm, name: event.target.value })} /></label>
                <label>Mô tả<input maxLength={500} placeholder="Skill này giúp agent làm gì?" value={skillForm.description} onChange={(event) => setSkillForm({ ...skillForm, description: event.target.value })} /></label>
                <label>Instructions<textarea required placeholder="Viết các chỉ dẫn mà agent phải tuân theo…" value={skillForm.instructions} onChange={(event) => setSkillForm({ ...skillForm, instructions: event.target.value })} /></label>
                <button className="install-button" disabled={busy || !skillForm.slug.trim() || !skillForm.name.trim() || !skillForm.instructions.trim()}>Cài skill</button>
                <span className="form-message">{message}</span>
              </form>
            </div>
          </section>
        ) : (
          <ModelsPage providers={providers} refresh={refreshProviders} />
        )}

        {activeTab === "workflows" && selected && (
          <footer className="action-bar">
            <div className="run-state">{run ? <><span className="run-dot" />Run <code>{run.id.slice(0, 8)}</code> · {run.status}</> : "Chưa có run trong phiên này"}</div>
            <div className="actions">
              <button disabled={!selected || busy} onClick={() => void perform(async () => { const result = await api.validate(selected!.id); setMessage(result.valid ? "Graph hợp lệ" : result.errors.join(" · ")); })}>Kiểm tra</button>
              <button disabled={!selected || busy} onClick={() => void perform(async () => { const saved = await api.updateDraft(selected!, parseDraft()); setWorkflows((items) => items.map((item) => item.id === saved.id ? saved : item)); setMessage(`Đã lưu revision ${saved.revision}`); })}>Lưu draft</button>
              <button className="primary" disabled={!selected || busy} onClick={() => void perform(async () => { const published = await api.publish(selected!.id); setVersion(published); setMessage(`Đã publish version ${published.version}`); })}>Publish</button>
              <button className="run-button" disabled={!selected || !version || busy} onClick={() => void perform(async () => { const created = await api.createRun(selected!.id, version!.id, parseRunInput()); setRun(created); setRuns((items) => [created, ...items.filter((item) => item.id !== created.id)]); setMessage(created.status === "succeeded" ? `Đã chạy xong workflow: ${JSON.stringify(created.output)}` : `Run đang ở trạng thái ${created.status}`); })}>▶ Chạy</button>
            </div>
          </footer>
        )}
      </main>
    </div>
  );
}
