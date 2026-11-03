"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api/platform";
import { usePagedResources } from "./use-paged-resources";
import { WorkRunPanel } from "./work-run-panel";
import { ResourceDialog } from "./resource-dialog";
import type { RepositoryPermissions } from "./repository-picker";

type Task = {
  workflow_task?: boolean;
  id: string;
  title: string;
  goal: string;
  revision: number;
  updated_at: string;
};
export function WorkTasks({ repositoryPermissions = {}, canManage = true }: { repositoryPermissions?: RepositoryPermissions; canManage?: boolean }) {
  const [showEditor, setShowEditor] = useState(false);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Task | null>(null);
  const { items, reload, more, cursor, loading, error: listError } = usePagedResources<Task>("/work-tasks", query);
  const [editing, setEditing] = useState<Task | null>(null);
  const [title, setTitle] = useState("");
  const [goal, setGoal] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  function clear() {
    setShowEditor(false);
    setEditing(null);
    setTitle("");
    setGoal("");
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(editing ? `/work-tasks/${editing.id}` : "/work-tasks", {
        method: editing ? "PUT" : "POST",
        body: JSON.stringify({ title, goal, revision: editing?.revision }),
      });
      await reload();
      clear();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(id: string) {
    if (!window.confirm("确定删除此任务草稿吗？")) return;
    setBusy(true);
    setError("");
    try {
      await api(`/work-tasks/${id}`, { method: "DELETE" });
      await reload();
      if (editing?.id === id) clear();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="run-history">
      <p>工作空间 / 任务工作台</p>
      <header className="resource-list-heading"><div><h1>任务工作台</h1><p>保存任务目标，在执行详情查看进度、审批和成果。</p></div><button className="settings-primary" disabled={!canManage} onClick={() => { clear(); setError(""); setShowEditor(true); }}>新建任务</button></header>
      <label className="resource-list-search"><input aria-label="搜索任务" placeholder="搜索任务标题或目标" value={query} onChange={(event) => setQuery(event.target.value)}/><span>已加载 {items.length} 个任务</span></label>
      {selected && (
        <WorkRunPanel
          key={selected.id}
          task={selected}
          repositoryPermissions={repositoryPermissions}
          close={() => {
            setSelected(null);
            void reload();
          }}
        />
      )}
      {(error || listError) && !showEditor && (
        <div
          role="alert"
          className="platform-error"
        >
          {error || listError}
          {listError && <button onClick={() => void reload().catch(() => {})}>重试加载任务</button>}
        </div>
      )}
      {showEditor && <ResourceDialog title={editing ? "编辑任务草稿" : "新建任务草稿"} busy={busy} close={clear}>
      {error && <p role="alert" className="platform-error">{error}</p>}
      <form
        className="agent-form"
        onSubmit={save}
      >
        <h2>{editing ? "编辑任务草稿" : "新建任务草稿"}</h2>
        <label>
          标题{" "}
          <input
            aria-label="任务标题"
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            maxLength={120}
            required
          />
        </label>
        <label>
          任务目标{" "}
          <textarea
            aria-label="任务目标"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            maxLength={12000}
            required
          />
        </label>
        <button
          className="settings-primary"
          disabled={busy}
        >
          {busy ? "保存中…" : "保存草稿"}
        </button>
        {editing && (
          <button
            type="button"
            onClick={clear}
          >
            取消编辑
          </button>
        )}
      </form></ResourceDialog>}
      <h2>我的任务草稿</h2>
      {loading && <p role="status">正在加载任务…</p>}
      {!loading && !listError && !items.length && <p>{query ? "没有匹配的任务。" : "还没有任务草稿。"}</p>}
      {!loading && items.length > 0 && !items.some((task) => `${task.title} ${task.goal}`.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的任务。</p>}
      {items.filter((task) => `${task.title} ${task.goal}`.toLowerCase().includes(query.toLowerCase())).map((task) => (
        <article
          className="run-history-card"
          key={task.id}
        >
          <div>
            <strong>{task.title}</strong>
            <p>{task.goal}</p>
            <small>
              草稿 · 更新于 {new Date(task.updated_at).toLocaleString("zh-CN")}
            </small>
          </div>
          <button
            type="button"
            disabled={busy || !canManage}
            onClick={() => {
              setShowEditor(true);
              setError("");
              setEditing(task);
              setTitle(task.title);
              setGoal(task.goal);
            }}
          >
            编辑
          </button>
          <button
            type="button"
            onClick={() => setSelected(task)}
          >
            执行详情
          </button>
          <button
            type="button"
            disabled={busy || !canManage}
            onClick={() => void remove(task.id)}
          >
            删除
          </button>
        </article>
      ))}
      {cursor && <button type="button" disabled={loading} onClick={() => void more().catch(() => {})}>加载更多任务</button>}
    </section>
  );
}
