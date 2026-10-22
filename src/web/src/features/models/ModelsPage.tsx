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
    description: "Connect an API key to use the OpenRouter model catalog.",
    keyLabel: "OpenRouter API key",
    keyPlaceholder: "sk-or-v1-…",
  },
  {
    kind: "openai",
    name: "OpenAI",
    initials: "OA",
    description: "Connect directly to the OpenAI catalog and Chat Completions API.",
    keyLabel: "OpenAI API key",
    keyPlaceholder: "sk-…",
  },
  {
    kind: "anthropic",
    name: "Anthropic",
    initials: "AN",
    description: "Use Claude through Anthropic's Models and Messages APIs.",
    keyLabel: "Anthropic API key",
    keyPlaceholder: "sk-ant-…",
  },
  {
    kind: "nvidia",
    name: "NVIDIA",
    initials: "NV",
    description: "Use the NVIDIA API Catalog through its OpenAI-compatible interface.",
    keyLabel: "NVIDIA API key",
    keyPlaceholder: "nvapi-…",
  },
  {
    kind: "openai-compatible",
    name: "OpenAI-compatible",
    initials: "OC",
    description: "Connect any provider that supports /models and /chat/completions.",
    keyLabel: "Provider API key",
    keyPlaceholder: "Provider API key",
    customEndpoint: true,
    endpointPlaceholder: "https://api.provider.example/v1",
  },
  {
    kind: "anthropic-compatible",
    name: "Anthropic-compatible",
    initials: "AC",
    description: "Connect a provider that supports the Anthropic Models and Messages APIs.",
    keyLabel: "Provider API key",
    keyPlaceholder: "Provider API key",
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
  const [message, setMessage] = useState("Ready to configure a provider");

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
      setMessage(error instanceof Error ? error.message : "Something went wrong");
    } finally {
      setBusyAction(undefined);
    }
  }

  async function openModelPicker(provider: LlmProvider) {
    await run(`catalog:${provider.id}`, async () => {
      setMessage("Loading model catalog…");
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
      setMessage(`Loaded ${catalog.length} models from ${providerMetadata(provider.kind).name}`);
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
          <p>Add an API key, activate the provider by selecting models, then test each model before use.</p>
        </div>
        <div className="library-summary-actions">
          <span className="library-count"><strong>{providers.filter((item) => item.enabled).length}</strong> active</span>
          <button
            className="add-provider-button"
            type="button"
            onClick={() => setIsAddingProvider(true)}
          >+ Add provider</button>
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
                  <div><dt>Selected models</dt><dd>{chosenModels.length || "None selected"}</dd></div>
                  <div><dt>Default model</dt><dd>{provider.settings.default_model ?? "None selected"}</dd></div>
                  <div><dt>API key</dt><dd>{provider.has_api_key ? "Saved (encrypted)" : "Not configured"}</dd></div>
                  {provider.settings.base_url && <div><dt>Base URL</dt><dd>{provider.settings.base_url}</dd></div>}
                </dl>

                <div className="provider-key-update">
                  <input
                    type="password"
                    autoComplete="new-password"
                    maxLength={1000}
                    placeholder="New API key"
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
                        ? "API key replaced; test a model to verify the new key"
                        : provider.enabled
                          ? "The API key is valid and has been replaced"
                          : "The new API key is saved and will be tested during activation");
                    })}
                  >Thay API key</button>
                </div>

                <div className="provider-actions">
                  <button
                    className="danger"
                    disabled={Boolean(busyAction)}
                    aria-label={`Delete provider ${provider.name}`}
                    onClick={() => {
                      if (!window.confirm(`Delete provider “${provider.name}”? Its API key and selected model list will be deleted.`)) return;
                      void run(`delete:${provider.id}`, async () => {
                        await api.deleteLlmProvider(provider.id);
                        if (configuringId === provider.id) setConfiguringId(undefined);
                        await refresh();
                        setMessage(`Deleted provider “${provider.name}”`);
                      });
                    }}
                  >{busyAction === `delete:${provider.id}` ? "Deleting…" : "Delete"}</button>
                  {provider.enabled && (
                    <button disabled={Boolean(busyAction)} onClick={() => void run(`disable:${provider.id}`, async () => {
                      await api.updateLlmProvider(provider, {
                        name: provider.name,
                        settings: provider.settings,
                        enabled: false,
                      });
                      await refresh();
                      setMessage("Provider set to inactive");
                    })}>Disable</button>
                  )}
                  <button className="primary" disabled={Boolean(busyAction)} onClick={() => void openModelPicker(provider)}>
                    {busyAction === `catalog:${provider.id}` ? "Loading…" : provider.enabled ? "Manage models" : "Select models & activate"}
                  </button>
                </div>

                {isConfiguring && (
                  <div className="model-picker">
                    <div className="model-picker-heading">
                      <span><strong>Select models to use</strong><small>Select at least one model</small></span>
                      <b>{draft.length} selected</b>
                    </div>
                    <input
                      className="model-search"
                      placeholder="Search by name or model ID…"
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
                      {!filteredCatalog.length && <p className="model-picker-empty">No matching models found.</p>}
                    </div>
                    <label className="model-default-select">Default model
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
                      <button disabled={Boolean(busyAction)} onClick={() => setConfiguringId(undefined)}>Cancel</button>
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
                          setMessage(`Provider activated with ${draft.length} models`);
                        })}
                      >{busyAction === `save:${provider.id}` ? "Testing…" : provider.enabled ? "Save models" : "Save & activate"}</button>
                    </div>
                  </div>
                )}

                {chosenModels.length > 0 && (
                  <div className="selected-models">
                    <div className="model-catalog-title"><strong>Models in use</strong><span>{chosenModels.length} models</span></div>
                    {chosenModels.map((modelId) => {
                      const model = catalog.find((item) => item.id === modelId);
                      const state = modelTests[testKey(provider.id, modelId)];
                      return (
                        <div className="selected-model-row" key={modelId}>
                          <span>
                            <strong>{model?.name ?? modelId}</strong>
                            <code>{modelId}</code>
                            {provider.settings.default_model === modelId && <small className="default-model-badge">Default</small>}
                          </span>
                          <span className={`model-test-state ${state?.status ?? ""}`}>
                            {state?.status === "testing" && "Testing…"}
                            {state?.status === "ok" && `Working · ${state.detail}`}
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
                                  setMessage(`Model ${modelId} is working`);
                                })
                                .catch((error: unknown) => {
                                  const detail = error instanceof Error ? error.message : "Test failed";
                                  setModelTests((current) => ({ ...current, [key]: { status: "error", detail } }));
                                  setMessage(detail);
                                });
                            }}
                          >Test</button>
                        </div>
                      );
                    })}
                  </div>
                )}
              </article>
            );
          })}
          {!providers.length && <div className="catalog-empty">No providers yet. Add a model connection to get started.</div>}
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
                <h2 id="add-provider-title">Add provider</h2>
                <p>Select the service you want to connect to open the appropriate configuration form.</p>
              </div>
              <button type="button" aria-label="Close add-provider form" onClick={closeProviderForm}>×</button>
            </div>

            <div className="provider-choice-list" aria-label="Select provider type">
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
                  setMessage(`Saved “${name}” as inactive. Select models to activate it.`);
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
                  <span><strong>Configure {selectedProviderOption.name}</strong><small>The API key is encrypted before it is stored.</small></span>
                </div>
                <label>Provider configuration name
                  <input
                    autoFocus
                    required
                    maxLength={160}
                    placeholder={`Example: Personal ${selectedProviderOption.name}`}
                    value={providerName}
                    onChange={(event) => setProviderName(event.target.value)}
                  />
                  <small>Use different names to store multiple API keys for the same provider.</small>
                </label>
                {selectedProviderOption.customEndpoint && (
                  <>
                    <label>API base URL
                      <input
                        required
                        type="url"
                        maxLength={2048}
                        placeholder={selectedProviderOption.endpointPlaceholder}
                        value={baseUrl}
                        onChange={(event) => setBaseUrl(event.target.value)}
                      />
                      <small>Use a public HTTPS URL. OpenAI-compatible URLs usually end in /v1; for Anthropic-compatible services, enter the part before /v1.</small>
                    </label>
                    <label>Model IDs (optional)
                      <textarea
                        rows={3}
                        maxLength={20000}
                        placeholder={"model-small\nmodel-large"}
                        value={customModels}
                        onChange={(event) => setCustomModels(event.target.value)}
                      />
                      <small>Separate models with new lines or commas. Leave blank if the provider supports a model-listing endpoint.</small>
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
                  <button type="button" onClick={closeProviderForm} disabled={busyAction === "create"}>Cancel</button>
                  <button
                    className="install-button"
                    disabled={Boolean(busyAction)
                      || !providerName.trim()
                      || !apiKey.trim()
                      || Boolean(selectedProviderOption.customEndpoint && !baseUrl.trim())}
                  >
                    {busyAction === "create" ? "Saving…" : "Add provider"}
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
