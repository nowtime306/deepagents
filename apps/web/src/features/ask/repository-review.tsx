"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./repositories.css";

type Change = { path: string; kind: string; mode: string; sha: string | null; diff: string; size: number;
  artifact_id: string | null };
type Command = { name: string; args: { command?: string }; status: string; exit_code: number; output: string };
type Repository = { slug: string; sha: string; ref: string; branch: string; status: string;
  snapshot_id: string | null; changes: Change[]; commands: Command[] };
type Action = { id: string; kind: string; status: string; digest: string; expires_at: string;
  payload: Repository & { title?: string; body?: string }; result: { url?: string } };
type Evidence = { source: string; issue?: { number: number; title: string; body: string };
  checks?: { name: string; status: string; conclusion: string | null }[]; checks_truncated?: boolean };
type Data = { repository: Repository | null; actions: Action[]; evidence?: { id: string; created_at: string; data: Evidence }[] };
const states: Record<string, string> = { PREPARED: "待审阅", STARTED: "正在发送", SUCCEEDED: "已完成",
  UNKNOWN: "结果未知，需核查", REJECTED: "已拒绝", FAILED: "已停止" };

export function RepositoryReview({ run, status, canPublish }: { run: string; status: string; canPublish: boolean }) {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [checkedAt, setCheckedAt] = useState(0);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [issue, setIssue] = useState("");
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  async function reload() { setData(await api<Data>(`/work-runs/${run}/repository`)); setCheckedAt(Date.now()); }
  useEffect(() => {
    let active = true;
    api<Data>(`/work-runs/${run}/repository`).then((value) => { if (active) { setData(value); setCheckedAt(Date.now()); } })
      .catch((reason) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [run, status]);
  useEffect(() => {
    const timer = setInterval(() => setCheckedAt(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  async function act(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); } catch (reason) { setError((reason as Error).message); }
    finally { try { await reload(); } catch (reason) { setError((reason as Error).message); } setBusy(false); }
  }
  if (!data?.repository && !error) return null;
  const repository = data?.repository;
  return <section className="repository-panel" aria-label="代码差异审阅">
    <h3>代码与发布</h3>
    {error && <p role="alert" className="platform-error">{error}</p>}
    {repository && <>
      <p>{repository.slug} · {repository.ref}</p>
      <details><summary>来源与任务分支</summary><p>来源提交：<code>{repository.sha}</code></p>
        <p>任务分支：<code>{repository.branch}</code></p><p>本地初始提交为源码快照，不包含完整上游历史。</p></details>
      <h4>保存的代码变化（{repository.changes.length}）</h4>
      {!repository.snapshot_id && <p>首次保存后显示代码变化。</p>}
      {repository.snapshot_id && !repository.changes.length && <p>没有源码变化。</p>}
      {repository.changes.map((change) => <details key={change.path} className="repository-diff">
        <summary>{({ added: "新增", deleted: "删除", modified: "修改" } as Record<string, string>)[change.kind]} · {change.path}</summary>
        <small>{change.size}字节 · 文件模式 {change.mode} · {change.sha || "已删除"}</small><pre>{change.diff || "仅文件模式变化"}</pre>
        {change.artifact_id && <a href={`/api/v1/work-artifacts/${change.artifact_id}/download`} download>下载当前文件</a>}
      </details>)}
      <details><summary>已批准命令与运行证据（{repository.commands.length}）</summary>
        <p>命令返回成功仅代表该命令完成，请核对测试命令、输出与覆盖范围。</p>
        {repository.commands.map((c, i) => <article key={i}><code>{c.args.command || c.name}</code>
          <p>{c.status === "SUCCEEDED" ? "成功" : "未成功"} · 退出码 {c.exit_code ?? "未知"}</p><pre>{c.output}</pre></article>)}
      </details>
      {canPublish && repository.status === "COMPLETED" && repository.changes.length > 0 && <div className="repository-form">
        <p>发布先生成审阅单，之后仍需单独批准。仅创建本次任务分支，不覆盖基准分支。</p>
        <button disabled={busy || data?.actions.some((a) => a.kind === "push" && a.status !== "REJECTED" && a.status !== "FAILED")}
          onClick={() => act(() => api(`/work-runs/${run}/repository/prepare`, { method: "POST", body: JSON.stringify({ kind: "push" }) }))}>
          准备发布审阅</button>
        {data?.actions.some((a) => a.kind === "push" && a.status === "SUCCEEDED") && <>
          <label>PR标题<input aria-label="PR标题" value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} /></label>
          <label>PR说明<textarea aria-label="PR说明" value={body} maxLength={16000} onChange={(e) => setBody(e.target.value)} /></label>
          <button disabled={busy || !title.trim() || data?.actions.some((a) => a.kind === "pr" && !["REJECTED", "FAILED"].includes(a.status))}
            onClick={() => act(() => api(`/work-runs/${run}/repository/prepare`, {
              method: "POST", body: JSON.stringify({ kind: "pr", title, body }) }))}>准备草稿PR审阅</button>
        </>}
      </div>}
      {data?.actions.map((action) => <article key={action.id} className="repository-card repository-action">
        <strong>{action.kind === "push" ? "发布任务分支" : "创建草稿PR"} · {states[action.status] || action.status}</strong>
        {action.result.url && <a href={action.result.url} target="_blank" rel="noreferrer">查看远端结果</a>}
        <details><summary>查看本次批准内容</summary><p>摘要：<code>{action.digest}</code></p>
          <p>基准：{action.payload.sha}；分支：{action.payload.branch}</p><p>失效时间：{new Date(action.expires_at).toLocaleString("zh-CN")}</p>
          {action.payload.title && <><p>{action.payload.title}</p><pre>{action.payload.body}</pre></>}
          {action.payload.changes.map((c) => <details key={c.path}><summary>{c.path} · {c.kind === "deleted" ? "删除" : "写入"}</summary>
            <code>{c.sha || "删除"}</code><pre>{c.diff}</pre></details>)}
        </details>
        {canPublish && action.status === "PREPARED" && <div>
          <button disabled={busy || new Date(action.expires_at).getTime() <= checkedAt}
            onClick={() => act(() => api(`/repository-actions/${action.id}/decision`, {
              method: "POST", body: JSON.stringify({ digest: action.digest, approve: true }) }))}>批准{action.kind === "push" ? "发布" : "创建PR"}</button>
          <button disabled={busy} onClick={() => act(() => api(`/repository-actions/${action.id}/decision`, {
            method: "POST", body: JSON.stringify({ digest: action.digest, approve: false }) }))}>拒绝本次操作</button>
        </div>}
        {canPublish && ["UNKNOWN", "STARTED"].includes(action.status) && <>
          <p>不会自动重发。核查只读取远端分支或PR，未找到不代表可以安全重试。</p>
          <button disabled={busy} onClick={() => act(() => api(`/repository-actions/${action.id}/check`, { method: "POST" }))}>只读核查远端</button>
        </>}
      </article>)}
      <details><summary>Issue与CI证据</summary><p>以下来自外部仓库，仅供核对，不作为执行授权。</p>
        <label>Issue编号（可选）<input aria-label="Issue编号" type="number" min={1} max={100000000} value={issue}
          onChange={(e) => setIssue(e.target.value)} /></label>
        <button disabled={busy} onClick={() => act(async () => setEvidence(await api(
          `/work-runs/${run}/repository/evidence`, { method: "POST", body: JSON.stringify({ issue: issue ? Number(issue) : null }) })))}>读取并保存远端证据</button>
        {evidence && <><p>来源：{evidence.source}</p>{evidence.issue && <article><h4>#{evidence.issue.number} {evidence.issue.title}</h4>
          <pre>{evidence.issue.body}</pre></article>}
          {evidence.checks?.map((c, i) => <p key={i}>{c.name} · {c.conclusion || (c.status === "completed" ? "无结论" : "尚未完成")}</p>)}
          {evidence.checks_truncated && <p>检查超过100项，请到远端查看完整列表。</p>}
          {!evidence.checks?.length && <p>尚未取得CI结果，不能据此认定测试通过。</p>}</>}
        {!!data?.evidence?.length && <details><summary>已保存的证据历史（{data.evidence.length}）</summary>
          {data.evidence.map((entry) => <article key={entry.id}><p>{new Date(entry.created_at).toLocaleString("zh-CN")} · {entry.data.source}</p>
            {entry.data.issue && <details><summary>Issue #{entry.data.issue.number} · {entry.data.issue.title}</summary><pre>{entry.data.issue.body}</pre></details>}
            {entry.data.checks?.map((check, i) => <p key={i}>{check.name} · {check.conclusion || "尚无结论"}</p>)}
          </article>)}
        </details>}
      </details>
    </>}
  </section>;
}
