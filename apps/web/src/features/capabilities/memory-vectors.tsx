"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";
import { TokenUsage, type Usage } from "@/features/ask/token-usage";
import type { ScopeOptions } from "./memory-scope-values";

type Settings = {
  embedding_model_id: string | null;
  model_revision: number | null;
  enabled: boolean;
  revision: number;
};
type Operation = {
  id: string;
  action: string;
  model_id: string;
  model_revision: number;
  status: string;
  error_code: string | null;
  created_at: string;
  usage?: Usage;
};
type Recall = {
  items: {
    id: string;
    title: string;
    content: string;
    vector_score?: number | null;
  }[];
  mode: string;
  warning: string | null;
};
const statuses: Record<string, string> = {
  RUNNING: "处理中",
  SUCCEEDED: "已完成",
  FAILED: "处理失败",
  UNKNOWN: "结果未知，未自动重试",
};

export function MemoryVectors({
  canManage,
  options,
}: {
  canManage: boolean;
  options: ScopeOptions;
}) {
  const [open, setOpen] = useState(false);
  const [saved, setSaved] = useState<Settings | null>(null);
  const [models, setModels] = useState<
    { id: string; name: string; revision: number }[]
  >([]);
  const [model, setModel] = useState("");
  const [enabled, setEnabled] = useState(false);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [query, setQuery] = useState("");
  const [scope, setScope] = useState("global");
  const [scopeId, setScopeId] = useState("");
  const [result, setResult] = useState<Recall | null>(null);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useCallback(
    () =>
      Promise.all([
        api<Settings>("/memories/settings"),
        api<{ items: { id: string; name: string; revision: number }[] }>(
          "/memories/embedding-options",
        ),
      ]),
    [],
  );
  const history = useCallback(
    async () =>
      setOperations(
        (await api<{ items: Operation[] }>("/memories/resource-operations"))
          .items,
      ),
    [],
  );
  const accept = useCallback(
    ([settings, available]: Awaited<ReturnType<typeof request>>) => {
      setSaved(settings);
      setModel(settings.embedding_model_id || "");
      setEnabled(settings.enabled);
      setModels(available.items);
    },
    [],
  );
  useEffect(() => {
    let live = true;
    void request()
      .then((value) => {
        if (live) accept(value);
      })
      .catch((e: Error) => setError(e.message));
    const refresh = () =>
      void history().catch((e: Error) => setError(e.message));
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [request, history, accept]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await operation();
      await history();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const chosen = models.find((item) => item.id === model);
  const dirty =
    !!saved &&
    (model !== (saved.embedding_model_id || "") || enabled !== saved.enabled);
  function save(e: FormEvent) {
    e.preventDefault();
    if (!saved) return;
    void act(async () => {
      await api("/memories/settings", {
        method: "PUT",
        body: JSON.stringify({
          embedding_model_id: model || null,
          model_revision: chosen?.revision || null,
          enabled,
          revision: saved.revision,
        }),
      });
      accept(await request()); setOpen(false);
      setMessage(
        "已保存。新配置不会自动发送或索引记忆；旧向量须匹配当前模型修订才会使用。",
      );
    });
  }
  return (
    <section
      className="memory-governance"
      aria-label="语义记忆设置"
    >
      <header className="resource-list-heading"><h2>语义召回</h2><button disabled={!canManage || !saved} onClick={() => { setError(""); setOpen(true); }}>配置语义召回</button></header>
      <p>{saved?.enabled ? "已启用" : "未启用"} · {models.find((item) => item.id === saved?.embedding_model_id)?.name || "未选择向量模型"}</p>
      <p>
        可选向量模型补充关键词匹配。仅向已确认的模型配置发送授权范围内的记忆文本；索引和每次语义查询均产生调用，计入用量。
      </p>
      {error && !open && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {open && <ResourceDialog title="配置语义召回" busy={busy} close={() => { setOpen(false); setModel(saved?.embedding_model_id || ""); setEnabled(saved?.enabled || false); }}>{error && <p role="alert">{error}</p>}<form
        className="memory-extraction-form"
        onSubmit={save}
      >
        <label>
          记忆向量模型
          <select
            aria-label="记忆向量模型"
            disabled={!canManage || busy}
            value={model}
            onChange={(e) => setModel(e.target.value)}
          >
            <option value="">不使用向量模型</option>
            {models.map((item) => (
              <option
                key={item.id}
                value={item.id}
              >
                {item.name} · 修订 {item.revision}
              </option>
            ))}
          </select>
        </label>
        <label className="memory-message-choice">
          <input
            type="checkbox"
            disabled={!canManage || busy}
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          允许使用所选模型进行语义召回
        </label>
        <p>
          已保存的模型修订：{saved?.model_revision ?? "未配置"}
          。服务地址或配置变化后会停止语义调用，需刷新、核对并保存新配置。
        </p>
        {canManage && (
          <footer>
            <button
              disabled={busy || !saved || (enabled && !chosen)}
              className="settings-primary"
            >
              确认并保存语义配置
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void act(async () => accept(await request()))}
            >
              刷新配置
            </button>
          </footer>
        )}
      </form></ResourceDialog>}
      {canManage && (
        <>
          <button
            disabled={
              busy ||
              !saved?.enabled ||
              dirty ||
              chosen?.revision !== saved?.model_revision
            }
            onClick={() => {
              const unknown = operations.some(
                (operation) => operation.status === "UNKNOWN",
              );
              const allow =
                !unknown ||
                window.confirm(
                  "曾有索引结果未知。请核对消耗；重新索引可能重复计费。确认继续？",
                );
              if (allow)
                void act(async () => {
                  const result = await api<{
                    indexed: number;
                    skipped_changed?: number;
                    message?: string;
                  }>("/memories/index", {
                    method: "POST",
                    body: JSON.stringify({ acknowledge_duplicate: unknown }),
                  });
                  setMessage(
                    result.message ||
                      `已索引 ${result.indexed} 条${result.skipped_changed ? `；${result.skipped_changed} 条在处理时已变化，已跳过` : ""}。`,
                  );
                });
            }}
          >
            索引下一批记忆（最多16条）
          </button>
          {dirty && <p>请先保存语义配置，再索引或预览召回。</p>}
          <form
            className="memory-extraction-form"
            onSubmit={(e) => {
              e.preventDefault();
              void act(async () =>
                setResult(
                  await api<Recall>("/memories/recall", {
                    method: "POST",
                    body: JSON.stringify({
                      query,
                      project_id: scope === "project" ? scopeId : null,
                      agent_id: scope === "agent" ? scopeId : null,
                    }),
                  }),
                ),
              );
            }}
          >
            <label>
              召回预览问题
              <input
                required
                maxLength={500}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            <label>
              召回上下文
              <select
                aria-label="召回上下文"
                value={scope}
                onChange={(e) => {
                  setScope(e.target.value);
                  setScopeId("");
                }}
              >
                <option value="global">仅本人全局</option>
                <option value="project">指定项目及全局</option>
                <option value="agent">指定智能体及全局</option>
              </select>
            </label>
            {scope !== "global" && (
              <label>
                上下文对象
                <select
                  aria-label="上下文对象"
                  value={scopeId}
                  required
                  onChange={(e) => setScopeId(e.target.value)}
                >
                  <option value="">请选择</option>
                  {(scope === "project"
                    ? options.projects
                    : options.agents
                  ).map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            )}
            <button disabled={busy || dirty}>预览记忆召回</button>
          </form>
        </>
      )}
      {result && (
        <div aria-label="记忆召回结果">
          <p>
            本次模式：{result.mode === "hybrid" ? "语义与关键词融合" : "关键词"}
            {result.warning ? ` · ${result.warning}` : ""}
          </p>
          {result.items.length === 0 && <p>没有找到符合范围的有效记忆。</p>}
          {result.items.map((item) => (
            <article
              className="memory-recall-item"
              key={item.id}
            >
              <h3>{item.title}</h3>
              <p className="memory-preserve">{item.content}</p>
              {item.vector_score != null && (
                <small>
                  向量相似度 {item.vector_score.toFixed(3)}（不是答案置信度）
                </small>
              )}
            </article>
          ))}
        </div>
      )}
      {operations.length > 0 && (
        <details className="memory-job-history">
          <summary>向量处理记录与消耗（{operations.length}）</summary>
          {operations.map((operation) => (
            <article key={operation.id}>
              <h3>
                {operation.action === "index" ? "记忆索引" : "召回预览"} ·{" "}
                {statuses[operation.status] || operation.status}
              </h3>
              <p>
                {new Date(operation.created_at).toLocaleString("zh-CN")} ·
                模型修订 {operation.model_revision}
              </p>
              {operation.error_code && <p>{operation.error_code}</p>}
              <TokenUsage
                usage={operation.usage}
                label="资源处理"
              />
              {!operation.usage && (
                <p>
                  {operation.status === "RUNNING"
                    ? "等待用量记录。"
                    : "此操作未发生模型调用。"}
                </p>
              )}
            </article>
          ))}
        </details>
      )}
    </section>
  );
}
