import { useState } from "react";

import { api } from "../../shared/api";
import type { LlmProvider, ProviderModel, ProviderSettings } from "../../shared/types";

type Props = {
  providers: LlmProvider[];
  refresh: () => Promise<void>;
};

type ModelTestState = {
  status: "testing" | "ok" | "error";
  detail?: string;
};

const providerOptions: Array<{
  kind: LlmProvider["kind"];
  name: string;
  initials: string;
  description: string;
  keyLabel: string;
  keyPlaceholder: string;
  customEndpoint?: boolean;
  endpointPlaceholder?: string;
}> = [
  {
    kind: "openrouter",
    name: "OpenRouter",
    initials: "OR",
    description: "Kết nối một API key để sử dụng catalog model của OpenRouter.",
    keyLabel: "OpenRouter API key",
    keyPlaceholder: "sk-or-v1-…",
  },
  {
    kind: "openai",
    name: "OpenAI",
    initials: "OA",
    description: "Kết nối trực tiếp tới catalog và Chat Completions của OpenAI.",
    keyLabel: "OpenAI API key",
    keyPlaceholder: "sk-…",
  },
  {
    kind: "anthropic",
    name: "Anthropic",
    initials: "AN",
    description: "Sử dụng Claude qua Models API và Messages API của Anthropic.",
    keyLabel: "Anthropic API key",
    keyPlaceholder: "sk-ant-…",
  },
  {
    kind: "nvidia",
    name: "NVIDIA",
    initials: "NV",
    description: "Sử dụng NVIDIA API Catalog qua giao thức OpenAI-compatible.",
    keyLabel: "NVIDIA API key",
    keyPlaceholder: "nvapi-…",
  },
  {
    kind: "openai-compatible",
    name: "OpenAI-compatible",
    initials: "OC",
    description: "Kết nối provider bất kỳ hỗ trợ /models và /chat/completions.",
    keyLabel: "Provider API key",
    keyPlaceholder: "API key của provider",
    customEndpoint: true,
    endpointPlaceholder: "https://api.provider.example/v1",
  },
  {
    kind: "anthropic-compatible",
    name: "Anthropic-compatible",
    initials: "AC",
    description: "Kết nối provider hỗ trợ Anthropic Models và Messages API.",
    keyLabel: "Provider API key",
    keyPlaceholder: "API key của provider",
    customEndpoint: true,
    endpointPlaceholder: "https://api.provider.example",
  },
];

const providerMetadata = (kind: LlmProvider["kind"]) =>
  providerOptions.find((option) => option.kind === kind) ?? providerOptions[0];

function selectedModels(provider: LlmProvider) {
  return provider.settings.selected_models ?? [];
}

function settingsWithModels(provider: LlmProvider, modelIds: string[], defaultModel: string): ProviderSettings {
  return {
    ...provider.settings,
    selected_models: modelIds,
    default_model: defaultModel && modelIds.includes(defaultModel)
      ? defaultModel
      : modelIds[0] ?? null,
  };
}

export function ModelsPage({ providers, refresh }: Props) {
  const [providerName, setProviderName] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [customModels, setCustomModels] = useState("");
  const [isAddingProvider, setIsAddingProvider] = useState(false);
  const [selectedProviderKind, setSelectedProviderKind] = useState<LlmProvider["kind"] | "">("");
  const [replacementKeys, setReplacementKeys] = useState<Record<string, string>>({});
  const [catalogs, setCatalogs] = useState<Record<string, ProviderModel[]>>({});
  const [draftSelections, setDraftSelections] = useState<Record<string, string[]>>({});
  const [draftDefaults, setDraftDefaults] = useState<Record<string, string>>({});
  const [searches, setSearches] = useState<Record<string, string>>({});
  const [configuringId, setConfiguringId] = useState<string>();
  const [busyAction, setBusyAction] = useState<string>();
  const [modelTests, setModelTests] = useState<Record<string, ModelTestState>>({});
  const [message, setMessage] = useState("Sẵn sàng cấu hình provider");

  const selectedProviderOption = providerOptions.find((option) => option.kind === selectedProviderKind);

  function closeProviderForm() {
    if (busyAction === "create") return;
    setIsAddingProvider(false);
    setSelectedProviderKind("");
    setProviderName("");
    setApiKey("");
    setBaseUrl("");
    setCustomModels("");
  }

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
      setMessage("Đang tải danh sách model…");
      const catalog = await api.listProviderModels(provider.id);
      const availableIds = new Set(catalog.map((model) => model.id));
      setCatalogs((current) => ({ ...current, [provider.id]: catalog }));
      setDraftSelections((current) => ({
        ...current,
        [provider.id]: selectedModels(provider).filter((id) => availableIds.has(id)),
      }));
      const availableSelection = selectedModels(provider).filter((id) => availableIds.has(id));
      setDraftDefaults((current) => ({
        ...current,
        [provider.id]: provider.settings.default_model
          && availableSelection.includes(provider.settings.default_model)
          ? provider.settings.default_model
          : availableSelection[0] ?? "",
      }));
      setConfiguringId(provider.id);
      setMessage(`Đã tải ${catalog.length} model từ ${providerMetadata(provider.kind).name}`);
    });
  }

  function toggleModel(providerId: string, modelId: string) {
    const selected = draftSelections[providerId] ?? [];
    const next = selected.includes(modelId)
      ? selected.filter((id) => id !== modelId)
      : [...selected, modelId];
    setDraftSelections((current) => ({ ...current, [providerId]: next }));
    if (!next.includes(draftDefaults[providerId] ?? "")) {
      setDraftDefaults((current) => ({ ...current, [providerId]: next[0] ?? "" }));
    }
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
        <div className="library-summary-actions">
          <span className="library-count"><strong>{providers.filter((item) => item.enabled).length}</strong> đang hoạt động</span>
          <button
            className="add-provider-button"
            type="button"
            onClick={() => setIsAddingProvider(true)}
          >+ Thêm provider</button>
        </div>
      </div>

      <div className="provider-feedback" role="status">{message}</div>

      <div className="models-layout">
        <div className="provider-list">
          {providers.map((provider) => {
            const metadata = providerMetadata(provider.kind);
            const chosenModels = selectedModels(provider);
            const catalog = catalogs[provider.id] ?? [];
            const draft = draftSelections[provider.id] ?? [];
            const query = (searches[provider.id] ?? "").trim().toLowerCase();
            const filteredCatalog = query
              ? catalog.filter((model) => `${model.name} ${model.id}`.toLowerCase().includes(query))
              : catalog;
            const isConfiguring = configuringId === provider.id;
            const draftDefault = draftDefaults[provider.id] ?? draft[0] ?? "";

            return (
              <article className={isConfiguring ? "provider-card configuring" : "provider-card"} key={provider.id}>
                <div className="provider-heading">
                  <div>
                    <span className="provider-logo">{metadata.initials}</span>
                    <span><strong>{provider.name}</strong><small>{metadata.name} · revision {provider.revision}</small></span>
                  </div>
                  <span className={provider.enabled ? "provider-status enabled" : "provider-status"}>
                    {provider.enabled ? "Active" : "Inactive"}
                  </span>
                </div>

                <dl className="provider-details">
                  <div><dt>Models đã chọn</dt><dd>{chosenModels.length || "Chưa chọn"}</dd></div>
                  <div><dt>Model mặc định</dt><dd>{provider.settings.default_model ?? "Chưa chọn"}</dd></div>
                  <div><dt>API key</dt><dd>{provider.has_api_key ? "Đã lưu (mã hóa)" : "Chưa cấu hình"}</dd></div>
                  {provider.settings.base_url && <div><dt>Base URL</dt><dd>{provider.settings.base_url}</dd></div>}
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
                      setMessage(provider.settings.custom_models?.length
                        ? "Đã thay API key; hãy kiểm tra một model để xác nhận key mới"
                        : provider.enabled
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
                    <label className="model-default-select">Model mặc định
                      <select
                        value={draftDefault}
                        disabled={!draft.length || Boolean(busyAction)}
                        onChange={(event) => setDraftDefaults((current) => ({
                          ...current,
                          [provider.id]: event.target.value,
                        }))}
                      >
                        {draft.map((modelId) => (
                          <option value={modelId} key={modelId}>{modelId}</option>
                        ))}
                      </select>
                    </label>
                    <div className="model-picker-actions">
                      <button disabled={Boolean(busyAction)} onClick={() => setConfiguringId(undefined)}>Hủy</button>
                      <button
                        className="primary"
                        disabled={Boolean(busyAction) || draft.length === 0}
                        onClick={() => void run(`save:${provider.id}`, async () => {
                          await api.updateLlmProvider(provider, {
                            name: provider.name,
                            settings: settingsWithModels(provider, draft, draftDefault),
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
                          <span>
                            <strong>{model?.name ?? modelId}</strong>
                            <code>{modelId}</code>
                            {provider.settings.default_model === modelId && <small className="default-model-badge">Mặc định</small>}
                          </span>
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
          {!providers.length && <div className="catalog-empty">Chưa có provider. Hãy thêm một kết nối model để bắt đầu.</div>}
        </div>
      </div>

      {isAddingProvider && (
        <div
          className="provider-modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) closeProviderForm();
          }}
        >
          <div className="provider-modal" role="dialog" aria-modal="true" aria-labelledby="add-provider-title">
            <div className="provider-modal-heading">
              <div>
                <p className="eyebrow">ADD PROVIDER</p>
                <h2 id="add-provider-title">Thêm provider</h2>
                <p>Chọn dịch vụ bạn muốn kết nối để mở đúng form cấu hình.</p>
              </div>
              <button type="button" aria-label="Đóng form thêm provider" onClick={closeProviderForm}>×</button>
            </div>

            <div className="provider-choice-list" aria-label="Chọn loại provider">
              {providerOptions.map((option) => (
                <button
                  className={selectedProviderKind === option.kind ? "provider-choice selected" : "provider-choice"}
                  type="button"
                  key={option.kind}
                  onClick={() => setSelectedProviderKind(option.kind)}
                >
                  <span className="provider-logo">{option.initials}</span>
                  <span><strong>{option.name}</strong><small>{option.description}</small></span>
                  <b aria-hidden="true">{selectedProviderKind === option.kind ? "✓" : "›"}</b>
                </button>
              ))}
            </div>

            {selectedProviderOption && (
              <form className="provider-form provider-modal-form" onSubmit={(event) => {
                event.preventDefault();
                const name = providerName.trim();
                const endpoint = baseUrl.trim();
                if (!name || !apiKey.trim() || (selectedProviderOption.customEndpoint && !endpoint)) return;
                const configuredModels = customModels
                  .split(/[\n,]/)
                  .map((model) => model.trim())
                  .filter((model, index, models) => model && models.indexOf(model) === index);
                void run("create", async () => {
                  await api.createLlmProvider({
                    name,
                    kind: selectedProviderOption.kind,
                    api_key: apiKey.trim(),
                    ...(selectedProviderOption.customEndpoint
                      ? { base_url: endpoint, custom_models: configuredModels }
                      : {}),
                  });
                  await refresh();
                  setMessage(`Đã lưu “${name}” ở trạng thái Inactive. Hãy chọn model để kích hoạt.`);
                  setIsAddingProvider(false);
                  setSelectedProviderKind("");
                  setProviderName("");
                  setApiKey("");
                  setBaseUrl("");
                  setCustomModels("");
                });
              }}>
                <div className="provider-config-heading">
                  <span className="provider-logo">{selectedProviderOption.initials}</span>
                  <span><strong>Cấu hình {selectedProviderOption.name}</strong><small>API key được mã hóa trước khi lưu.</small></span>
                </div>
                <label>Tên cấu hình provider
                  <input
                    autoFocus
                    required
                    maxLength={160}
                    placeholder={`Ví dụ: ${selectedProviderOption.name} cá nhân`}
                    value={providerName}
                    onChange={(event) => setProviderName(event.target.value)}
                  />
                  <small>Dùng tên khác nhau để lưu nhiều API key của cùng một nhà cung cấp.</small>
                </label>
                {selectedProviderOption.customEndpoint && (
                  <>
                    <label>Base URL của API
                      <input
                        required
                        type="url"
                        maxLength={2048}
                        placeholder={selectedProviderOption.endpointPlaceholder}
                        value={baseUrl}
                        onChange={(event) => setBaseUrl(event.target.value)}
                      />
                      <small>Dùng HTTPS công khai. OpenAI-compatible thường kết thúc bằng /v1; Anthropic-compatible nhập phần đứng trước /v1.</small>
                    </label>
                    <label>Model IDs (không bắt buộc)
                      <textarea
                        rows={3}
                        maxLength={20000}
                        placeholder={"model-small\nmodel-large"}
                        value={customModels}
                        onChange={(event) => setCustomModels(event.target.value)}
                      />
                      <small>Mỗi dòng hoặc dấu phẩy là một model. Để trống nếu provider hỗ trợ endpoint liệt kê model.</small>
                    </label>
                  </>
                )}
                <label>{selectedProviderOption.keyLabel}
                  <input
                    required
                    type="password"
                    autoComplete="new-password"
                    maxLength={1000}
                    placeholder={selectedProviderOption.keyPlaceholder}
                    value={apiKey}
                    onChange={(event) => setApiKey(event.target.value)}
                  />
                </label>
                <div className="provider-form-actions">
                  <button type="button" onClick={closeProviderForm} disabled={busyAction === "create"}>Hủy</button>
                  <button
                    className="install-button"
                    disabled={Boolean(busyAction)
                      || !providerName.trim()
                      || !apiKey.trim()
                      || Boolean(selectedProviderOption.customEndpoint && !baseUrl.trim())}
                  >
                    {busyAction === "create" ? "Đang lưu…" : "Thêm provider"}
                  </button>
                </div>
                <span className="form-message" role="status">{message}</span>
              </form>
            )}
          </div>
        </div>
      )}
    </section>
  );
}
