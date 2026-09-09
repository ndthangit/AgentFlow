import { useState } from "react";

import { api } from "./api";
import type { LlmProvider, OpenRouterSettings, ProviderModel } from "./types";

const defaultSettings: OpenRouterSettings = {
  default_model: null,
  site_url: null,
  app_title: "AgentFlow",
};

type Props = {
  providers: LlmProvider[];
  refresh: () => Promise<void>;
};

export function ModelsPage({ providers, refresh }: Props) {
  const [name, setName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [settings, setSettings] = useState(defaultSettings);
  const [models, setModels] = useState<Record<string, ProviderModel[]>>({});
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({});
  const [loadingId, setLoadingId] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Sẵn sàng cấu hình provider");

  async function run(action: () => Promise<void>) {
    setBusy(true);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Có lỗi xảy ra");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="models-page">
      <div className="library-intro">
        <div><p className="eyebrow">LLM CONNECTIONS</p><h2>Model providers</h2><p>Quản lý kết nối tới các nhà cung cấp LLM và kiểm tra catalog model có thể sử dụng.</p></div>
        <span className="library-count"><strong>{providers.filter((item) => item.enabled).length}</strong> đang bật</span>
      </div>

      <div className="models-layout">
        <div className="provider-list">
          {providers.map((provider) => (
            <article className={provider.enabled ? "provider-card" : "provider-card disabled"} key={provider.id}>
              <div className="provider-heading">
                <div><span className="provider-logo">OR</span><span><strong>{provider.name}</strong><small>OpenRouter · revision {provider.revision}</small></span></div>
                <span className={provider.enabled ? "provider-status enabled" : "provider-status"}>{provider.enabled ? "Đang bật" : "Đã tắt"}</span>
              </div>
              <dl className="provider-details">
                <div><dt>Model mặc định</dt><dd>{provider.settings.default_model || "Chưa chọn"}</dd></div>
                <div><dt>API key</dt><dd>{provider.has_api_key ? "Đã lưu (mã hóa)" : "Chưa cấu hình"}</dd></div>
              </dl>
              <div className="provider-key-update">
                <input
                  type="password"
                  autoComplete="new-password"
                  maxLength={1000}
                  placeholder="Nhập API key mới để thay thế"
                  value={replacementKeys[provider.id] ?? ""}
                  onChange={(event) => setReplacementKeys((current) => ({ ...current, [provider.id]: event.target.value }))}
                />
                <button disabled={busy || !(replacementKeys[provider.id] ?? "").trim()} onClick={() => void run(async () => {
                  const replacementKey = replacementKeys[provider.id]?.trim();
                  if (!replacementKey) return;
                  setMessage("Đang kiểm tra API key mới…");
                  await api.updateLlmProvider(provider, {
                    name: provider.name,
                    settings: provider.settings,
                    enabled: provider.enabled,
                    api_key: replacementKey,
                  });
                  setReplacementKeys((current) => ({ ...current, [provider.id]: "" }));
                  await refresh();
                  setMessage("API key hợp lệ và đã được thay thế");
                })}>Thay API key</button>
              </div>
              <div className="provider-actions">
                <button disabled={busy} onClick={() => void run(async () => {
                  await api.updateLlmProvider(provider, { name: provider.name, settings: provider.settings, enabled: !provider.enabled });
                  await refresh();
                  setMessage(provider.enabled ? "Đã tắt provider" : "Đã bật provider");
                })}>{provider.enabled ? "Tắt" : "Bật"}</button>
                <button className="primary" disabled={busy || !provider.enabled} onClick={() => void run(async () => {
                  setLoadingId(provider.id);
                  try {
                    const result = await api.listProviderModels(provider.id);
                    setModels((current) => ({ ...current, [provider.id]: result }));
                    setMessage(`Đã tải ${result.length} model từ OpenRouter`);
                  } finally {
                    setLoadingId(undefined);
                  }
                })}>{loadingId === provider.id ? "Đang tải…" : "Tải models"}</button>
              </div>
              {models[provider.id] && (
                <div className="model-catalog">
                  <div className="model-catalog-title"><strong>Models</strong><span>{models[provider.id].length} kết quả</span></div>
                  {models[provider.id].slice(0, 30).map((model) => (
                    <div className="model-row" key={model.id}><span><strong>{model.name}</strong><code>{model.id}</code></span><small>{model.context_length ? `${model.context_length.toLocaleString()} tokens` : "N/A"}</small></div>
                  ))}
                  {models[provider.id].length > 30 && <p className="model-overflow">Đang hiển thị 30 model đầu tiên.</p>}
                </div>
              )}
            </article>
          ))}
          {!providers.length && <div className="catalog-empty">Chưa có provider. Hãy thêm kết nối OpenRouter đầu tiên.</div>}
        </div>

        <form className="provider-form" onSubmit={(event) => {
          event.preventDefault();
          if (!name.trim() || !apiKey.trim()) return;
          void run(async () => {
            setMessage("Đang kiểm tra kết nối OpenRouter…");
            await api.createLlmProvider({ name: name.trim(), kind: "openrouter", api_key: apiKey.trim(), settings });
            setName("");
            setApiKey("");
            setSettings(defaultSettings);
            await refresh();
            setMessage("Kết nối hợp lệ, đã lưu OpenRouter provider");
          });
        }}>
          <div><p className="eyebrow">ADD PROVIDER</p><h2>Kết nối OpenRouter</h2><p>API key được kiểm tra với OpenRouter trước khi lưu và chỉ được lưu ở dạng mã hóa. Secret sẽ không được trả về trình duyệt.</p></div>
          <label>Tên kết nối<input required maxLength={160} placeholder="OpenRouter chính" value={name} onChange={(event) => setName(event.target.value)} /></label>
          <label>OpenRouter API key<input required type="password" autoComplete="new-password" maxLength={1000} placeholder="sk-or-v1-…" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /></label>
          <label>Model mặc định<input placeholder="openai/gpt-5.2" value={settings.default_model ?? ""} onChange={(event) => setSettings({ ...settings, default_model: event.target.value || null })} /></label>
          <label>Site URL<input type="url" placeholder="http://localhost:3000" value={settings.site_url ?? ""} onChange={(event) => setSettings({ ...settings, site_url: event.target.value || null })} /></label>
          <label>App title<input required maxLength={120} value={settings.app_title} onChange={(event) => setSettings({ ...settings, app_title: event.target.value })} /></label>
          <button className="install-button" disabled={busy || !name.trim() || !apiKey.trim()}>{busy ? "Đang kiểm tra…" : "Thêm provider"}</button>
          <span className="form-message">{message}</span>
        </form>
      </div>
    </section>
  );
}
