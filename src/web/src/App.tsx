import { useEffect, useMemo, useState } from "react";

import { api } from "./api";
import { keycloak } from "./auth";
import type { FlowRun, Workflow, WorkflowVersion } from "./types";

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

export function App() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [selectedId, setSelectedId] = useState<string>();
  const [editor, setEditor] = useState(JSON.stringify(starterDraft, null, 2));
  const [name, setName] = useState("");
  const [message, setMessage] = useState("Đang tải workflow…");
  const [busy, setBusy] = useState(false);
  const [version, setVersion] = useState<WorkflowVersion>();
  const [run, setRun] = useState<FlowRun>();

  const selected = useMemo(
    () => workflows.find((workflow) => workflow.id === selectedId),
    [selectedId, workflows],
  );

  async function refresh(select?: string) {
    const items = await api.listWorkflows();
    setWorkflows(items);
    setSelectedId(select ?? selectedId ?? items[0]?.id);
    setMessage(items.length ? "Sẵn sàng" : "Tạo workflow đầu tiên để bắt đầu");
  }

  useEffect(() => {
    refresh().catch((error: Error) => setMessage(error.message));
  }, []);

  useEffect(() => {
    if (selected) {
      setEditor(JSON.stringify(selected.draft, null, 2));
      setVersion(undefined);
      setRun(undefined);
    }
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

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand"><span>AF</span><div><strong>AgentFlow</strong><small>Control plane</small></div></div>
        <div className="sidebar-title"><span>Workflows</span><b>{workflows.length}</b></div>
        <nav className="workflow-list">
          {workflows.map((workflow) => (
            <button className={workflow.id === selectedId ? "workflow active" : "workflow"} key={workflow.id} onClick={() => setSelectedId(workflow.id)}>
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
            await refresh(created.id);
            setMessage("Đã tạo workflow");
          });
        }}>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Tên workflow mới" maxLength={160} />
          <button disabled={busy || !name.trim()}>Tạo mới</button>
        </form>
      </aside>

      <main className="workspace">
        <header>
          <div><p className="eyebrow">WORKFLOW STUDIO</p><h1>{selected?.name ?? "AgentFlow"}</h1></div>
          <div className="account"><span className="status-dot" /><div><strong>{keycloak.tokenParsed?.preferred_username ?? "developer"}</strong><small>Đã xác thực</small></div><button onClick={() => void keycloak.logout()}>Đăng xuất</button></div>
        </header>

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

        <footer className="action-bar">
          <div className="run-state">{run ? <><span className="run-dot" />Run <code>{run.id.slice(0, 8)}</code> · {run.status}</> : "Chưa có run trong phiên này"}</div>
          <div className="actions">
            <button disabled={!selected || busy} onClick={() => void perform(async () => { const result = await api.validate(selected!.id); setMessage(result.valid ? "Graph hợp lệ" : result.errors.join(" · ")); })}>Kiểm tra</button>
            <button disabled={!selected || busy} onClick={() => void perform(async () => { const saved = await api.updateDraft(selected!, parseDraft()); setWorkflows((items) => items.map((item) => item.id === saved.id ? saved : item)); setMessage(`Đã lưu revision ${saved.revision}`); })}>Lưu draft</button>
            <button className="primary" disabled={!selected || busy} onClick={() => void perform(async () => { const published = await api.publish(selected!.id); setVersion(published); setMessage(`Đã publish version ${published.version}`); })}>Publish</button>
            <button className="run-button" disabled={!selected || !version || busy} onClick={() => void perform(async () => { const created = await api.createRun(selected!.id, version!.id); setRun(created); setMessage(`Run đang ở trạng thái ${created.status}`); })}>▶ Chạy</button>
          </div>
        </footer>
      </main>
    </div>
  );
}
