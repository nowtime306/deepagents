"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "./resource-dialog";

type Key = {
  id: string;
  name: string;
  token_hint: string;
  expires_at: string;
  revoked_at: string | null;
  rpm: number;
  concurrency: number;
  workflow_name: string;
  version: number;
};
type Version = {
  id: string;
  name: string;
  version: number;
  active: boolean;
  digest: string;
};
type Log = {
  id: number;
  key_name: string;
  run_id: string;
  status: string;
  outcome: string;
  duration_ms: number | null;
  created_at: string;
};
const labels: Record<string, string> = {
  PENDING: "待执行",
  RUNNING: "运行中",
  SUCCEEDED: "成功",
  FAILED: "失败",
  NEEDS_REVIEW: "需要核查",
  CANCELLED: "已取消",
};

export function WorkflowAccess() {
  const [open, setOpen] = useState(false), [query, setQuery] = useState("");
  const [keys, setKeys] = useState<Key[]>([]);
  const [versions, setVersions] = useState<Version[]>([]);
  const [logs, setLogs] = useState<Log[]>([]);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [days, setDays] = useState(30);
  const [rpm, setRpm] = useState(10);
  const [concurrency, setConcurrency] = useState(1);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function reload() {
    const [credential, audit, workflows] = await Promise.all([
      api<{ items: Key[] }>("/api-keys"),
      api<{ items: Log[] }>("/api-call-logs"),
      api<{ items: { id: string }[] }>("/workflows"),
    ]);
    const published = await Promise.all(
      workflows.items.map((item) =>
        api<{ items: Version[] }>(`/workflows/${item.id}/versions`),
      ),
    );
    setKeys(credential.items);
    setLogs(audit.items);
    setVersions(
      published
        .flatMap((result) => result.items)
        .filter((item) => item.active && item.digest),
    );
  }
  useEffect(() => {
    reload().catch((reason) => setError(reason.message));
  }, []);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await operation();
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    setSecret("");
    void action(async () => {
      const result = await api<{ key: string }>("/api-keys", {
        method: "POST",
        body: JSON.stringify({
          name,
          version_id: version,
          valid_days: days,
          rpm,
          concurrency,
        }),
      });
      setSecret(result.key);
      setName(""); setOpen(false);
    });
  }
  return (
    <section aria-label="工作流 API 发布管理">
      <header className="resource-list-heading"><h2>API 发布与调用记录</h2><button className="settings-primary" onClick={() => { setName(""); setError(""); setOpen(true); }}>新增 API Key</button></header>
      <label className="resource-list-search"><input aria-label="搜索 API Key" placeholder="搜索调用方或工作流" value={query} onChange={(e) => setQuery(e.target.value)}/><span>{keys.length}个凭证</span></label>
      <p>
        每个 Key
        仅授权一个发布版本。该版本可能使用你绑定的知识、记忆和工具；请只把 Key
        交给可信的调用方。
      </p>
      <p>
        当前服务仅监听本机地址。请求排队后返回运行编号，服务重启后继续处理待执行请求；执行中断会标记“需要核查”，不会自动重复调用。
      </p>
      {error && !open && (
        <p
          className="platform-error"
          role="alert"
        >
          {error}
        </p>
      )}
      {secret && (
        <div
          className="capability-notice"
          role="status"
        >
          <strong>Key 仅展示这一次，请立即保存。</strong>
          <label>
            新生成的 API Key
            <input
              readOnly
              value={secret}
              autoComplete="off"
            />
          </label>
          <button onClick={() => setSecret("")}>我已保存，隐藏 Key</button>
        </div>
      )}
      {open && <ResourceDialog title="新增 API Key" busy={busy} close={() => setOpen(false)}>{error && <p role="alert" className="platform-error">{error}</p>}
      <form
        className="agent-form"
        onSubmit={create}
      >
        <label>
          调用方名称
          <input
            required
            value={name}
            maxLength={100}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <label>
          授权发布版本
          <select
            aria-label="授权发布版本"
            required
            value={version}
            onChange={(e) => setVersion(e.target.value)}
          >
            <option value="">请选择已测试并上线的版本</option>
            {versions.map((item) => (
              <option
                key={item.id}
                value={item.id}
              >
                {item.name} · v{item.version}
              </option>
            ))}
          </select>
        </label>
        <label>
          Key 有效天数
          <input
            required
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </label>
        <label>
          每分钟调用上限
          <input
            required
            type="number"
            min={1}
            max={120}
            value={rpm}
            onChange={(e) => setRpm(Number(e.target.value))}
          />
        </label>
        <label>
          排队与运行并发上限
          <input
            required
            type="number"
            min={1}
            max={4}
            value={concurrency}
            onChange={(e) => setConcurrency(Number(e.target.value))}
          />
        </label>
        <button disabled={busy || !versions.length}>生成调用 Key</button>
      </form></ResourceDialog>}
      <div className="capability-grid">
        {keys.length > 0 && !keys.some((item) => `${item.name} ${item.workflow_name}`.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的记录。</p>}
      {keys.filter((item) => `${item.name} ${item.workflow_name}`.toLowerCase().includes(query.toLowerCase())).map((item) => (
          <article
            className="capability-card"
            key={item.id}
          >
            <h3>{item.name}</h3>
            <p>
              {item.workflow_name} · v{item.version} · 尾号 {item.token_hint}
            </p>
            <p>
              {item.rpm} 次/分钟 · 并发 {item.concurrency}
            </p>
            <p>
              {item.revoked_at
                ? "已撤销"
                : new Date(item.expires_at) <= new Date()
                  ? "已到期"
                  : "有效"}{" "}
              · 到期 {new Date(item.expires_at).toLocaleString("zh-CN")}
            </p>
            <button
              disabled={busy || !!item.revoked_at}
              onClick={() => {
                if (window.confirm(`撤销“${item.name}”的访问权限？`))
                  void action(() =>
                    api(`/api-keys/${item.id}`, { method: "DELETE" }),
                  );
              }}
            >
              撤销 Key
            </button>
          </article>
        ))}
      </div>
      <details>
        <summary>查看调用方式</summary>
        <p>
          POST /api/public/v1/workflow-runs，携带 Authorization: Bearer
          你的Key、Idempotency-Key: 每次业务请求的唯一标识，JSON 内容为{" "}
          {JSON.stringify({ text: "输入内容" })}。
        </p>
        <p>
          返回 202 和 id；GET /api/public/v1/workflow-runs/运行id
          查询状态及结果。同一幂等标识和输入可安全重试；输入变化必须换标识。此
          Key 无权访问内部会话或管理接口。
        </p>
      </details>
      <h3>最近调用</h3>
      <button
        disabled={busy}
        onClick={() => void action(async () => {})}
      >
        刷新调用记录
      </button>
      {!logs.length && <p>暂无调用记录。</p>}
      {logs.map((log) => (
        <article
          className="run-history-card"
          key={log.id}
        >
          <strong>{log.key_name}</strong>
          <span>{labels[log.status] || log.status}</span>
          <small>
            {log.run_id} · {new Date(log.created_at).toLocaleString("zh-CN")} ·{" "}
            {log.duration_ms === null
              ? "尚未结束"
              : `${Math.round(log.duration_ms)} 毫秒`}
          </small>
        </article>
      ))}
    </section>
  );
}
