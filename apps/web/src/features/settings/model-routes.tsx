"use client";

import { FormEvent, useEffect, useState } from "react";
import { ConfigurationHistory } from "./configuration-history";
import { api } from "@/lib/api/platform";
import "../ask/repositories.css";
import { ResourceDialog } from "../ask/resource-dialog";

type Route = {
  id: string;
  name: string;
  primary_model: string;
  candidates: string[];
  revision: number;
  failures: number;
  cooldown: number;
  enabled: boolean;
  unavailable?: boolean;
  require_images: boolean;
  require_tools: boolean;
  require_native: boolean;
  digest: string;
  consented: boolean;
  receivers: { id: string; name: string; revision: number; receiver: string }[];
};
const blank = {
  name: "",
  primary_model: "",
  candidates: "",
  failures: 3,
  cooldown: 60,
  enabled: true,
  require_images: false,
  require_tools: false,
  require_native: false,
};

export function ModelRoutes({
  isAdmin,
  models,
}: {
  isAdmin: boolean;
  models: { id: string; name: string }[];
}) {
  const [open, setOpen] = useState(false), [query, setQuery] = useState("");
  const [items, setItems] = useState<Route[]>([]),
    [error, setError] = useState("");
  const [editing, setEditing] = useState<Route | null>(null),
    [form, setForm] = useState(blank);
  const [busy, setBusy] = useState(false);
  async function reload() {
    setItems((await api<{ items: Route[] }>("/model-routes")).items);
  }
  useEffect(() => {
    let active = true;
    void api<{ items: Route[] }>("/model-routes")
      .then((v) => {
        if (active) setItems(v.items);
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await operation();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    await action(async () => {
      await api(`/model-routes/${editing?.id || crypto.randomUUID()}`, {
        method: "PUT",
        body: JSON.stringify({
          ...form,
          candidates: [
            form.primary_model,
            ...form.candidates
              .split(/[,，\n]/)
              .map((v) => v.trim())
              .filter(Boolean),
          ],
          revision: editing?.revision,
        }),
      });
      setEditing(null);
      setForm(blank);
      setOpen(false);
    });
  }
  return (
    <section
      className="repository-panel phase-eight-settings model-routing-settings"
      aria-label="模型路由设置"
    >
      <header className="resource-list-heading"><h2>模型路由</h2>{isAdmin && <button className="settings-primary" onClick={() => { setEditing(null); setForm(blank); setError(""); setOpen(true); }}>新增路由</button>}</header>
      <p>
        手动开启后，在新运行发出前按顺序选择可用模型。请求发出后失败或结果未知，本轮停止，不向其他服务重发。
      </p>
      <p>
        启用入口：对话输入区、任务执行或智能体设置。图片、工具和联网权限不会因切换而增加。
      </p>
      <label className="resource-list-search"><input aria-label="搜索模型路由" placeholder="搜索路由名称" value={query} onChange={(e) => setQuery(e.target.value)}/><span>{items.length}条路由</span></label>
      {error && !open && <p role="alert">{error}</p>}
      {!items.length && <p>还没有模型路由。</p>}
      {items.length > 0 && !items.some((item) => item.name.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的记录。</p>}
      {items.filter((item) => item.name.toLowerCase().includes(query.toLowerCase())).map((item) => (
        <article
          key={item.id}
          className="repository-card"
        >
          <h3>
            {item.name} · 修订 {item.revision}
            {!item.enabled && " · 已停用"}
          </h3>
          <ol>
            {item.receivers.map((receiver) => (
              <li key={receiver.id}>
                {receiver.name} · 修订 {receiver.revision} · {receiver.receiver}
              </li>
            ))}
          </ol>
          <p>
            累计失败 {item.failures} 次后冷却 {item.cooldown} 秒；
            {item.consented ? "已同意当前接收方" : "当前接收方尚未确认"}
          </p>
          <button
            disabled={busy || !item.enabled || item.unavailable}
            onClick={() => {
              if (
                !item.consented &&
                !window.confirm(
                  "确认允许以上全部候选服务接收本次运行的上下文、图片和工具结果？路由不扩大已有工具和联网权限。",
                )
              )
                return;
              void action(() =>
                api(`/model-routes/${item.id}/consent`, {
                  method: item.consented ? "DELETE" : "POST",
                  ...(item.consented
                    ? {}
                    : { body: JSON.stringify({ digest: item.digest }) }),
                }),
              );
            }}
          >
            {item.consented ? "撤销同意" : "确认候选接收方"}
          </button>
          {isAdmin && (
            <button
              disabled={busy}
              onClick={() => {
                setOpen(true); setError("");
                setEditing(item);
                setForm({
                  ...item,
                  candidates: item.candidates.slice(1).join(","),
                });
              }}
            >
              编辑路由
            </button>
          )}
          {isAdmin && <ConfigurationHistory kind="route" resourceId={item.id} name={item.name}/>}
        </article>
      ))}
      {isAdmin && open && (
        <ResourceDialog title={editing ? "编辑路由" : "新增路由"} busy={busy} close={() => setOpen(false)}>{error && <p role="alert">{error}</p>}<form
          onSubmit={save}
          className="repository-form"
        >
          <h3>{editing ? "编辑路由" : "新增路由"}</h3>
          <label>
            路由名称
            <input
              required
              value={form.name}
              maxLength={100}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </label>
          <label>
            主模型
            <select
              required
              value={form.primary_model}
              onChange={(e) =>
                setForm({ ...form, primary_model: e.target.value })
              }
            >
              <option value="">请选择</option>
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
            按顺序添加备用模型
            <select
              value=""
              onChange={(e) => {
                if (e.target.value)
                  setForm({
                    ...form,
                    candidates: [
                      ...form.candidates.split(",").filter(Boolean),
                      e.target.value,
                    ].join(","),
                  });
              }}
            >
              <option value="">请选择备用模型</option>
              {models
                .filter(
                  (m) =>
                    m.id !== form.primary_model &&
                    !form.candidates.split(",").includes(m.id),
                )
                .map((m) => (
                  <option
                    key={m.id}
                    value={m.id}
                  >
                    {m.name}
                  </option>
                ))}
            </select>
          </label>
          <ol>
            {form.candidates
              .split(",")
              .filter(Boolean)
              .map((id) => (
                <li key={id}>
                  {models.find((m) => m.id === id)?.name || "模型已删除"}
                  <button
                    type="button"
                    onClick={() =>
                      setForm({
                        ...form,
                        candidates: form.candidates
                          .split(",")
                          .filter((v) => v !== id)
                          .join(","),
                      })
                    }
                  >
                    移除备用模型
                  </button>
                </li>
              ))}
          </ol>
          <label>
            失败次数
            <input
              type="number"
              min={1}
              max={20}
              value={form.failures}
              onChange={(e) =>
                setForm({ ...form, failures: Number(e.target.value) })
              }
            />
          </label>
          <label>
            冷却秒数
            <input
              type="number"
              min={10}
              max={3600}
              value={form.cooldown}
              onChange={(e) =>
                setForm({ ...form, cooldown: Number(e.target.value) })
              }
            />
          </label>
          {(
            [
              ["require_images", "要求图片输入"],
              ["require_tools", "要求工具调用"],
              ["require_native", "要求原生联网"],
              ["enabled", "启用路由"],
            ] as const
          ).map(([key, label]) => (
            <label key={key}>
              <input
                type="checkbox"
                checked={form[key]}
                onChange={(e) => setForm({ ...form, [key]: e.target.checked })}
              />
              {label}
            </label>
          ))}
          <button disabled={busy}>保存路由</button>
          {editing && (
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                setEditing(null);
                setForm(blank);
              }}
            >
              取消编辑
            </button>
          )}
        </form></ResourceDialog>
      )}
    </section>
  );
}

export function RouteSelection({ run }: { run: string }) {
  const [items, setItems] = useState<
    {
      id: string;
      name: string;
      scope: string;
      reason: { model: string; reason: string }[];
    }[]
  >([]);
  const [loadedRun, setLoadedRun] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    void api<{ items: typeof items }>(`/model-routes/runs/${run}`)
      .then((v) => {
        if (active) {
          setItems(v.items || []);
          setLoadedRun(run);
        }
      })
      .catch(() => {
        if (active) {
          setItems([]);
          setLoadedRun(run);
        }
      });
    return () => {
      active = false;
    };
  }, [run]);
  return loadedRun === run && items.length ? (
    <details>
      <summary>本轮实际模型与选择原因</summary>
      {items.map((item) => (
        <p key={item.id}>
          {item.name} ·{" "}
          {item.scope === "lead"
            ? "主助手"
            : item.scope.startsWith("team:")
              ? `团队成员 ${item.scope.slice(5)}`
              : "工作流模型调用"}
          ：{item.reason.map((r) => `${r.model}（${r.reason}）`).join("；")}
        </p>
      ))}
    </details>
  ) : null;
}
