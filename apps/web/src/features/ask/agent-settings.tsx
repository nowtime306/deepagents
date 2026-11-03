"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "./resource-dialog";
import { AgentVersions } from "./agent-versions";
import { AgentTeamEditor, type TeamMember } from "./agent-team";

export type Agent = {
  id: string;
  name: string;
  instructions: string;
  model_id: string;
  model_routing?: boolean;
  enabled: boolean;
  memory_enabled: boolean;
  web_enabled: boolean;
  web_mode: "tavily" | "openai_native";
  knowledge_ids: string[];
  skill_ids: string[];
  mcp_ids: string[];
  workflow_version_ids: string[];
  revision: number;
  team?: TeamMember[];
};
type Model = { id: string; name: string };
type Resource = { id: string; name: string; enabled?: boolean };
type Bindings = {
  knowledge_ids: string[];
  skill_ids: string[];
  mcp_ids: string[];
  workflow_version_ids: string[];
};
const emptyBindings = (): Bindings => ({
  knowledge_ids: [],
  skill_ids: [],
  mcp_ids: [],
  workflow_version_ids: [],
});

export function AgentSettings({
  agents,
  models,
  reload,
  isAdmin,
  canManage = true,
}: {
  agents: Agent[];
  models: Model[];
  reload: () => Promise<void>;
  isAdmin: boolean;
  canManage?: boolean;
}) {
  const [showEditor, setShowEditor] = useState(false);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Agent | null>(null);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [model, setModel] = useState(models[0]?.id || "");
  const [enabled, setEnabled] = useState(true);
  const [memoryEnabled, setMemoryEnabled] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const [modelRouting, setModelRouting] = useState(false);
  const [webMode, setWebMode] = useState<"tavily" | "openai_native">("tavily");
  const [bindings, setBindings] = useState<Bindings>(emptyBindings);
  const [resourcesReady, setResourcesReady] = useState(false);
  const [resources, setResources] = useState<
    Record<keyof Bindings, Resource[]>
  >({
    knowledge_ids: [],
    skill_ids: [],
    mcp_ids: [],
    workflow_version_ids: [],
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [team, setTeam] = useState<TeamMember[]>([]);
  useEffect(() => {
    Promise.all([
      api<{ items: Resource[] }>("/capabilities/knowledge"),
      api<{ items: Resource[] }>("/capabilities/skills"),
      api<{ items: Resource[] }>("/capabilities/mcp"),
    ])
      .then(async ([knowledge, skills, mcp]) => {
        const workflows = await api<{ items: { id: string }[] }>("/workflows");
        const published = await Promise.all(
          workflows.items.map((item) =>
            api<{
              items: {
                id: string;
                name: string;
                version: number;
                active: boolean;
              }[];
            }>(`/workflows/${item.id}/versions`),
          ),
        );
        const workflowVersions = published
          .flatMap((result) => result.items)
          .filter((version) => version.active)
          .map((version) => ({
            id: version.id,
            name: `${version.name} · v${version.version}`,
          }));
        setResources({
          knowledge_ids: knowledge.items,
          workflow_version_ids: workflowVersions,
          skill_ids: skills.items.filter((item) => item.enabled),
          mcp_ids: mcp.items.filter((item) => item.enabled),
        });
        setResourcesReady(true);
      })
      .catch((reason) => setError(reason.message));
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(editing ? `/agents/${editing.id}` : "/agents", {
        method: editing ? "PUT" : "POST",
        body: JSON.stringify({
          name,
          instructions,
          model_id: model,
          model_routing: modelRouting,
          enabled,
          memory_enabled: memoryEnabled,
          web_enabled: webEnabled,
          web_mode: webMode,
          ...bindings,
          revision: editing?.revision,
          team,
        }),
      });
      await reload();
      setShowEditor(false);
      setEditing(null);
      setName("");
      setInstructions("");
      setEnabled(true);
      setWebEnabled(false);
      setModelRouting(false);
      setMemoryEnabled(false);
      setWebMode("tavily");
      setBindings(emptyBindings());
      setTeam([]);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="run-history">
      <p>工作空间 / 智能体管理</p>
      <header className="resource-list-heading"><div><h1>智能体管理</h1><p>配置模型、指令和资源，发布后用于对话与自动任务。</p></div><button className="settings-primary" disabled={!canManage} onClick={() => {
        setEditing(null); setName(""); setInstructions(""); setModel(models[0]?.id || ""); setEnabled(true);
        setWebEnabled(false); setMemoryEnabled(false); setModelRouting(false); setWebMode("tavily");
        setBindings(emptyBindings()); setTeam([]); setError(""); setShowEditor(true);
      }}>新建智能体</button></header>
      <label className="resource-list-search"><input aria-label="搜索智能体" placeholder="搜索智能体名称" value={query} onChange={(event) => setQuery(event.target.value)}/><span>{agents.length}个智能体</span></label>
      {isAdmin && <details><summary>联网搜索服务设置</summary><SearchSettings /></details>}
      {error && !showEditor && (
        <div
          className="platform-error"
          role="alert"
        >
          {error}
        </div>
      )}
      {showEditor && <ResourceDialog title={editing ? "编辑智能体" : "创建智能体"} busy={busy} close={() => setShowEditor(false)}>
      {error && <p className="platform-error" role="alert">{error}</p>}
      <form
        className="agent-form"
        onSubmit={save}
      >
        <h2>{editing ? "编辑智能体" : "创建智能体"}</h2>
        <label>
          名称
          <input
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          聊天模型
          <select
            aria-label="聊天模型"
            required
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="">请选择模型</option>
            {models.map((m) => (
              <option
                key={m.id}
                value={m.id}
              >
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          系统指令
          <textarea
            maxLength={12000}
            value={instructions}
            onChange={(e) => setInstructions(e.target.value)}
            placeholder="例如：你是一名代码审查助手，回答时先说明问题和验证依据。"
          />
        </label>
        {(
          [
            ["knowledge_ids", "知识库"],
            ["mcp_ids", "MCP 工具"],
            ["skill_ids", "Skills 技能"],
            ["workflow_version_ids", "Ask / Work 可调用的发布工作流"],
          ] as const
        ).map(([key, label]) => (
          <fieldset
            key={key}
            className="agent-bindings"
          >
            <legend>{label}</legend>
            {!resources[key].length && (
              <small>暂无可用资源，可在能力中心配置。</small>
            )}
            {bindings[key]
              .filter((id) => !resources[key].some((item) => item.id === id))
              .map((id) => (
                <label
                  key={id}
                  className="agent-binding"
                >
                  <input
                    type="checkbox"
                    checked
                    onChange={() =>
                      setBindings((current) => ({
                        ...current,
                        [key]: current[key].filter((item) => item !== id),
                      }))
                    }
                  />{" "}
                  资源已停用或删除，取消勾选以移除
                </label>
              ))}
            {resources[key].map((item) => (
              <label
                key={item.id}
                className="agent-binding"
              >
                <input
                  type="checkbox"
                  checked={bindings[key].includes(item.id)}
                  onChange={(event) =>
                    setBindings((current) => ({
                      ...current,
                      [key]: event.target.checked
                        ? [...current[key], item.id]
                        : current[key].filter((id) => id !== item.id),
                    }))
                  }
                />{" "}
                {item.name}
              </label>
            ))}
          </fieldset>
        ))}
        <label className="agent-checkbox">
          <input
            type="checkbox"
            checked={memoryEnabled}
            onChange={(e) => setMemoryEnabled(e.target.checked)}
          />
          允许读取我的长期记忆
          <small>
            默认关闭。开启后，此智能体可按关键词检索能力中心中主动保存的记忆。
          </small>
        </label>
        <label className="agent-checkbox">
          <input
            type="checkbox"
            checked={webEnabled}
            aria-label="启用联网搜索"
            onChange={(e) => setWebEnabled(e.target.checked)}
          />{" "}
          启用联网搜索
          <small>联网会将相关内容发送给所选服务商，调用可能产生费用。</small>
        </label>
        <label className="agent-checkbox"><input type="checkbox" checked={modelRouting}
          onChange={e => setModelRouting(e.target.checked)}/>使用模型路由
          <small>请先在设置中确认当前候选接收方，发布时固定路由修订。</small></label>
        {webEnabled && (
          <label>
            联网方式
            <select
              aria-label="联网方式"
              value={webMode}
              onChange={(e) =>
                setWebMode(e.target.value as "tavily" | "openai_native")
              }
            >
              <option value="tavily">Tavily 平台搜索</option>
              <option value="openai_native">OpenAI 原生联网</option>
            </select>
            <small>
              {webMode === "tavily"
                ? "需要管理员配置 Tavily，并使用支持工具调用的模型。"
                : "需要 OpenAI 服务商的模型支持 Responses API 和联网工具；服务端搜索开始后无法立即撤销。"}
            </small>
          </label>
        )}
        <AgentTeamEditor value={team} onChange={setTeam} agentId={editing?.id} />
        <label className="agent-checkbox">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />{" "}
          启用智能体
        </label>
        <button
          className="settings-primary"
          disabled={busy || !models.length || !resourcesReady}
        >
          {busy ? "保存中…" : "保存智能体"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={() => {
              setShowEditor(false);
              setEditing(null);
              setName("");
              setInstructions("");
              setEnabled(true);
              setWebEnabled(false);
              setModelRouting(false);
              setMemoryEnabled(false);
              setWebMode("tavily");
              setBindings(emptyBindings());
              setTeam([]);
            }}
          >
            取消编辑
          </button>
        )}
      </form></ResourceDialog>}
      {!agents.length && <p className="automatic-empty">还没有智能体，点击右上角“新建智能体”开始配置。</p>}
      {agents.length > 0 && !agents.some((agent) => agent.name.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的智能体。</p>}
      {agents.filter((agent) => agent.name.toLowerCase().includes(query.toLowerCase())).map((agent) => (
        <article
          className="run-history-card"
          key={agent.id}
        >
          <div>
            <strong>{agent.name}</strong>
            <p>
              {agent.enabled ? "已启用" : "已停用"} ·{" "}
              {models.find((m) => m.id === agent.model_id)?.name ||
                "模型不可用"}
            </p>
          </div>
          <AgentVersions
            agent={agent.id}
            revision={agent.revision}
            enabled={agent.enabled}
          />
          <button
            disabled={!canManage}
            onClick={() => {
              setShowEditor(true);
              setError("");
              setEditing(agent);
              setName(agent.name);
              setInstructions(agent.instructions);
              setModel(agent.model_id);
              setEnabled(agent.enabled);
              setTeam(agent.team || []);
              setWebEnabled(agent.web_enabled);
              setModelRouting(agent.model_routing ?? false);
              setMemoryEnabled(agent.memory_enabled);
              setWebMode(agent.web_mode || "tavily");
              setBindings({
                knowledge_ids: agent.knowledge_ids,
                skill_ids: agent.skill_ids,
                mcp_ids: agent.mcp_ids,
                workflow_version_ids: agent.workflow_version_ids || [],
              });
            }}
          >
            编辑
          </button>
          <button
            disabled={busy || !canManage}
            onClick={async () => {
              if (
                !window.confirm(
                  `删除智能体“${agent.name}”？已有对话的智能体只能停用。`,
                )
              )
                return;
              setBusy(true);
              setError("");
              try {
                await api(`/agents/${agent.id}`, { method: "DELETE" });
                await reload();
              } catch (reason) {
                setError((reason as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            删除
          </button>
        </article>
      ))}
    </section>
  );
}

function SearchSettings() {
  const [settings, setSettings] = useState({
    enabled: false,
    key_configured: false,
  });
  const [key, setKey] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [clear, setClear] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    api<{ enabled: boolean; key_configured: boolean }>("/search-settings")
      .then((data) => {
        setSettings(data);
        setEnabled(data.enabled);
      })
      .catch((error) => setMessage(error.message));
  }, []);
  async function save(event: FormEvent) {
    event.preventDefault();
    setMessage("");
    try {
      await api("/search-settings", {
        method: "PUT",
        body: JSON.stringify({
          api_key: key || null,
          enabled,
          clear_api_key: clear,
        }),
      });
      const data = await api<{ enabled: boolean; key_configured: boolean }>(
        "/search-settings",
      );
      setSettings(data);
      setEnabled(data.enabled);
      setKey("");
      setClear(false);
      setMessage("联网搜索配置已保存");
    } catch (error) {
      setMessage((error as Error).message);
    }
  }
  return (
    <form
      className="agent-form"
      onSubmit={save}
    >
      <h2>联网搜索服务</h2>
      <p>
        管理员可配置 Tavily 搜索，密钥加密保存；使用联网的智能体会将搜索词发送给
        Tavily。
      </p>
      <label>
        Tavily API 密钥
        <input
          type="password"
          autoComplete="new-password"
          maxLength={8192}
          value={key}
          disabled={clear}
          onChange={(event) => setKey(event.target.value)}
          placeholder={
            settings.key_configured
              ? "已保存密钥，留空保持不变"
              : "输入 Tavily 密钥"
          }
        />
      </label>
      {settings.key_configured && (
        <label>
          <input
            type="checkbox"
            checked={clear}
            onChange={(event) => {
              setClear(event.target.checked);
              setKey("");
            }}
          />{" "}
          清除密钥
        </label>
      )}
      <label>
        <input
          type="checkbox"
          checked={enabled}
          onChange={(event) => setEnabled(event.target.checked)}
        />{" "}
        启用联网搜索服务
      </label>
      {message && <p role="status">{message}</p>}
      <button className="settings-primary">保存联网配置</button>
    </form>
  );
}
