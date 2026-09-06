import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { keycloak } from "./auth";
import type { FlowRun, Skill, Workflow, WorkflowVersion } from "./types";

type AppTab = "workflows" | "skills";

const starterDraft = {
  nodes: [
    { id: "start", type: "trigger.manual" },
    { id: "agent", type: "agent" },
    { id: "done", type: "end" },
  ],
  edges: [
    { from: "start", to: "agent", port: "success" },
    { from: "agent", to: "done", port: "success" },
  ],
};

const emptySkill = { slug: "", name: "", description: "", instructions: "" };

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
  const [skills, setSkills] = useState<Skill[]>([]);
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
    setSelectedId(select ?? selectedId ?? items[0]?.id);
    setMessage(items.length ? "Sẵn sàng" : "Tạo workflow đầu tiên để bắt đầu");
  }

  async function refreshSkills() {
    setSkills(await api.listSkills());
  }

  useEffect(() => {
    Promise.all([refreshWorkflows(), refreshSkills()]).catch((error: Error) =>
      setMessage(error.message),
    );
  }, []);

  useEffect(() => {
    if (!selected) return;
    setEditor(JSON.stringify(selected.draft, null, 2));
    setVersion(undefined);
    setRun(undefined);
    api
      .listWorkflowSkills(selected.id)
      .then((items) => setSelectedSkillIds(items.map((skill) => skill.id)))
      .catch((error: Error) => setMessage(error.message));
  }, [selected?.id]);

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

  function openWorkflow(workflowId: string) {
    setSelectedId(workflowId);
    setActiveTab("workflows");
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span>AF</span>
          <div><strong>AgentFlow</strong><small>Control plane</small></div>
        </div>

        <nav className="primary-nav" aria-label="Điều hướng chính">
          <button className={activeTab === "workflows" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("workflows")}>
            <span className="nav-icon">⌘</span><span><strong>Workflows</strong><small>Thiết kế và chạy flow</small></span><b>{workflows.length}</b>
          </button>
          <button className={activeTab === "skills" ? "nav-tab active" : "nav-tab"} onClick={() => setActiveTab("skills")}>
            <span className="nav-icon">◆</span><span><strong>Skills</strong><small>Quản lý kỹ năng agent</small></span><b>{enabledSkills.length}</b>
          </button>
        </nav>

        {activeTab === "workflows" ? (
          <>
            <div className="sidebar-title"><span>Danh sách workflow</span><b>{workflows.length}</b></div>
            <nav className="workflow-list">
              {workflows.map((workflow) => (
                <button className={workflow.id === selectedId ? "workflow active" : "workflow"} key={workflow.id} onClick={() => openWorkflow(workflow.id)}>
                  <span className="flow-icon">↗</span><span><strong>{workflow.name}</strong><small>Revision {workflow.revision}</small></span>
                </button>
              ))}
            </nav>
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
          </>
        ) : (
          <div className="sidebar-note">
            <strong>Skill library</strong>
            <p>Các skill được cài ở đây sẽ xuất hiện trong bộ chọn của từng workflow.</p>
          </div>
        )}
      </aside>

      <main className={activeTab === "workflows" ? "workspace" : "workspace skills-workspace"}>
        <header>
          <div>
            <p className="eyebrow">{activeTab === "workflows" ? "WORKFLOW STUDIO" : "SKILL LIBRARY"}</p>
            <h1>{activeTab === "workflows" ? selected?.name ?? "AgentFlow" : "Skills"}</h1>
          </div>
          <div className="account"><span className="status-dot" /><div><strong>{keycloak.tokenParsed?.preferred_username ?? "developer"}</strong><small>Đã xác thực</small></div><button onClick={() => void keycloak.logout()}>Đăng xuất</button></div>
        </header>

        {activeTab === "workflows" ? (
          <>
            <section className="canvas-card">
              <div className="canvas-toolbar"><div><span className="pill">Draft</span>{selected && <span>Revision {selected.revision}</span>}</div><span className="save-state">{message}</span></div>
              {!selected ? (
                <div className="empty"><div>⌁</div><h2>Chưa có workflow</h2><p>Tạo workflow bên trái để chỉnh graph JSON, validate và chạy thử.</p></div>
              ) : (
                <div className="editor-grid">
                  <section className="flow-preview">
                    <div className="node trigger"><small>TRIGGER</small><strong>Manual start</strong><span>Nhận input từ người dùng</span></div>
                    <div className="connector"><i /><b>1</b><i /></div>
                    <div className="node agent-node"><small>AI AGENT</small><strong>Deep Agent</strong><span>Lập kế hoạch bằng LLM</span></div>
                    <div className="connector"><i /><b>2</b><i /></div>
                    <div className="node end"><small>OUTPUT</small><strong>Complete</strong><span>Trả kết quả của flow</span></div>
                  </section>
                  <section className="json-panel"><div className="panel-title"><span>Graph definition</span><code>JSON</code></div><textarea value={editor} onChange={(event) => setEditor(event.target.value)} spellCheck={false} /></section>
                </div>
              )}
            </section>

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
        ) : (
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
        )}

        {activeTab === "workflows" && (
          <footer className="action-bar">
            <div className="run-state">{run ? <><span className="run-dot" />Run <code>{run.id.slice(0, 8)}</code> · {run.status}</> : "Chưa có run trong phiên này"}</div>
            <div className="actions">
              <button disabled={!selected || busy} onClick={() => void perform(async () => { const result = await api.validate(selected!.id); setMessage(result.valid ? "Graph hợp lệ" : result.errors.join(" · ")); })}>Kiểm tra</button>
              <button disabled={!selected || busy} onClick={() => void perform(async () => { const saved = await api.updateDraft(selected!, parseDraft()); setWorkflows((items) => items.map((item) => item.id === saved.id ? saved : item)); setMessage(`Đã lưu revision ${saved.revision}`); })}>Lưu draft</button>
              <button className="primary" disabled={!selected || busy} onClick={() => void perform(async () => { const published = await api.publish(selected!.id); setVersion(published); setMessage(`Đã publish version ${published.version}`); })}>Publish</button>
              <button className="run-button" disabled={!selected || !version || busy} onClick={() => void perform(async () => { const created = await api.createRun(selected!.id, version!.id); setRun(created); setMessage(`Run đang ở trạng thái ${created.status}`); })}>▶ Chạy</button>
            </div>
          </footer>
        )}
      </main>
    </div>
  );
}
