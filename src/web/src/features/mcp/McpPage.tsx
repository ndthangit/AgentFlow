import { useState } from "react";

import { api } from "../../shared/api";
import type { McpServer } from "../../shared/types";

type Props = {
  servers: McpServer[];
  refresh: () => Promise<void>;
};

type McpForm = {
  slug: string;
  name: string;
  description: string;
  transport: McpServer["transport"];
  url: string;
  headerName: string;
  headerValue: string;
  clearHeaders: boolean;
  enabled: boolean;
};

const emptyForm: McpForm = {
  slug: "",
  name: "",
  description: "",
  transport: "streamable_http",
  url: "",
  headerName: "Authorization",
  headerValue: "",
  clearHeaders: false,
  enabled: true,
};

export function McpPage({ servers, refresh }: Props) {
  const [editing, setEditing] = useState<McpServer>();
  const [isOpen, setIsOpen] = useState(false);
  const [form, setForm] = useState<McpForm>(emptyForm);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Ready to configure an MCP server");

  function close() {
    if (busy) return;
    setIsOpen(false);
    setEditing(undefined);
    setForm(emptyForm);
  }

  function openNew() {
    setEditing(undefined);
    setForm(emptyForm);
    setIsOpen(true);
  }

  function openEdit(server: McpServer) {
    setEditing(server);
    setForm({
      slug: server.slug,
      name: server.name,
      description: server.description,
      transport: server.transport,
      url: server.url,
      headerName: "Authorization",
      headerValue: "",
      clearHeaders: false,
      enabled: server.enabled,
    });
    setIsOpen(true);
  }

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not update the MCP server");
    } finally {
      setBusy(false);
    }
  }

  function replacementHeaders(): Record<string, string> | undefined {
    if (form.clearHeaders) return {};
    if (!form.headerValue.trim()) return editing ? undefined : {};
    return { [form.headerName.trim() || "Authorization"]: form.headerValue.trim() };
  }

  return (
    <section className="models-page">
      <div className="library-intro">
        <div>
          <p className="eyebrow">TOOLS FOR AGENTS</p>
          <h2>MCP servers</h2>
          <p>Register an MCP server over Streamable HTTP or SSE. Agents receive only the MCP servers selected in their node configuration.</p>
        </div>
        <div className="library-summary-actions">
          <span className="library-count"><strong>{servers.filter((server) => server.enabled).length}</strong> active</span>
          <button className="add-provider-button" type="button" onClick={openNew}>+ Add MCP</button>
        </div>
      </div>

      <div className="provider-feedback" role="status">{message}</div>
      <div className="provider-list">
        {servers.map((server) => (
          <article className="provider-card" key={server.id}>
            <div className="provider-heading">
              <div>
                <span className="provider-logo">MCP</span>
                <span><strong>{server.name}</strong><small>{server.slug} · revision {server.revision}</small></span>
              </div>
              <span className={server.enabled ? "provider-status enabled" : "provider-status"}>{server.enabled ? "Active" : "Inactive"}</span>
            </div>
            <dl className="provider-details">
              <div><dt>Transport</dt><dd>{server.transport === "streamable_http" ? "Streamable HTTP" : "SSE"}</dd></div>
              <div><dt>Authentication</dt><dd>{server.has_headers ? "Encrypted header" : "No header"}</dd></div>
            </dl>
            <p className="skill-description">{server.description || "No description."}</p>
            <code className="mcp-server-url">{server.url}</code>
            <div className="provider-actions">
              <button className="danger" disabled={busy} onClick={() => {
                if (!window.confirm(`Delete MCP “${server.name}”? Workflows that reference it must select it again before publishing.`)) return;
                void run(async () => {
                  await api.deleteMcpServer(server.id);
                  await refresh();
                  setMessage(`Deleted MCP “${server.name}”`);
                });
              }}>Delete</button>
              <button className="primary" disabled={busy} onClick={() => openEdit(server)}>Edit</button>
            </div>
          </article>
        ))}
        {!servers.length && <div className="catalog-empty">No MCP servers yet. Select “Add MCP” to register the first one.</div>}
      </div>

      {isOpen && (
        <div className="provider-modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) close();
        }}>
          <div className="provider-modal skill-modal" role="dialog" aria-modal="true" aria-labelledby="mcp-form-title">
            <div className="provider-modal-heading">
              <div>
                <p className="eyebrow">{editing ? "EDIT MCP" : "REGISTER MCP"}</p>
                <h2 id="mcp-form-title">{editing ? "Edit MCP server" : "Add MCP server"}</h2>
                <p>Only remote connections are supported; secret headers are encrypted before storage.</p>
              </div>
              <button type="button" aria-label="Close MCP form" onClick={close}>×</button>
            </div>
            <form className="skill-form skill-modal-form" onSubmit={(event) => {
              event.preventDefault();
              if (!form.slug.trim() || !form.name.trim() || !form.url.trim()) return;
              void run(async () => {
                const headers = replacementHeaders();
                if (editing) {
                  await api.updateMcpServer(editing, {
                    name: form.name.trim(),
                    description: form.description.trim(),
                    transport: form.transport,
                    url: form.url.trim(),
                    ...(headers === undefined ? {} : { headers }),
                    enabled: form.enabled,
                  });
                } else {
                  await api.createMcpServer({
                    slug: form.slug.trim(),
                    name: form.name.trim(),
                    description: form.description.trim(),
                    transport: form.transport,
                    url: form.url.trim(),
                    headers: headers ?? {},
                    enabled: form.enabled,
                  });
                }
                await refresh();
                setMessage(editing ? `Updated MCP “${form.name.trim()}”` : "Registered the MCP server");
                setIsOpen(false);
                setEditing(undefined);
                setForm(emptyForm);
              });
            }}>
              <label>Slug<input required disabled={Boolean(editing)} pattern="[a-z0-9]+(?:-[a-z0-9]+)*" maxLength={80} placeholder="github-tools" value={form.slug} onChange={(event) => setForm({ ...form, slug: event.target.value.toLowerCase() })} /></label>
              <label>MCP name<input required maxLength={160} placeholder="GitHub MCP" value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></label>
              <label>Description<input maxLength={500} placeholder="Tools provided by this server" value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></label>
              <label>Transport<select value={form.transport} onChange={(event) => setForm({ ...form, transport: event.target.value as McpServer["transport"] })}>
                <option value="streamable_http">Streamable HTTP</option>
                <option value="sse">SSE</option>
              </select></label>
              <label>Endpoint URL<input required type="url" maxLength={2048} placeholder="https://example.com/mcp" value={form.url} onChange={(event) => setForm({ ...form, url: event.target.value })} /></label>
              <label>Authentication header<input maxLength={200} placeholder="Authorization" value={form.headerName} disabled={form.clearHeaders} onChange={(event) => setForm({ ...form, headerName: event.target.value })} /></label>
              <label>Header value<input type="password" autoComplete="new-password" maxLength={2000} placeholder={editing?.has_headers ? "Leave blank to keep the current header" : "Bearer … (optional)"} value={form.headerValue} disabled={form.clearHeaders} onChange={(event) => setForm({ ...form, headerValue: event.target.value })} /></label>
              {editing?.has_headers && <label className="schema-required"><input type="checkbox" checked={form.clearHeaders} onChange={(event) => setForm({ ...form, clearHeaders: event.target.checked, headerValue: "" })} /> Delete saved header</label>}
              <label className="schema-required"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /> MCP is active</label>
              <div className="provider-form-actions">
                <button type="button" onClick={close} disabled={busy}>Cancel</button>
                <button className="install-button" disabled={busy || !form.slug.trim() || !form.name.trim() || !form.url.trim()}>{busy ? "Saving…" : "Save MCP"}</button>
              </div>
              <span className="form-message" role="status">{message}</span>
            </form>
          </div>
        </div>
      )}
    </section>
  );
}
