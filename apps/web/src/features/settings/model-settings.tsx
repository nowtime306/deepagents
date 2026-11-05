"use client";

import { FormEvent, useEffect, useState } from "react";
import {
  CheckCircle2,
  CircleHelp,
  LoaderCircle,
  Plus,
  Server,
  ShieldCheck,
  X,
} from "lucide-react";
import { ConfigurationHistory } from "./configuration-history";
import { EmbeddingCompatibility } from "./embedding-compatibility";
import { api } from "@/lib/api/platform";
import "./model-settings.css";

type ModelRecord = {
  id: string;
  name: string;
  model: string;
  base_url: string;
  api_key_configured: boolean;
  enabled: boolean;
  is_default: boolean;
  max_output_tokens: number | null;
  temperature: number | null;
  supports_images: boolean | null;
  supports_tools: boolean | null;
  revision: number;
  connection_status: string;
  kind: string;
  provider: string;
  protocol: string;
  probe_hint: string | null;
  compatibility?: { state: string; entry: string; verification: string };
};

type FormState = {
  name: string;
  model: string;
  base_url: string;
  api_key: string;
  enabled: boolean;
  is_default: boolean;
  clear_api_key: boolean;
  max_output_tokens: string;
  temperature: string;
  supports_images: string;
  supports_tools: string;
  kind: string;
  provider: string;
  protocol: string;
};

type Catalog = {
  kinds: { id: string; name: string; hint: string }[];
  providers: { id: string; name: string; base_url: string; protocol: string }[];
  protocols: { id: string; name: string; hint: string }[];
};

const connectionLabels: Record<string, string> = {
  not_tested: "未检测",
  available: "连接正常",
  unavailable: "连接失败",
};

export function ModelSettings({
  isAdmin,
  onChanged,
}: {
  isAdmin: boolean;
  onChanged: () => Promise<void>;
}) {
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [kindFilter, setKindFilter] = useState("");
  const [providerFilter, setProviderFilter] = useState("");
  const [search, setSearch] = useState("");
  const [editing, setEditing] = useState<ModelRecord | null | undefined>(
    undefined,
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  async function reload() {
    const response = await api<{ items: ModelRecord[] }>("/admin/models");
    setModels(response.items);
  }

  useEffect(() => {
    if (!isAdmin) {
      setLoading(false);
      return;
    }
    let active = true;
    Promise.all([
      api<{ items: ModelRecord[] }>("/admin/models"),
      api<Catalog>("/admin/models/catalog"),
    ])
      .then(([data, options]) => {
        if (active) {
          setModels(data.items);
          setCatalog(options);
        }
      })
      .catch((error) => {
        if (active) setError(error.message);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [isAdmin]);

  async function action(
    identifier: string,
    operation: () => Promise<unknown>,
    message: string,
  ) {
    setBusy(identifier);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
      await onChanged();
      setNotice(message);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function update(model: ModelRecord, changes: Partial<ModelRecord>) {
    const body = {
      name: model.name,
      model: model.model,
      base_url: model.base_url,
      max_output_tokens: model.max_output_tokens,
      temperature: model.temperature,
      enabled: model.enabled,
      is_default: model.is_default,
      revision: model.revision,
      kind: model.kind,
      provider: model.provider,
      protocol: model.protocol,
      supports_images: model.supports_images,
      supports_tools: model.supports_tools,
      ...changes,
    };
    await action(
      model.id,
      () =>
        api(`/admin/models/${model.id}`, {
          method: "PUT",
          body: JSON.stringify(body),
        }),
      "模型配置已更新，立即生效",
    );
  }

  async function test(model: ModelRecord) {
    setBusy(model.id);
    setError("");
    setNotice("");
    try {
      const result = await api<{ success: boolean; message: string }>(
        `/admin/models/${model.id}/test`,
        { method: "POST" },
      );
      await reload();
      if (result.success) setNotice(result.message);
      else setError(result.message);
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(null);
    }
  }

  const visible = models.filter(
    (model) =>
      (!kindFilter || model.kind === kindFilter) &&
      (!providerFilter || model.provider === providerFilter) &&
      `${model.name} ${model.model}`
        .toLowerCase()
        .includes(search.trim().toLowerCase()),
  );
  const label = (
    items: { id: string; name: string }[] | undefined,
    id: string,
  ) => items?.find((item) => item.id === id)?.name || id;
  if (!isAdmin)
    return (
      <section className="model-settings">
        <h1>模型管理</h1>
        <p>仅管理员可以配置模型。你可以在智能对话中选择已启用的模型。</p>
      </section>
    );
  return (
    <section className="model-settings">
      <div className="settings-heading">
        <div>
          <div className="settings-kicker">设置 / 模型与服务</div>
          <h1>模型管理</h1>
          <p>按服务商和用途管理模型，密钥统一加密保存。</p>
        </div>
        <button
          className="settings-primary"
          disabled={!catalog}
          onClick={() => setEditing(null)}
        >
          <Plus size={16} /> 添加模型
        </button>
      </div>
      <div className="settings-security">
        <ShieldCheck size={17} />
        <span>密钥加密保存，不在页面回显。模型配置保存后立即生效。</span>
      </div>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {notice && (
        <div
          role="status"
          className="settings-notice"
        >
          <CheckCircle2 size={16} />
          {notice}
        </div>
      )}
      <div className="model-summary">
        <span>
          已接入模型 <b>{models.length}</b>
        </span>
        <span>{models.filter((model) => model.enabled).length} 个已启用</span>
        <span className="protocol-tag">10 类模型用途</span>
      </div>
      <div className="model-filters">
        <input
          aria-label="搜索模型"
          placeholder="搜索显示名称或模型 ID"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          aria-label="筛选模型类型"
          value={kindFilter}
          onChange={(event) => setKindFilter(event.target.value)}
        >
          <option value="">全部模型类型</option>
          {catalog?.kinds.map((item) => (
            <option
              key={item.id}
              value={item.id}
            >
              {item.name}
            </option>
          ))}
        </select>
        <select
          aria-label="筛选服务商"
          value={providerFilter}
          onChange={(event) => setProviderFilter(event.target.value)}
        >
          <option value="">全部服务商</option>
          {catalog?.providers.map((item) => (
            <option
              key={item.id}
              value={item.id}
            >
              {item.name}
            </option>
          ))}
        </select>
        <small>
          显示 {visible.length} / {models.length} 个
        </small>
      </div>
      {loading ? (
        <div className="model-empty">
          <LoaderCircle size={24} />
          <p>正在读取模型配置…</p>
        </div>
      ) : !models.length ? (
        <div className="model-empty">
          <div className="model-empty-icon">
            <Server size={29} />
          </div>
          <h2>添加你的第一个模型</h2>
          <p>填写服务地址、模型名称和密钥，即可开始使用。</p>
          <button
            className="settings-primary"
            disabled={!catalog}
            onClick={() => setEditing(null)}
          >
            <Plus size={15} /> 添加模型
          </button>
        </div>
      ) : (
        <div className="model-grid">
          {visible.map((model) => (
            <article
              className="model-card"
              key={model.id}
            >
              <div className="model-card-header">
                <span className="provider-icon">
                  <Server size={21} />
                </span>
                <div>
                  <h2>{model.name}</h2>
                  <small>{model.model}</small>
                </div>
                {model.is_default && (
                  <span className="default-badge">默认</span>
                )}
              </div>
              <div className="model-endpoint">{model.base_url}</div>
              <small>
                {label(catalog?.kinds, model.kind)} ·{" "}
                {label(catalog?.providers, model.provider)}
              </small>
              <small className="model-protocol">
                {label(catalog?.protocols, model.protocol)}
              </small>
              <div className="model-meta">
                <span className={`connection-state ${model.connection_status}`}>
                  <span />
                  {connectionLabels[model.connection_status] || "未检测"}
                </span>
                <span>
                  {model.api_key_configured ? "密钥已配置" : "未设置密钥"}
                </span>
                <span>{model.enabled ? "已启用" : "已停用"}</span>
              </div>
              {model.probe_hint && (
                <p className="model-availability">{model.probe_hint}</p>
              )}
              {model.compatibility && <p className="model-availability">
                适配状态：{({ executable: "可执行", business_sample: "需业务样例检测", configuration_only: "仅保存配置", unsupported: "未支持" } as Record<string,string>)[model.compatibility.state] || "未核验"}
                {" · "}{model.compatibility.entry}<br />{model.compatibility.verification}
              </p>}
              {model.kind === "chat" && (
                <div className="model-params">
                  <span>输出上限：{model.max_output_tokens ?? "对话默认 4096"}</span>
                  <span>温度：{model.temperature ?? "服务默认"}</span>
                </div>
              )}
              <div className="model-card-actions">
                <button
                  disabled={!!busy}
                  onClick={() => setEditing(model)}
                >
                  编辑
                </button>
                <button
                  disabled={!!busy || !!model.probe_hint}
                  onClick={() => void test(model)}
                  title={model.probe_hint || "发送一次简短请求检测连接"}
                >
                  {model.probe_hint
                    ? "检测待接入"
                    : busy === model.id
                      ? "处理中…"
                      : "测试连接"}
                </button>
                <button
                  disabled={!!busy}
                  onClick={() =>
                    void update(model, {
                      enabled: !model.enabled,
                      is_default: model.enabled ? false : model.is_default,
                    })
                  }
                >
                  {model.enabled ? "停用" : "启用"}
                </button>
                <button
                  disabled={
                    !!busy ||
                    model.is_default ||
                    model.kind !== "chat" ||
                    model.protocol === "custom"
                  }
                  onClick={() =>
                    void update(model, { enabled: true, is_default: true })
                  }
                >
                  设为默认
                </button>
                <button
                  className="model-delete"
                  disabled={!!busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        `确定删除模型“${model.name}”吗？历史对话会保留。`,
                      )
                    )
                      void action(
                        model.id,
                        () =>
                          api(`/admin/models/${model.id}`, {
                            method: "DELETE",
                          }),
                        "模型已删除，历史对话已保留",
                      );
                  }}
                >
                  删除
                </button>
              </div>
              <ConfigurationHistory kind="model" resourceId={model.id} name={model.name}/>
              {(model.kind === "embedding" || model.kind === "multimodal_embedding") && <EmbeddingCompatibility modelId={model.id} model={model.model} revision={model.revision} baseUrl={model.base_url}/>}
            </article>
          ))}
          {!!models.length && !visible.length && (
            <div className="model-empty">
              <h2>没有匹配的模型</h2>
              <p>请调整名称、类型或服务商筛选条件。</p>
            </div>
          )}
        </div>
      )}
      <p className="settings-footnote">
        <CircleHelp size={14} />{" "}
        大语言、文本向量和重排模型按各自接口检测；其他用途请按模型卡片提示在对应业务中验证，未适配的协议暂不支持调用。配置保存不代表连通性已验证。
      </p>
      {editing !== undefined && catalog && (
        <ModelDialog
          key={editing?.id || "new"}
          model={editing}
          catalog={catalog}
          onClose={() => setEditing(undefined)}
          onSaved={async () => {
            setKindFilter("");
            setProviderFilter("");
            setSearch("");
            setEditing(undefined);
            await reload();
            await onChanged();
            setError("");
            setNotice("模型配置已保存，使用范围见模型类型说明");
          }}
        />
      )}
    </section>
  );
}

function ModelDialog({
  model,
  catalog,
  onClose,
  onSaved,
}: {
  model: ModelRecord | null;
  catalog: Catalog;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [form, setForm] = useState<FormState>({
    name: model?.name || "",
    model: model?.model || "",
    base_url: model?.base_url || "",
    api_key: "",
    enabled: model?.enabled ?? true,
    is_default: model?.is_default ?? false,
    clear_api_key: false,
    kind: model?.kind || "chat",
    provider: model?.provider || "custom",
    protocol: model?.protocol || "openai",
    max_output_tokens: model?.max_output_tokens?.toString() || "",
    temperature: model?.temperature?.toString() || "",
    supports_images:
      model?.supports_images == null ? "auto" : String(model.supports_images),
    supports_tools: model?.supports_tools == null ? "auto" : String(model.supports_tools),
  });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof FormState, value: string | boolean) =>
    setForm((current) => ({ ...current, [key]: value }));
  const kind = catalog.kinds.find((item) => item.id === form.kind);
  const protocol = catalog.protocols.find((item) => item.id === form.protocol);

  useEffect(() => {
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !saving) onClose();
    }
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [saving, onClose]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      await api(model ? `/admin/models/${model.id}` : "/admin/models", {
        method: model ? "PUT" : "POST",
        body: JSON.stringify({
          ...form,
          revision: model?.revision,
          supports_tools: form.supports_tools === "auto" ? null : form.supports_tools === "true",
          supports_images:
            form.supports_images === "auto"
              ? null
              : form.supports_images === "true",
          max_output_tokens:
            form.max_output_tokens === ""
              ? null
              : Number(form.max_output_tokens),
          temperature:
            form.temperature === "" ? null : Number(form.temperature),
        }),
      });
      await onSaved();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="model-dialog-overlay">
      <div
        className="model-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="model-dialog-title"
      >
        <div className="model-dialog-heading">
          <div>
            <h2 id="model-dialog-title">{model ? "编辑模型" : "添加模型"}</h2>
            <p>选择服务商与模型用途，填写对应接口地址。</p>
          </div>
          <button
            aria-label="关闭模型配置"
            disabled={saving}
            onClick={onClose}
          >
            <X size={19} />
          </button>
        </div>
        <form onSubmit={submit}>
          <fieldset
            disabled={saving}
            className="model-form-fields"
          >
            <div className="model-form-grid">
              <label>
                服务商
                <select
                  aria-label="服务商"
                  value={form.provider}
                  onChange={(event) => {
                    const provider = catalog.providers.find(
                      (item) => item.id === event.target.value,
                    )!;
                    setForm((current) => ({
                      ...current,
                      provider: provider.id,
                      protocol: provider.protocol,
                      base_url:
                        provider.id === "custom"
                          ? current.base_url
                          : provider.base_url,
                      is_default:
                        provider.protocol !== "custom" && current.is_default,
                    }));
                  }}
                >
                  {catalog.providers.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.name}
                    </option>
                  ))}
                </select>
                <small>
                  选择服务商会填入常用地址；地域、部署和计费套餐不同，请按实际服务调整。
                </small>
              </label>
              <label>
                模型类型
                <select
                  aria-label="模型类型"
                  value={form.kind}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      kind: event.target.value as FormState["kind"],
                      is_default:
                        event.target.value === "chat" && current.is_default,
                    }))
                  }
                >
                  {catalog.kinds.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.name}
                    </option>
                  ))}
                </select>
                <small>{kind?.hint}</small>
              </label>
            </div>
            <label>
              接口协议
              <select
                aria-label="接口协议"
                value={form.protocol}
                onChange={(event) =>
                  setForm((current) => ({
                    ...current,
                    protocol: event.target.value,
                    base_url:
                      event.target.value === "dashscope_multimodal" &&
                      current.provider === "dashscope" &&
                      current.base_url ===
                        "https://dashscope.aliyuncs.com/compatible-mode/v1"
                        ? "https://dashscope.aliyuncs.com/api/v1"
                        : current.base_url,
                    is_default:
                      ["openai", "anthropic"].includes(event.target.value) &&
                      current.is_default,
                  }))
                }
              >
                {catalog.protocols.map((item) => (
                  <option
                    key={item.id}
                    value={item.id}
                  >
                    {item.name}
                  </option>
                ))}
              </select>
              <small>{protocol?.hint}。协议须与服务地址匹配。</small>
            </label>
            <div className="model-form-grid">
              <label>
                显示名称
                <input
                  autoFocus
                  required
                  maxLength={120}
                  placeholder="例如：我的通用助手"
                  value={form.name}
                  onChange={(event) => set("name", event.target.value)}
                />
              </label>
              <label>
                模型名称
                <input
                  required
                  maxLength={200}
                  placeholder="服务商提供的实际模型 ID"
                  value={form.model}
                  onChange={(event) => set("model", event.target.value)}
                />
              </label>
            </div>
            <label>
              服务地址
              <input
                type="url"
                required
                placeholder="https://你的服务地址/v1"
                value={form.base_url}
                onChange={(event) => set("base_url", event.target.value)}
              />
              <small>
                {form.protocol === "anthropic"
                  ? "填写基础地址，如 https://api.anthropic.com；不包含 /v1/messages。"
                  : form.protocol === "openai"
                    ? "填写 API 基础地址，不包含 /chat/completions 或 /embeddings。"
                    : "填写实际部署或服务商文档提供的基础地址。"}
              </small>
            </label>
            <label>
              API 密钥
              <input
                type="password"
                autoComplete="new-password"
                maxLength={8192}
                disabled={form.clear_api_key}
                placeholder={
                  model?.api_key_configured
                    ? "已保存密钥，留空保持不变"
                    : "本地无鉴权服务可留空"
                }
                value={form.api_key}
                onChange={(event) => set("api_key", event.target.value)}
              />
            </label>
            {model?.api_key_configured && (
              <label className="settings-checkbox">
                <input
                  type="checkbox"
                  checked={form.clear_api_key}
                  onChange={(event) => {
                    set("clear_api_key", event.target.checked);
                    set("api_key", "");
                  }}
                />
                清除已保存的密钥
              </label>
            )}
            {["chat", "vision"].includes(form.kind) && (
              <label>
                图片输入能力
                <select
                  aria-label="图片输入能力"
                  value={form.supports_images}
                  onChange={(event) =>
                    set("supports_images", event.target.value)
                  }
                >
                  <option value="auto">自动识别（已核验模型）</option>
                  <option value="true">支持图片输入</option>
                  <option value="false">仅文本输入</option>
                </select>
                <small>
                  仅在服务和模型实际支持图片协议时开启；不会自动把文本模型升级为视觉模型。
                </small>
              </label>
            )}
            {["chat", "vision"].includes(form.kind) && <label>工具调用能力<select aria-label="工具调用能力"
              value={form.supports_tools} onChange={e => set("supports_tools",e.target.value)}>
              <option value="auto">未声明</option><option value="true">支持工具调用</option><option value="false">不支持</option>
            </select><small>路由用于智能体时必须明确支持工具调用，请按服务商实际能力填写。</small></label>}
            {form.kind === "chat" && (
              <details className="model-advanced">
                <summary>调用参数（可选）</summary>
                <div className="model-form-grid">
                  <label>
                    最大输出 Token
                    <input
                      type="number"
                      min="1"
                      max="1048576"
                      step="1"
                      placeholder="对话默认 4096"
                      value={form.max_output_tokens}
                      onChange={(event) =>
                        set("max_output_tokens", event.target.value)
                      }
                    />
                  </label>
                  <label>
                    温度
                    <input
                      type="number"
                      min="0"
                      max="2"
                      step="0.1"
                      placeholder="使用服务默认值"
                      value={form.temperature}
                      onChange={(event) =>
                        set("temperature", event.target.value)
                      }
                    />
                  </label>
                </div>
                <small>参数支持范围取决于模型；不确定时请留空。</small>
              </details>
            )}
            <div className="model-toggles">
              <label className="settings-checkbox">
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(event) =>
                    setForm((current) => ({
                      ...current,
                      enabled: event.target.checked,
                      is_default: event.target.checked
                        ? current.is_default
                        : false,
                    }))
                  }
                />
                启用模型
              </label>
              <label className="settings-checkbox">
                <input
                  type="checkbox"
                  disabled={
                    !form.enabled ||
                    form.kind !== "chat" ||
                    !["openai", "anthropic"].includes(form.protocol)
                  }
                  checked={form.is_default}
                  onChange={(event) => set("is_default", event.target.checked)}
                />
                设为默认模型
              </label>
            </div>
          </fieldset>
          {error && (
            <div
              className="platform-error"
              role="alert"
            >
              {error}
            </div>
          )}
          <div className="model-dialog-footer">
            <span>密钥仅在服务端使用</span>
            <button
              type="button"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button
              className="settings-primary"
              disabled={saving}
            >
              {saving ? "正在保存…" : "保存模型"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
