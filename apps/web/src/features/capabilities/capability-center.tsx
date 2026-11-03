"use client";

import { ToolAuthorization } from "./tool-connections";

import { FormEvent, useCallback, useEffect, useState } from "react";
import {
  BookOpen,
  Cable,
  FileCode,
  Plus,
  Upload,
  X,
} from "lucide-react";
import { ConfigurationHistory } from "../settings/configuration-history";
import { api } from "@/lib/api/platform";
import { SkillResourcePreview } from "./skill-resource-preview";
import { MemoryCenter } from "./memory-center";
import { HabitLearning } from "./habit-learning";
import { BrowserSkills } from "./browser-skills";
import { KnowledgeWorkspace, type Knowledge } from "./knowledge-workspace";
import "./capability-center.css";
import "../ask/resource-list.css";

type Skill = {
  id: string;
  name: string;
  description: string;
  version: number;
  enabled: boolean;
};
type Tool = {
  name: string;
  description: string;
  enabled: boolean;
  read_only: boolean;
  schema: object;
};
type MCP = {
  transport?: string;
  command_json?: string[];
  id: string;
  name: string;
  url?: string;
  token_configured?: boolean;
  allow_private?: boolean;
  enabled: boolean;
  status: string;
  tools: Tool[];
};
type Bindings = {
  knowledge_ids: string[];
  skill_ids: string[];
  mcp_ids: string[];
};
export function CapabilityCenter({ isAdmin, permissions, onOpenConversation }: { isAdmin: boolean; permissions?: string[]; onOpenConversation?: (id: string) => void }) {
  const allowed = (permission: string) => isAdmin || permissions === undefined || permissions.includes(permission);
  const canManageKnowledge = allowed("knowledge.manage"), canSearchKnowledge = allowed("knowledge.search");
  const canManageSkills = allowed("skills.manage");
  const canReadBindings = allowed("chat.read"), canBind = canReadBindings && allowed("chat.manage");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState(() => allowed("knowledge.read") ? "knowledge" : allowed("skills.read") ? "skills" : allowed("tools.read") ? "mcp" : "memory");
  const [listQuery, setListQuery] = useState("");
  const [skillView, setSkillView] = useState("library");
  const [browserDirty, setBrowserDirty] = useState(false);
  const leaveBrowser = () => !browserDirty || window.confirm("浏览器录制的修改尚未保存，放弃修改并离开？");
  const [knowledge, setKnowledge] = useState<Knowledge[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);
  const [servers, setServers] = useState<MCP[]>([]);
  const [bindings, setBindings] = useState<Bindings>({
    knowledge_ids: [],
    skill_ids: [],
    mcp_ids: [],
  });
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [showCreate, setShowCreate] = useState(false);
  const [editingMCP, setEditingMCP] = useState<MCP | null | undefined>();
  const [selected, setSelected] = useState<Knowledge | null>(null);
  const [preview, setPreview] = useState<{
    name: string;
    files: Record<string, string>;
  } | null>(null);
  const [filePath, setFilePath] = useState("SKILL.md");

  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const [kb, sk, mcp, binding] = await Promise.all([
        tab === "knowledge" ? api<{ items: Knowledge[] }>("/capabilities/knowledge", { signal }) : null,
        tab === "skills" ? api<{ items: Skill[] }>("/capabilities/skills", { signal }) : null,
        tab === "mcp" ? api<{ items: MCP[] }>("/capabilities/mcp", { signal }) : null,
        canReadBindings ? api<Bindings>("/capabilities/bindings", { signal }) : null,
      ]);
      if (signal?.aborted) return;
      if (kb) setKnowledge(kb.items);
      if (sk) setSkills(sk.items);
      if (mcp) setServers(mcp.items);
      if (binding) setBindings(binding);
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [tab, canReadBindings]);
  useEffect(() => {
    const controller = new AbortController();
    reload(controller.signal).catch((error) => { if (!controller.signal.aborted) setError(error.message); });
    return () => controller.abort();
  }, [reload]);

  async function action(operation: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
      setNotice(message);
      return true;
    } catch (error) {
      setError((error as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function bind(kind: keyof Bindings, id: string, checked: boolean) {
    const selection = {
      ...bindings,
      [kind]: checked
        ? [...bindings[kind], id]
        : bindings[kind].filter((item) => item !== id),
    };
    const previous = bindings;
    setBindings(selection);
    const saved = await action(
      () =>
        api("/capabilities/bindings", {
          method: "PUT",
          body: JSON.stringify(selection),
        }),
      "助手能力绑定已更新，下次发送消息即可使用",
    );
    if (!saved) setBindings(previous);
  }
  async function enableTool(server: MCP, tool: Tool, checked: boolean) {
    if (
      checked &&
      !tool.read_only &&
      !window.confirm(
        "此工具可能修改外部数据。启用后仅允许平台管理员在自主任务中逐次审批执行，继续？",
      )
    )
      return;
    const previous = servers;
    const updated = server.tools.map((item) =>
      item.name === tool.name ? { ...item, enabled: checked } : item,
    );
    setServers(
      servers.map((item) =>
        item.id === server.id ? { ...item, tools: updated } : item,
      ),
    );
    const saved = await action(
      () =>
        api(`/capabilities/mcp/${server.id}/tools`, {
          method: "PUT",
          body: JSON.stringify({
            allow_write_tools: updated.some(
              (item) => item.enabled && !item.read_only,
            ),
            names: updated
              .filter((item) => item.enabled)
              .map((item) => item.name),
          }),
        }),
      "工具启用状态已更新",
    );
    if (!saved) setServers(previous);
  }
  async function uploadSkill(file: File) {
    const body = new FormData();
    body.set("file", file);
    await action(
      () => api("/capabilities/skills", { method: "POST", body }),
      "技能已导入，同名技能会创建新版本",
    );
  }

  const visibleKnowledge = knowledge.filter((item) => `${item.name} ${item.description}`.toLowerCase().includes(listQuery.trim().toLowerCase()));

  return (
    <section className={`capability-center ${tab === "knowledge" ? "knowledge-center-active" : ""}`}>
      <div className="capability-title">
        <div>
          <p>工作空间 / 共享能力</p>
          <h1>能力中心</h1>
          <small>让助手使用你的知识、工具和工作方法。</small>
        </div>
        <span className="capability-scope">当前账号私有知识与技能</span>
      </div>
      <div className="capability-tabs">
        {[
          ["knowledge", "知识库", BookOpen],
          ["mcp", "MCP 工具", Cable],
          ["skills", "Skills 技能", FileCode],
          ["memory", "长期记忆", BookOpen],
        ].filter(([id]) => allowed(({ knowledge: "knowledge.read", skills: "skills.read", mcp: "tools.read", memory: "memory.read" } as Record<string, string>)[id as string])).map(([id, label, Icon]) => {
          const Glyph = Icon as typeof BookOpen;
          return (
            <button
              key={id as string}
              className={tab === id ? "active" : ""}
              onClick={() => {
                if (id === tab || !leaveBrowser()) return;
                setBrowserDirty(false);
                setTab(id as string);
                setLoading(true);
                setListQuery("");
                setSelected(null);
                setError("");
              }}
            >
              <Glyph size={16} />
              {label as string}
            </button>
          );
        })}
      </div>
      {error && (
        <div
          className="platform-error"
          role="alert"
        >
          {error}
          {tab === "knowledge" && <button type="button" onClick={() => { setError(""); setLoading(true); void reload().catch((reason) => setError(reason.message)); }}>重试加载知识库</button>}
        </div>
      )}
      {notice && (
        <div
          className="capability-notice"
          role="status"
        >
          {notice}
        </div>
      )}
      {tab === "memory" && <MemoryCenter />}
      {tab === "skills" && <nav className="browser-skill-views" aria-label="技能视图"><button type="button" aria-pressed={skillView === "library"} onClick={() => { if (!leaveBrowser()) return; setBrowserDirty(false); setSkillView("library"); }}>技能库与习惯学习</button><button type="button" aria-pressed={skillView === "browser"} onClick={() => setSkillView("browser")}>浏览器录制</button></nav>}
      {tab !== "memory" && !(tab === "skills" && skillView === "browser") && !selected && <label className="resource-list-search"><input aria-label="搜索能力资源" placeholder={tab === "knowledge" ? "搜索知识库名称或描述" : "搜索资源名称"} value={listQuery} onChange={(event) => setListQuery(event.target.value)}/><span>{tab === "knowledge" ? `${visibleKnowledge.length} / ${knowledge.length} 个知识库` : `${tab === "mcp" ? servers.length : skills.length}项资源`}</span></label>}
      <div className="capability-actions">
        <p>
          {tab === "knowledge"
            ? "上传资料后可直接测试检索，勾选绑定后供助手调用。"
            : tab === "mcp"
              ? "管理员接入 MCP 服务。只读工具供助手使用；写工具仅供管理员自主任务逐次审批后执行。"
              : tab === "memory"
                ? "记忆由你主动维护，启用后仅供当前账号授权的助手检索。"
                : "导入 SKILL.md 或 ZIP，绑定后按需读取技能和参考资料。"}
        </p>
        {tab === "knowledge" && !selected && canManageKnowledge && (
          <button
            className="settings-primary"
            onClick={() => setShowCreate(true)}
          >
            <Plus size={15} />
            新建知识库
          </button>
        )}
        {tab === "mcp" && isAdmin && (
          <button
            className="settings-primary"
            onClick={() => setEditingMCP(null)}
          >
            <Plus size={15} />
            添加 MCP
          </button>
        )}
        {tab === "skills" && skillView === "library" && canManageSkills && (
          <label className="settings-primary upload-label">
            <Upload size={15} />
            导入技能
            <input
              aria-label="导入技能文件"
              type="file"
              accept=".md,.zip"
              disabled={busy}
              onChange={(event) => {
                if (event.target.files?.[0])
                  void uploadSkill(event.target.files[0]);
                event.target.value = "";
              }}
            />
          </label>
        )}
      </div>
      {tab === "knowledge" && (
        <>
          {selected ? (
            <KnowledgeWorkspace
              key={selected.id}
              knowledge={selected}
              canManage={canManageKnowledge}
              canSearch={canSearchKnowledge}
              canBind={canBind}
              bindingKnown={canReadBindings}
              bound={bindings.knowledge_ids.includes(selected.id)}
              onBind={(checked) => bind("knowledge_ids", selected.id, checked)}
              onBack={() => setSelected(null)}
              onChanged={reload}
            />
          ) : (
            <div className="capability-grid knowledge-library" aria-busy={loading}>
              {loading && <p role="status">正在加载知识库…</p>}
              {!loading && visibleKnowledge.map((item) => (
                <article
                  key={item.id}
                  className="capability-card"
                >
                  <BookOpen size={25} />
                  <h2><button type="button" className="knowledge-library-name" onClick={() => setSelected(item)}>{item.name}</button></h2>
                  <p>{item.description || "暂无描述"}</p>
                  <small>
                    {item.documents} 份文档 ·{" "}
                    {item.embedding_model_id ? "关键词 + 向量" : "关键词检索"}
                  </small>
                  {canReadBindings && <label className="capability-check">
                    <input
                      type="checkbox"
                      checked={bindings.knowledge_ids.includes(item.id)}
                      disabled={busy || !canBind}
                      onChange={(event) =>
                        void bind(
                          "knowledge_ids",
                          item.id,
                          event.target.checked,
                        )
                      }
                    />
                    绑定到通用助手
                  </label>}
                  <div className="capability-card-actions">
                    <button onClick={() => setSelected(item)}>
                      管理文档与检索
                    </button>
                    {canManageKnowledge && <button
                      disabled={busy}
                      onClick={() => {
                        if (
                          window.confirm(`删除知识库“${item.name}”及其文档？`)
                        )
                          void action(
                            () =>
                              api(`/capabilities/knowledge/${item.id}`, {
                                method: "DELETE",
                              }),
                            "知识库已删除",
                          );
                      }}
                    >
                      删除
                    </button>}
                  </div>
                </article>
              ))}
              {!loading && !error && knowledge.length > 0 && !visibleKnowledge.length && <p>没有匹配的知识库，请调整搜索词。</p>}
              {!loading && !error && !knowledge.length && (
                <Empty
                  title="还没有知识库"
                  description="创建知识库，上传你的第一份资料。"
                />
              )}
            </div>
          )}
        </>
      )}
      {tab === "skills" && (
        <>
          {skillView === "browser" ? <BrowserSkills isAdmin={isAdmin} canManage={canManageSkills} onChanged={() => reload()} onDirty={setBrowserDirty} onOpenSkill={id => { void api<{ name: string; files: Record<string, string> }>(`/capabilities/skills/${id}`).then(detail => { setPreview(detail); setFilePath("SKILL.md"); }).catch(reason => setError(reason.message)); }}/> : <>
          <HabitLearning canRead={allowed("skills.read")} canManage={allowed("skills.manage")} canBind={canBind} canReadChat={allowed("chat.read")} resourcesRevision={JSON.stringify([skills.map(item => [item.id, item.version, item.enabled]), bindings.skill_ids])} onChanged={() => reload()} onOpenConversation={onOpenConversation}/>
          <div className="capability-grid">
            {skills.filter((item) => item.name.toLowerCase().includes(listQuery.toLowerCase())).map((item) => (
              <article
                key={item.id}
                className="capability-card"
              >
                <FileCode size={25} />
                <h2>
                  {item.name}
                  <span>v{item.version}</span>
                </h2>
                <p>{item.description}</p>
                <small>{item.enabled ? "已启用" : "已停用"} · 按需加载</small>
                <label className="capability-check">
                  <input
                    type="checkbox"
                    checked={bindings.skill_ids.includes(item.id)}
                    disabled={busy || !item.enabled || !canBind}
                    onChange={(event) =>
                      void bind("skill_ids", item.id, event.target.checked)
                    }
                  />
                  绑定到通用助手
                </label>
                <div className="capability-card-actions">
                  <button
                    onClick={async () => {
                      try {
                        const detail = await api<{
                          name: string;
                          files: Record<string, string>;
                        }>(`/capabilities/skills/${item.id}`);
                        setPreview(detail);
                        setFilePath("SKILL.md");
                      } catch (error) {
                        setError((error as Error).message);
                      }
                    }}
                  >
                    查看资源
                  </button>
                  {canManageSkills && <button
                    disabled={busy}
                    onClick={() =>
                      void action(
                        () =>
                          api(
                            `/capabilities/skills/${item.id}?enabled=${!item.enabled}`,
                            { method: "PATCH" },
                          ),
                        "技能状态已更新",
                      )
                    }
                  >
                    {item.enabled ? "停用" : "启用"}
                  </button>}
                  {canManageSkills && <button
                    disabled={busy}
                    onClick={() => {
                      if (window.confirm(`删除技能“${item.name}”？`))
                        void action(
                          () =>
                            api(`/capabilities/skills/${item.id}`, {
                              method: "DELETE",
                            }),
                          "技能已删除",
                        );
                    }}
                  >
                    删除
                  </button>}
                </div>
              </article>
            ))}
            {!skills.length && (
              <Empty
                title="还没有技能"
                description="技能文件需包含 name 和 description 的 YAML 头部。"
              />
            )}
          </div>
          <p className="capability-help">
            技能说明和文本资源可供助手读取。浏览器录制技能只能在单独授权并审批的任务中重放。
          </p>
          </>}
        </>
      )}
      {tab === "mcp" && (
        <div className="capability-grid">
          {servers.filter((server) => server.name.toLowerCase().includes(listQuery.toLowerCase())).map((server) => (
            <article
              key={server.id}
              className="capability-card"
            >
              <Cable size={25} />
              <h2>{server.name}</h2>
              <p>{server.url || "管理员管理的共享工具服务"}</p>
              <small>
                {server.enabled ? "已启用" : "已停用"} ·{" "}
                {server.status === "available"
                  ? "连接正常"
                  : server.status === "unavailable"
                    ? "连接失败"
                    : "未检测"}
              </small>
              <label className="capability-check">
                <input
                  type="checkbox"
                  checked={bindings.mcp_ids.includes(server.id)}
                  disabled={busy || !server.enabled}
                  onChange={(event) =>
                    void bind("mcp_ids", server.id, event.target.checked)
                  }
                />
                绑定到通用助手
              </label>
              <p>{server.transport || "streamable_http"}{["stdio", "acp"].includes(server.transport || "") ? " · 仅Work隔离环境，逐次审批；Ask不可执行" : ""}</p>
              {!["stdio", "acp"].includes(server.transport || "") && <ToolAuthorization id={server.id} isAdmin={isAdmin} />}
              <div className="mcp-tool-list">
                {server.tools.map((tool) => (
                  <details key={tool.name}>
                    <summary>
                      <span>{tool.name}</span>
                      <small>{tool.read_only ? "只读" : "需审批"}</small>
                    </summary>
                    <p>{tool.description}</p>
                    <pre>{JSON.stringify(tool.schema, null, 2)}</pre>
                    {isAdmin && (
                      <label className="capability-check">
                        <input
                          type="checkbox"
                          checked={tool.enabled}
                          disabled={busy}
                          onChange={(event) =>
                            void enableTool(server, tool, event.target.checked)
                          }
                        />
                        启用此工具
                      </label>
                    )}
                  </details>
                ))}
              </div>
              {isAdmin && (
                <div className="capability-card-actions">
                  <button
                    disabled={busy}
                    onClick={() =>
                      void action(
                        () =>
                          api(`/capabilities/mcp/${server.id}/discover`, {
                            method: "POST",
                          }),
                        "连接成功，工具列表已更新，请选择启用只读工具",
                      )
                    }
                  >
                    测试并发现工具
                  </button>
                  <button onClick={() => setEditingMCP(server)}>编辑</button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      if (window.confirm(`删除 MCP“${server.name}”？`))
                        void action(
                          () =>
                            api(`/capabilities/mcp/${server.id}`, {
                              method: "DELETE",
                            }),
                          "MCP 已删除",
                        );
                    }}
                  >
                    删除
                  </button>
                </div>
              )}
              {isAdmin && <ConfigurationHistory kind="mcp" resourceId={server.id} name={server.name}/>}
            </article>
          ))}
          {!servers.length && (
            <Empty
              title="还没有 MCP 服务"
              description={
                isAdmin
                  ? "添加 Streamable HTTP 服务后测试连接并发现工具。"
                  : "请联系管理员添加工具服务。"
              }
            />
          )}
        </div>
      )}
      {showCreate && (
        <KnowledgeDialog
          onClose={() => setShowCreate(false)}
          onSaved={async () => {
            setShowCreate(false);
            await reload();
          }}
        />
      )}
      {editingMCP !== undefined && (
        <MCPDialog
          server={editingMCP}
          onClose={() => setEditingMCP(undefined)}
          onSaved={async () => {
            setEditingMCP(undefined);
            await reload();
          }}
        />
      )}
      {preview && (
        <div className="capability-overlay">
          <section
            role="dialog"
            aria-modal="true"
            aria-label="技能资源"
            className="capability-dialog skill-preview"
          >
            <header>
              <h2>{preview.name}</h2>
              <button
                aria-label="关闭技能资源"
                onClick={() => setPreview(null)}
              >
                <X size={18} />
              </button>
            </header>
            <select
              aria-label="选择技能资源"
              value={filePath}
              onChange={(event) => setFilePath(event.target.value)}
            >
              {Object.keys(preview.files).map((path) => (
                <option key={path}>{path}</option>
              ))}
            </select>
            <SkillResourcePreview path={filePath} text={preview.files[filePath] || ""}/>
          </section>
        </div>
      )}
    </section>
  );
}

function Empty({ title, description }: { title: string; description: string }) {
  return (
    <div className="capability-empty">
      <BookOpen size={30} />
      <h2>{title}</h2>
      <p>{description}</p>
    </div>
  );
}

function KnowledgeDialog({
  onClose,
  onSaved,
}: {
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [embedding, setEmbedding] = useState("");
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ items: { id: string; name: string }[] }>(
      "/capabilities/embedding-models",
    )
      .then((data) => setModels(data.items))
      .catch((error) => setError(error.message));
  }, []);
  return (
    <Dialog
      title="新建知识库"
      onClose={onClose}
    >
      <form
        onSubmit={async (event) => {
          event.preventDefault();
          setBusy(true);
          try {
            await api("/capabilities/knowledge", {
              method: "POST",
              body: JSON.stringify({
                name,
                description,
                embedding_model_id: embedding || null,
              }),
            });
            await onSaved();
          } catch (error) {
            setError((error as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <label>
          知识库名称
          <input
            required
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>
          描述
          <textarea
            maxLength={1000}
            value={description}
            onChange={(event) => setDescription(event.target.value)}
          />
        </label>
        <label>
          Embedding 模型
          <select
            value={embedding}
            onChange={(event) => setEmbedding(event.target.value)}
          >
            <option value="">暂不配置，使用关键词检索</option>
            {models.map((model) => (
              <option
                key={model.id}
                value={model.id}
              >
                {model.name}
              </option>
            ))}
          </select>
        </label>
        <p className="capability-help">
          可在模型管理添加 Embedding 类型模型，启用向量与关键词混合检索。
        </p>
        {error && (
          <div
            className="platform-error"
            role="alert"
          >
            {error}
          </div>
        )}
        <button
          className="settings-primary"
          disabled={busy}
        >
          {busy ? "创建中…" : "创建知识库"}
        </button>
      </form>
    </Dialog>
  );
}

function MCPDialog({
  server,
  onClose,
  onSaved,
}: {
  server: MCP | null;
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [name, setName] = useState(server?.name || "");
  const [url, setURL] = useState(server?.url || "");
  const [token, setToken] = useState("");
  const [clear, setClear] = useState(false);
  const [privateAccess, setPrivateAccess] = useState(
    server?.allow_private ?? false,
  );
  const [enabled, setEnabled] = useState(server?.enabled ?? true);
  const [transport, setTransport] = useState(server?.transport || "streamable_http");
  const [command, setCommand] = useState(JSON.stringify(server?.command_json || []));
  const isolated = ["stdio", "acp"].includes(transport);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await api(
        server ? `/capabilities/mcp/${server.id}` : "/capabilities/mcp",
        {
          method: server ? "PUT" : "POST",
          body: JSON.stringify({
            name,
            transport,
            command: isolated ? JSON.parse(command) : [],
            url: isolated ? "" : url,
            token: isolated ? "" : token,
            clear_token: clear,
            allow_private: privateAccess,
            enabled,
          }),
        },
      );
      await onSaved();
    } catch (error) {
      setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog
      title={server ? "编辑 MCP 服务" : "添加 MCP 服务"}
      onClose={onClose}
    >
      <form onSubmit={submit}>
        <label>
          服务名称
          <input
            required
            maxLength={120}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>
        <label>传输协议<select value={transport} onChange={(e) => setTransport(e.target.value)}><option value="streamable_http">Streamable HTTP</option><option value="sse">旧版 HTTP+SSE</option><option value="stdio">隔离 stdio（仅Work审批）</option><option value="acp">ACP v1（仅Work审批）</option></select></label>
        {isolated && <label>隔离镜像命令参数（JSON数组）<textarea required value={command} onChange={(e) => setCommand(e.target.value)} placeholder={'["/usr/local/bin/python", "/workspace/server.py"]'} /><small>不在宿主执行，不自动下载安装包。脚本须预先在任务工作区准备；每次执行需审批。</small></label>}
        {!isolated && <label>
          Streamable HTTP 地址
          <input
            type="url"
            required
            value={url}
            onChange={(event) => setURL(event.target.value)}
            placeholder="https://服务地址/mcp"
          />
        </label>}
        <label hidden={isolated}>
          Bearer Token
          <input
            type="password"
            autoComplete="new-password"
            disabled={clear}
            value={token}
            placeholder={
              server?.token_configured
                ? "已保存，留空保持不变"
                : "无鉴权服务可留空"
            }
            onChange={(event) => setToken(event.target.value)}
          />
        </label>
        {server?.token_configured && (
          <label className="capability-check">
            <input
              type="checkbox"
              checked={clear}
              onChange={(event) => {
                setClear(event.target.checked);
                setToken("");
              }}
            />
            清除已保存的 Token
          </label>
        )}
        <label className="capability-check">
          <input
            type="checkbox"
            checked={privateAccess}
            onChange={(event) => setPrivateAccess(event.target.checked)}
          />
          允许访问内网（仅限你信任的本地 MCP 服务）
        </label>
        <label className="capability-check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
          />
          启用服务
        </label>
        <p className="capability-help">
          HTTP/SSE修改后需重新发现并审核工具；stdio/ACP绑定自主任务，在隔离环境批准发现和调用，不在宿主执行。
        </p>
        {error && (
          <div
            className="platform-error"
            role="alert"
          >
            {error}
          </div>
        )}
        <button
          className="settings-primary"
          disabled={busy}
        >
          {busy ? "保存中…" : "保存 MCP"}
        </button>
      </form>
    </Dialog>
  );
}

function Dialog({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="capability-overlay">
      <section
        className="capability-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <header>
          <h2>{title}</h2>
          <button
            aria-label="关闭能力配置"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </header>
        {children}
      </section>
    </div>
  );
}
