"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api/platform";
import "./repositories.css";
import { ResourceDialog } from "./resource-dialog";
import { usePagedResources } from "./use-paged-resources";

type Repository = { id: string; name: string; slug: string; ref: string; subdirectory: string;
  revision: number; enabled: boolean; has_token: boolean };
const empty = { name: "", slug: "", ref: "main", subdirectory: "", enabled: true };

export function RepositorySettings({ canManage }: { canManage: boolean }) {
  const [query, setQuery] = useState("");
  const { items, cursor, loading, error: pageError, reload, more } = usePagedResources<Repository>("/repositories", query);
  const [editing, setEditing] = useState<Repository | null>(null);
  const [form, setForm] = useState(empty);
  const [open, setOpen] = useState(false);
  const [token, setToken] = useState("");
  const [clearToken, setClearToken] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  function edit(item: Repository | null) {
    setEditing(item); setForm(item || empty); setToken(""); setClearToken(false); setOpen(true); setError("");
  }
  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(""); setNotice("");
    try {
      await api(editing ? `/repositories/${editing.id}` : "/repositories", { method: editing ? "PUT" : "POST",
        body: JSON.stringify({ name: form.name, slug: form.slug, ref: form.ref, subdirectory: form.subdirectory,
          enabled: form.enabled, revision: editing?.revision, token: token || null, clear_token: clearToken }) });
      setToken(""); await reload(); setOpen(false); setNotice("仓库已保存。可在编程任务中选择，发起时固定版本。");
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  async function operation(item: Repository, remove = false) {
    if (remove && !window.confirm(`删除仓库连接“${item.name}”？已有任务的仓库请改为停用。`)) return;
    setBusy(true); setError(""); setNotice("");
    try {
      if (remove) { await api(`/repositories/${item.id}`, { method: "DELETE" }); await reload(); }
      else {
        const result = await api<{ sha: string; files: number }>(`/repositories/${item.id}/test`, { method: "POST" });
        setNotice(`只读检测通过：${result.files}个文件，版本 ${result.sha}。没有写入远端。`);
      }
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  return <section className="run-history repository-panel">
    <p>工作空间 / 代码仓库</p><header className="resource-list-heading"><h1>代码仓库</h1>{canManage && <button className="settings-primary" onClick={() => edit(null)}>添加仓库</button>}</header>
    <p>为开发任务连接GitHub仓库，审阅代码变化后再决定是否发布分支或创建草稿PR。</p>
    {(error || pageError) && !open && <p role="alert" className="platform-error">{error || pageError}<button onClick={() => void (cursor ? more() : reload()).catch(() => {})}>重试加载仓库</button></p>}
    {notice && <p role="status">{notice}</p>}
    <label className="resource-list-search"><input aria-label="搜索仓库" placeholder="搜索名称或GitHub仓库" value={query} onChange={(e) => setQuery(e.target.value)}/><span>已加载 {items.length} 个仓库</span></label>
    {open && <ResourceDialog title={editing ? "编辑仓库" : "添加仓库"} busy={busy} close={() => { setOpen(false); setToken(""); }}>{error && <p role="alert" className="platform-error">{error}</p>}<form className="repository-form" onSubmit={save}>
      <h2>{editing ? "编辑仓库" : "添加仓库"}</h2>
      <label>名称<input aria-label="仓库名称" value={form.name} maxLength={120} required
        onChange={(e) => setForm({ ...form, name: e.target.value })} /></label>
      <label>GitHub仓库<input aria-label="GitHub仓库" placeholder="组织名/仓库名" value={form.slug} maxLength={200}
        required onChange={(e) => setForm({ ...form, slug: e.target.value })} /></label>
      <label>基准分支或提交<input aria-label="仓库基准" value={form.ref} required maxLength={200}
        onChange={(e) => setForm({ ...form, ref: e.target.value })} /></label>
      <label>代码子目录（可选）<input aria-label="仓库子目录" value={form.subdirectory} maxLength={400}
        onChange={(e) => setForm({ ...form, subdirectory: e.target.value })} /></label>
      <p>当前本地检出上限180个文件、总计20MB、单文件1MB；较大仓库请选择代码子目录。子模块、链接和LFS下载不会自动执行。</p>
      <label>访问令牌<input aria-label="仓库访问令牌" type="password" autoComplete="new-password" value={token}
        placeholder={editing?.has_token ? "已保存，留空保持不变" : "公开仓库可留空"} maxLength={8192}
        onChange={(e) => { setToken(e.target.value); setClearToken(false); }} /></label>
      <small>读取需仓库内容只读权限；发布分支需内容写入，PR另需拉取请求写入。凭证按本人账号加密保存。</small>
      {editing?.has_token && <label><input type="checkbox" checked={clearToken}
        onChange={(e) => { setClearToken(e.target.checked); setToken(""); }} />清除已保存令牌</label>}
      <label><input type="checkbox" checked={form.enabled}
        onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />启用仓库</label>
      <div><button disabled={busy}>保存仓库</button><button type="button" disabled={busy}
        onClick={() => { setOpen(false); setToken(""); }}>取消</button></div>
    </form></ResourceDialog>}
    {loading && <p role="status">正在加载仓库…</p>}
    {!loading && !pageError && !items.length && <p>还没有仓库连接。添加后可在任务工作台选择。</p>}
    {items.length > 0 && !items.some((item) => `${item.name} ${item.slug}`.toLowerCase().includes(query.trim().toLowerCase())) && <p>没有匹配的仓库。</p>}
    {items.filter((item) => `${item.name} ${item.slug}`.toLowerCase().includes(query.trim().toLowerCase())).map((item) => <article key={item.id} className="repository-card">
      <div><strong>{item.name}</strong><p>{item.slug}{item.subdirectory ? ` / ${item.subdirectory}` : ""}</p>
        <small>{item.ref} · {item.enabled ? "已启用" : "已停用"} · {item.has_token ? "已保存令牌" : "公开访问"}</small></div>
      {canManage && <div><button disabled={busy} onClick={() => operation(item)}>只读检测</button>
        <button disabled={busy} onClick={() => edit(item)}>编辑</button>
        <button disabled={busy} onClick={() => operation(item, true)}>删除</button></div>}
    </article>)}
    {cursor && <button disabled={loading} onClick={() => void more().catch(() => {})}>加载更多仓库</button>}
  </section>;
}
