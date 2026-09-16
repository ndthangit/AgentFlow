import { useState } from "react";

import { api } from "./api";
import type { LlmProvider, OpenRouterSettings, ProviderModel } from "./types";

type Props = {
  providers: LlmProvider[];
  refresh: () => Promise<void>;
};

type ModelTestState = {
  status: "testing" | "ok" | "error";
  detail?: string;
};

function selectedModels(provider: LlmProvider) {
  return provider.settings.selected_models ?? [];
}

function settingsWithModels(provider: LlmProvider, modelIds: string[]): OpenRouterSettings {
  const currentDefault = provider.settings.default_model;
  return {
    ...provider.settings,
    selected_models: modelIds,
    default_model: currentDefault && modelIds.includes(currentDefault)
      ? currentDefault
      : modelIds[0] ?? null,
  };
}

export function ModelsPage({ providers, refresh }: Props) {
  const [apiKey, setApiKey] = useState("");
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({});
  const [catalogs, setCatalogs] = useState<Record<string, ProviderModel[]>>({});
  const [draftSelections, setDraftSelections] = useState<Record<string, string[]>>({});
  const [searches, setSearches] = useState<Record<string, string>>({});
  const [configuringId, setConfiguringId] = useState<string>();
  const [busyAction, setBusyAction] = useState<string>();
  const [modelTests, setModelTests] = useState<Record<string, ModelTestState>>({});
  const [message, setMessage] = useState("Sẵn sàng cấu hình provider");

  async function run(actionId: string, action: () => Promise<void>) {
    setBusyAction(actionId);
    try {
      await action();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Có lỗi xảy ra");
    } finally {
      setBusyAction(undefined);
    }
  }

  async function openModelPicker(provider: LlmProvider) {
    await run(`catalog:${provider.id}`, async () => {
      setMessage("Đang kiểm tra API key và tải danh sách model…");
      const catalog = await api.listProviderModels(provider.id);
      const availableIds = new Set(catalog.map((model) => model.id));
      setCatalogs((current) => ({ ...current, [provider.id]: catalog }));
      setDraftSelections((current) => ({
        ...current,
        [provider.id]: selectedModels(provider).filter((id) => availableIds.has(id)),
      }));
      setConfiguringId(provider.id);
      setMessage(`Đã tải ${catalog.length} model từ OpenRouter`);
    });
  }

  function toggleModel(providerId: string, modelId: string) {
    setDraftSelections((current) => {
      const selected = current[providerId] ?? [];
      return {
        ...current,
        [providerId]: selected.includes(modelId)
          ? selected.filter((id) => id !== modelId)
          : [...selected, modelId],
      };
    });
  }

  const testKey = (providerId: string, modelId: string) => `${providerId}:${modelId}`;

  return (
    <section className="models-page">
      <div className="library-intro">
        <div>
          <p className="eyebrow">LLM CONNECTIONS</p>
          <h2>Model providers</h2>
          <p>Thêm API key, kích hoạt provider bằng cách chọn model, rồi kiểm tra từng model trước khi sử dụng.</p>
        </div>
        <span className="library-count"><strong>{providers.filter((item) => item.enabled).length}</strong> đang hoạt động</span>
      </div>

      <div className="models-layout">
        <div className="provider-list">
          {providers.map((provider) => {
            const chosenModels = selectedModels(provider);
            const catalog = catalogs[provider.id] ?? [];
            const draft = draftSelections[provider.id] ?? [];
            const query = (searches[provider.id] ?? "").trim().toLowerCase();
            const filteredCatalog = query
              ? catalog.filter((model) => `${model.name} ${model.id}`.toLowerCase().includes(query))
              : catalog;
            const isConfiguring = configuringId === provider.id;

            return (
              <article className="provider-card" key={provider.id}>
                <div className="provider-heading">
                  <div>
                    <span className="provider-logo">OR</span>
                    <span><strong>{provider.name}</strong><small>OpenRouter · revision {provider.revision}</small></span>
                  </div>
                  <span className={provider.enabled ? "provider-status enabled" : "provider-status"}>
                    {provider.enabled ? "Active" : "Inactive"}
                  </span>
                </div>

                <dl className="provider-details">
                  <div><dt>Models đã chọn</dt><dd>{chosenModels.length || "Chưa chọn"}</dd></div>
                  <div><dt>API key</dt><dd>{provider.has_api_key ? "Đã lưu (mã hóa)" : "Chưa cấu hình"}</dd></div>
                </dl>

                <div className="provider-key-update">
                  <input
                    type="password"
                    autoComplete="new-password"
                    maxLength={1000}
                    placeholder="API key mới"
                    value={replacementKeys[provider.id] ?? ""}
                    onChange={(event) => setReplacementKeys((current) => ({ ...current, [provider.id]: event.target.value }))}
                  />
                  <button
                    disabled={Boolean(busyAction) || !(replacementKeys[provider.id] ?? "").trim()}
                    onClick={() => void run(`key:${provider.id}`, async () => {
                      const replacementKey = replacementKeys[provider.id]?.trim();
                      if (!replacementKey) return;
                      await api.updateLlmProvider(provider, {
                        name: provider.name,
                        settings: provider.settings,
                        enabled: provider.enabled,
                        api_key: replacementKey,
                      });
                      setReplacementKeys((current) => ({ ...current, [provider.id]: "" }));
                      await refresh();
                      setMessage(provider.enabled
                        ? "API key hợp lệ và đã được thay thế"
                        : "Đã lưu API key mới; key sẽ được kiểm tra khi kích hoạt");
                    })}
                  >Thay API key</button>
                </div>

                <div className="provider-actions">
                  <button
                    className="danger"
                    disabled={Boolean(busyAction)}
                    aria-label={`Xóa provider ${provider.name}`}
                    onClick={() => {
                      if (!window.confirm(`Xóa provider “${provider.name}”? API key và danh sách model đã chọn sẽ bị xóa.`)) return;
                      void run(`delete:${provider.id}`, async () => {
                        await api.deleteLlmProvider(provider.id);
                        if (configuringId === provider.id) setConfiguringId(undefined);
                        await refresh();
                        setMessage(`Đã xóa provider “${provider.name}”`);
                      });
                    }}
                  >{busyAction === `delete:${provider.id}` ? "Đang xóa…" : "Xóa"}</button>
                  {provider.enabled && (
                    <button disabled={Boolean(busyAction)} onClick={() => void run(`disable:${provider.id}`, async () => {
                      await api.updateLlmProvider(provider, {
                        name: provider.name,
                        settings: provider.settings,
                        enabled: false,
                      });
                      await refresh();
                      setMessage("Đã chuyển provider sang Inactive");
                    })}>Tắt</button>
                  )}
                  <button className="primary" disabled={Boolean(busyAction)} onClick={() => void openModelPicker(provider)}>
                    {busyAction === `catalog:${provider.id}` ? "Đang tải…" : provider.enabled ? "Quản lý models" : "Chọn model & kích hoạt"}
                  </button>
                </div>

                {isConfiguring && (
                  <div className="model-picker">
                    <div className="model-picker-heading">
                      <span><strong>Chọn models muốn sử dụng</strong><small>Phải chọn ít nhất một model</small></span>
                      <b>{draft.length} đã chọn</b>
                    </div>
                    <input
                      className="model-search"
                      placeholder="Tìm theo tên hoặc model ID…"
                      value={searches[provider.id] ?? ""}
                      onChange={(event) => setSearches((current) => ({ ...current, [provider.id]: event.target.value }))}
                    />
                    <div className="model-options">
                      {filteredCatalog.map((model) => (
                        <label className={draft.includes(model.id) ? "model-option selected" : "model-option"} key={model.id}>
                          <input type="checkbox" checked={draft.includes(model.id)} onChange={() => toggleModel(provider.id, model.id)} />
                          <span><strong>{model.name}</strong><code>{model.id}</code></span>
                          <small>{model.context_length ? `${model.context_length.toLocaleString()} tokens` : "N/A"}</small>
                        </label>
                      ))}
                      {!filteredCatalog.length && <p className="model-picker-empty">Không tìm thấy model phù hợp.</p>}
                    </div>
                    <div className="model-picker-actions">
                      <button disabled={Boolean(busyAction)} onClick={() => setConfiguringId(undefined)}>Hủy</button>
                      <button
                        className="primary"
                        disabled={Boolean(busyAction) || draft.length === 0}
                        onClick={() => void run(`save:${provider.id}`, async () => {
                          await api.updateLlmProvider(provider, {
                            name: provider.name,
                            settings: settingsWithModels(provider, draft),
                            enabled: true,
                          });
                          setConfiguringId(undefined);
                          await refresh();
                          setMessage(`Provider đã Active với ${draft.length} model`);
                        })}
                      >{busyAction === `save:${provider.id}` ? "Đang kiểm tra…" : provider.enabled ? "Lưu models" : "Lưu & kích hoạt"}</button>
                    </div>
                  </div>
                )}

                {chosenModels.length > 0 && (
                  <div className="selected-models">
                    <div className="model-catalog-title"><strong>Models đang sử dụng</strong><span>{chosenModels.length} model</span></div>
                    {chosenModels.map((modelId) => {
                      const model = catalog.find((item) => item.id === modelId);
                      const state = modelTests[testKey(provider.id, modelId)];
                      return (
                        <div className="selected-model-row" key={modelId}>
                          <span><strong>{model?.name ?? modelId}</strong><code>{modelId}</code></span>
                          <span className={`model-test-state ${state?.status ?? ""}`}>
                            {state?.status === "testing" && "Đang kiểm tra…"}
                            {state?.status === "ok" && `Hoạt động · ${state.detail}`}
                            {state?.status === "error" && state.detail}
                          </span>
                          <button
                            disabled={!provider.enabled || state?.status === "testing"}
                            onClick={() => {
                              const key = testKey(provider.id, modelId);
                              setModelTests((current) => ({ ...current, [key]: { status: "testing" } }));
                              void api.testProviderModel(provider.id, modelId)
                                .then((result) => {
                                  setModelTests((current) => ({ ...current, [key]: { status: "ok", detail: `${result.latency_ms} ms` } }));
                                  setMessage(`Model ${modelId} đang hoạt động`);
                                })
                                .catch((error: unknown) => {
                                  const detail = error instanceof Error ? error.message : "Kiểm tra thất bại";
                                  setModelTests((current) => ({ ...current, [key]: { status: "error", detail } }));
                                  setMessage(detail);
                                });
                            }}
                          >Kiểm tra</button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </article>
            );
          })}
          {!providers.length && <div className="catalog-empty">Chưa có provider. Hãy thêm API key OpenRouter để bắt đầu.</div>}
        </div>

        <form className="provider-form" onSubmit={(event) => {
          event.preventDefault();
          if (!apiKey.trim()) return;
          void run("create", async () => {
            await api.createLlmProvider({ api_key: apiKey.trim() });
            setApiKey("");
            await refresh();
            setMessage("Đã lưu OpenRouter ở trạng thái Inactive. Hãy chọn model để kích hoạt.");
          });
        }}>
          <div>
            <p className="eyebrow">ADD PROVIDER</p>
            <h2>Thêm OpenRouter</h2>
            <p>Chỉ cần nhập API key. Provider mới luôn ở trạng thái Inactive và key chỉ được lưu dưới dạng mã hóa.</p>
          </div>
          <label>OpenRouter API key
            <input
              required
              type="password"
              autoComplete="new-password"
              maxLength={1000}
              placeholder="sk-or-v1-…"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
            />
          </label>
          <button className="install-button" disabled={Boolean(busyAction) || !apiKey.trim()}>
            {busyAction === "create" ? "Đang lưu…" : "Thêm provider"}
          </button>
          <span className="form-message">{message}</span>
        </form>
      </div>
    </section>
  );
}
