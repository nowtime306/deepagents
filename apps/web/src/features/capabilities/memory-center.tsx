"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";
import { MemoryCandidates } from "./memory-candidates";
import { MemoryVectors } from "./memory-vectors";
import { MemoryScopeEditor } from "./memory-scope";
import {
  emptyScope,
  localExpiry,
  scopePayload,
  type ScopeOptions,
} from "./memory-scope-values";

type Memory = {
  id: string;
  title: string;
  content: string;
  enabled: boolean;
  revision: number;
  scope_kind?: "global" | "project" | "agent";
  scope_id?: string | null;
  expires_at?: string | null;
  origin?: string;
  candidate_id?: string | null;
};
type Bindings = {
  knowledge_ids: string[];
  skill_ids: string[];
  mcp_ids: string[];
  memory_enabled: boolean;
};

export function MemoryCenter() {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Memory[]>([]);
  const [offset, setOffset] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Memory | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [enabled, setEnabled] = useState(true);
  const [binding, setBinding] = useState<Bindings | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [scope, setScope] = useState(emptyScope);
  const [options, setOptions] = useState<ScopeOptions>({
    projects: [],
    agents: [],
  });
  const [canManage, setCanManage] = useState(false);
  const [canReadChat, setCanReadChat] = useState(false);
  const [canRunChat, setCanRunChat] = useState(false);
  const [canBind, setCanBind] = useState(false);
  async function reload(next = 0) {
    const result = await api<{ items: Memory[]; next_offset: number | null }>(
      `/memories?query=${encodeURIComponent(query)}&offset=${next}`,
    );
    setItems((previous) =>
      next ? [...previous, ...result.items] : result.items,
    );
    setOffset(result.next_offset);
  }
  useEffect(() => {
    api<{ items: Memory[]; next_offset: number | null }>(
      "/memories?query=&offset=0",
    )
      .then((result) => {
        setItems(result.items);
        setOffset(result.next_offset);
      })
      .catch((reason) => setError(reason.message));
    api<{ is_admin?: boolean; permissions?: string[] }>("/auth/me")
      .then((account) => {
        const allowed = (permission: string) =>
          !!account.is_admin ||
          account.permissions === undefined ||
          account.permissions.includes(permission);
        setCanManage(allowed("memory.manage"));
        setCanReadChat(allowed("chat.read"));
        setCanRunChat(allowed("chat.run"));
        setCanBind(allowed("chat.manage"));
        if (allowed("chat.read"))
          void api<Bindings>("/capabilities/bindings")
            .then(setBinding)
            .catch((reason) => setError(reason.message));
      })
      .catch((reason) => setError(reason.message));
    api<ScopeOptions>("/memories/scope-options")
      .then(setOptions)
      .catch((reason) => setError(reason.message));
  }, []);
  async function action(operation: () => Promise<unknown>, refresh = true) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      if (refresh) await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function reset() {
    setOpen(false);
    setEditing(null);
    setTitle("");
    setContent("");
    setEnabled(true);
    setScope(emptyScope);
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      const result = await api<{ duplicate?: boolean }>(
        editing ? `/memories/${editing.id}` : "/memories",
        {
          method: editing ? "PUT" : "POST",
          body: JSON.stringify({
            title,
            content,
            enabled,
            revision: editing?.revision,
            ...scopePayload(scope),
          }),
        },
      );
      if (result.duplicate)
        setNotice(
          "已有相同内容的记忆，本次没有重复保存。请查看原记忆；原有名称、启用状态和期限保持不变。",
        );
      reset();
    });
  }
  return (
    <section aria-label="长期记忆管理">
      <p>
        只保存持续有用的背景或偏好。可从你授权的会话生成候选，确认后才生效；记忆不作为知识库证据或执行指令。
      </p>
      <header className="resource-list-heading"><h2>长期记忆</h2>{canManage && <button className="settings-primary" onClick={() => { reset(); setError(""); setOpen(true); }}>新建记忆</button>}</header>
      {error && !open && (
        <p
          className="platform-error"
          role="alert"
        >
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      <label>
        <input
          type="checkbox"
          checked={binding?.memory_enabled || false}
          disabled={busy || !binding || !canBind}
          onChange={(event) => {
            const checked = event.target.checked;
            const previous = binding;
            setBinding((current) =>
              current ? { ...current, memory_enabled: checked } : current,
            );
            void action(async () => {
              try {
                const current = await api<Bindings>("/capabilities/bindings");
                await api("/capabilities/bindings", {
                  method: "PUT",
                  body: JSON.stringify({ ...current, memory_enabled: checked }),
                });
                setBinding({ ...current, memory_enabled: checked });
              } catch (reason) {
                setBinding(previous);
                throw reason;
              }
            });
          }}
        />
        允许通用助手读取我的长期记忆
      </label>
      <p>
        独立智能体的记忆权限在“智能体管理”中单独开启，关闭后已有运行的新检索也会停止。
      </p>
      {canManage && open && (
        <ResourceDialog title={editing ? "编辑记忆" : "新建记忆"} busy={busy} close={reset}>{error && <p role="alert">{error}</p>}<form
          className="agent-form"
          onSubmit={save}
        >
          <h2>{editing ? "编辑记忆" : "新建记忆"}</h2>
          <label>
            记忆标题
            <input
              required
              maxLength={120}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
            />
          </label>
          <label>
            记忆内容
            <textarea
              aria-label="记忆内容"
              required
              maxLength={4000}
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
          </label>
          <label>
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            参与记忆检索
          </label>
          <MemoryScopeEditor
            value={scope}
            options={options}
            onChange={setScope}
          />
          <button
            className="settings-primary"
            disabled={busy}
          >
            保存记忆
          </button>
          {editing && (
            <button
              type="button"
              onClick={reset}
            >
              取消编辑
            </button>
          )}
        </form></ResourceDialog>
      )}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          void action(() => reload());
        }}
      >
        <label>
          搜索记忆
          <input
            value={query}
            maxLength={500}
            onChange={(e) => setQuery(e.target.value)}
          />
        </label>
        <button disabled={busy}>查询记忆</button>
      </form>
      <div className="capability-grid">
        {items.map((item) => (
          <article
            className="capability-card"
            key={item.id}
          >
            <h2>{item.title}</h2>
            <p style={{ whiteSpace: "pre-wrap" }}>{item.content}</p>
            <small>
              {item.enabled ? "参与检索" : "已停用"} · 修订 {item.revision}
              {item.scope_kind === "project"
                ? " · 项目范围"
                : item.scope_kind === "agent"
                  ? " · 智能体范围"
                  : " · 本人全局"}
              {item.expires_at
                ? ` · ${new Date(item.expires_at) <= new Date() ? "已到期" : "到期"}：${new Date(item.expires_at).toLocaleString("zh-CN")}`
                : ""}
              {item.origin === "candidate"
                ? item.candidate_id
                  ? " · 来自已确认候选"
                  : " · 已确认候选（来源已删除）"
                : " · 手动保存"}
            </small>
            {canManage && (
              <div>
                <button
                  disabled={busy}
                  onClick={() => {
                    setOpen(true); setError("");
                    setEditing(item);
                    setTitle(item.title);
                    setContent(item.content);
                    setEnabled(item.enabled);
                    setScope({
                      kind: item.scope_kind || "global",
                      id: item.scope_id || "",
                      expires: localExpiry(item.expires_at),
                    });
                  }}
                >
                  编辑记忆
                </button>
                <button
                  disabled={busy}
                  onClick={() => {
                    if (
                      window.confirm(
                        `删除记忆“${item.title}”？删除后无法恢复。`,
                      )
                    )
                      void action(() =>
                        api(`/memories/${item.id}`, { method: "DELETE" }),
                      );
                  }}
                >
                  删除记忆
                </button>
              </div>
            )}
          </article>
        ))}
      </div>
      {!items.length && <p>暂无记忆，可以先保存一条工作偏好。</p>}
      {offset !== null && (
        <button
          disabled={busy}
          onClick={() => void action(() => reload(offset), false)}
        >
          加载更多记忆
        </button>
      )}
      <MemoryCandidates
        canManage={canManage}
        canGenerate={canManage && canReadChat && canRunChat}
        canReadChat={canReadChat}
        options={options}
        onAccepted={() => reload()}
      />
      <MemoryVectors
        canManage={canManage}
        options={options}
      />
    </section>
  );
}
