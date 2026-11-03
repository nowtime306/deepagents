"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";
import "./habit-learning.css";

type Preference = {
  id: string; kind: string; value: string | null; status: "learning" | "active" | "disabled" | "forgotten";
  evidence_count: number; revision: number; skill_id: string | null; skill_version: number | null;
  skill_enabled?: boolean | null; manual_confirmed?: boolean; updated_at: string;
};
type Settings = {
  enabled: boolean; meta_skill_id: string | null; meta_skill_bound: boolean; meta_skill_enabled?: boolean;
  threshold: number; scope: string; preferences: Preference[];
  options: Record<string, { value: string; label: string }[]>;
};
type Evidence = { message_id: string; conversation_id: string; created_at: string; available: boolean };
const kinds: Record<string, string> = {
  language: "回复语言", output_format: "输出格式", detail_level: "内容详略", docs_first: "开发前先写文档",
  review_before_test: "代码审查后交测试", management_layout: "管理页面布局",
};
const statuses = { learning: "正在积累证据", active: "已生成技能", disabled: "已暂停", forgotten: "已遗忘，旧证据不会再生" };

export function HabitLearning({ canRead, canManage, canBind, canReadChat, resourcesRevision, onChanged, onOpenConversation }: {
  canRead: boolean; canManage: boolean; canBind: boolean; canReadChat: boolean;
  resourcesRevision: string;
  onChanged: () => Promise<void>; onOpenConversation?: (id: string) => void;
}) {
  const [data, setData] = useState<Settings | null>(null), [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [editing, setEditing] = useState<Preference | null>(null), [value, setValue] = useState("");
  const [sources, setSources] = useState<{ title: string; items: Evidence[] } | null>(null);
  const [preview, setPreview] = useState<{ name: string; files: Record<string, string> } | null>(null);
  const live = useRef(false), request = useRef<AbortController | null>(null);
  const readVersion = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    if (!canRead) return;
    const version = ++readVersion.current;
    const result = await api<Settings>("/capabilities/habits", { signal });
    if (live.current && !signal?.aborted && version === readVersion.current) setData(result);
  }, [canRead]);
  useEffect(() => {
    live.current = true;
    return () => { live.current = false; request.current?.abort(); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    void load(controller.signal).catch(reason => { if (!controller.signal.aborted) setError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load, resourcesRevision]);

  async function action(operation: (signal: AbortSignal) => Promise<unknown>, message: string) {
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    setBusy(true); setError(""); setNotice("");
    try {
      await operation(controller.signal);
      if (!live.current || controller.signal.aborted) return false;
      await load(controller.signal); await onChanged();
      if (live.current && !controller.signal.aborted) setNotice(message);
      return true;
    } catch (reason) {
      if (live.current && !controller.signal.aborted) {
        setError((reason as Error).message);
        if (reason instanceof ApiError && reason.status === 409) { setEditing(null); await load(controller.signal).catch(() => {}); }
      }
      return false;
    } finally { if (live.current && !controller.signal.aborted) setBusy(false); }
  }
  async function enable(enabled: boolean) {
    await action(async signal => {
      if (enabled && (!data?.meta_skill_bound || data.meta_skill_enabled === false) && canBind) await api("/capabilities/habits/install", { method: "POST", signal });
      return api("/capabilities/habits", { method: "PUT", signal, body: JSON.stringify({ enabled }) });
    }, enabled ? "习惯学习已开启。新运行选中元技能后可开始积累证据。" : "已停止学习及元库自动读取，手工绑定的技能仍按原授权管理。");
  }
  async function evidence(preference: Preference) {
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    setBusy(true); setError("");
    try {
      const result = await api<{ items: Evidence[] }>(`/capabilities/habits/preferences/${preference.id}/evidence`, { signal: controller.signal });
      if (live.current && !controller.signal.aborted) setSources({ title: kinds[preference.kind] || preference.kind, items: result.items });
    } catch (reason) { if (live.current && !controller.signal.aborted) setError((reason as Error).message); }
    finally { if (live.current && !controller.signal.aborted) setBusy(false); }
  }
  async function viewSkill(id: string) {
    const controller = new AbortController(); request.current?.abort(); request.current = controller;
    setBusy(true); setError("");
    try {
      const result = await api<{ name: string; files: Record<string, string> }>(`/capabilities/skills/${id}`, { signal: controller.signal });
      if (live.current && !controller.signal.aborted) setPreview(result);
    } catch (reason) { if (live.current && !controller.signal.aborted) setError((reason as Error).message); }
    finally { if (live.current && !controller.signal.aborted) setBusy(false); }
  }

  if (!canRead) return null;
  return <section className="habit-learning" aria-label="习惯学习管理">
    <header><div><h2>习惯学习</h2><p>把你反复明确表达的通用工作偏好，变成可复用的私有 Skill。</p></div>
      <button type="button" disabled={busy || loading} onClick={() => void action(signal => load(signal), "习惯记录已刷新")}>刷新习惯</button></header>
    {error && <p className="platform-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {loading && <p role="status">正在加载习惯记录…</p>}
    {data && <>
      <div className="habit-learning-settings"><label><input type="checkbox" aria-label="开启习惯学习" checked={data.enabled} disabled={!canManage || busy}
        onChange={event => void enable(event.target.checked)}/>开启习惯学习</label>
        <span>{data.threshold} 条独立用户消息后自动生成 · 当前账号通用偏好</span>
        {!data.meta_skill_bound && <button type="button" disabled={!canManage || !canBind || busy} onClick={() => void action(signal => api("/capabilities/habits/install", { method: "POST", signal }), "元技能已接入通用助手，请开启学习后开始新运行")}>接入通用助手</button>}
        {data.meta_skill_id && <button type="button" disabled={busy} onClick={() => void viewSkill(data.meta_skill_id!)}>查看元技能</button>}
      </div>
      <p className="habit-learning-help">通用助手{data.meta_skill_bound ? "已接入" : "尚未接入"}。{data.meta_skill_enabled === false && "元技能已停用，请先在下方技能列表启用。"}独立智能体需在资源配置中绑定同一元技能，并按原流程发布新版本。不会改写旧版本或正在进行的回答。</p>
      <details className="habit-learning-help"><summary>学习范围与使用方式</summary><p>目前支持回复语言、输出格式、详略、文档优先、审查后测试和列表加新增布局。只采集已开启且选用元技能的真实对话消息；本次限定、特定项目、引用资料、临时授权和凭据不学习。任务模式可使用已有子技能，不从任务文字取证。</p>
        <p>生成只写操作规范，不生成脚本或授予执行权限。通过当前模型的受控工具步骤提议，实际模型用量仍记本轮；没有额外后台模型提取任务。未知习惯不会被强行归类。</p></details>
      {!data.preferences.length && <div className="habit-learning-empty">暂无习惯记录。开启后可在新对话中明确表达长期偏好，例如“以后回复默认用中文”。</div>}
      <div className="habit-preferences">{data.preferences.map(preference => {
        const paused = preference.status === "disabled" || preference.skill_enabled === false;
        const label = data.options[preference.kind]?.find(option => option.value === preference.value)?.label || preference.value || "已清除";
        return <article className="habit-preference" key={preference.id} data-habit-id={preference.id}>
          <div><strong>{kinds[preference.kind] || preference.kind}</strong><p>{label}</p><small>{statuses[preference.status]} · {preference.manual_confirmed ? "已由你直接确认" : `${preference.evidence_count}/${data.threshold} 条有效证据`}{preference.skill_version ? ` · Skill v${preference.skill_version}` : ""}{preference.skill_enabled === false ? " · 技能已停用" : ""}{preference.status === "active" && !preference.manual_confirmed && preference.evidence_count < data.threshold ? " · 来源不足，暂不可用" : ""}{preference.status === "active" && !preference.skill_id ? " · 生成技能已删除，请纠正或重新学习" : ""}</small></div>
          <div className="habit-preference-actions">
            <button type="button" disabled={busy || !canReadChat || preference.status === "forgotten"} onClick={() => void evidence(preference)}>查看来源</button>
            {preference.skill_id && <button type="button" disabled={busy} onClick={() => void viewSkill(preference.skill_id!)}>查看生成技能</button>}
            {canManage && (preference.status === "forgotten" ? <button type="button" disabled={busy} onClick={() => void action(signal => api(`/capabilities/habits/preferences/${preference.id}/relearn`, { method: "POST", signal, body: JSON.stringify({ revision: preference.revision }) }), "已重新开始学习，只统计之后的新消息")}>重新学习</button> : <>
              <button type="button" disabled={busy} onClick={() => { setEditing(preference); setValue(preference.value || data.options[preference.kind]?.[0]?.value || ""); }}>纠正或采纳</button>
              <button type="button" disabled={busy} onClick={() => void action(signal => api(`/capabilities/habits/preferences/${preference.id}`, { method: "PATCH", signal, body: JSON.stringify({ enabled: paused, revision: preference.revision }) }), paused ? "已恢复此习惯" : "已暂停此习惯")}>{paused ? "恢复" : "暂停"}</button>
              <button type="button" disabled={busy} onClick={() => void action(signal => api(`/capabilities/habits/preferences/${preference.id}?revision=${preference.revision}`, { method: "DELETE", signal }), "已遗忘并撤销派生技能，旧证据不会重新生成")}>遗忘</button>
            </>)}
          </div>
        </article>;
      })}</div>
      <p className="habit-learning-help">遗忘会清除派生技能内容及学习证据，撤销后续使用，并保留不含原文的抑制标识；不会删除原对话或你的手工 Skill。停止学习、暂停单条和遗忘是不同操作。</p>
    </>}
    {editing && data && <ResourceDialog title="纠正或采纳习惯" busy={busy} close={() => setEditing(null)}><form onSubmit={event => {
      event.preventDefault(); void action(signal => api(`/capabilities/habits/preferences/${editing.id}/correct`, { method: "POST", signal, body: JSON.stringify({ value, revision: editing.revision }) }), "习惯已由你确认，新的生成技能供后续运行使用").then(ok => { if (ok && live.current) setEditing(null); });
    }}><label>{kinds[editing.kind] || editing.kind}<select aria-label="习惯更正值" value={value} disabled={busy} onChange={event => setValue(event.target.value)}>{data.options[editing.kind]?.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <p>此操作明确确认你的偏好，可以直接生成 Skill；不会覆盖手工技能或修改原对话。旧运行不会自动改读新正文。</p><button className="settings-primary" disabled={busy || !value}>确认此习惯</button></form></ResourceDialog>}
    {sources && <ResourceDialog title={`习惯来源：${sources.title}`} busy={busy} close={() => setSources(null)}><p>只列仍有权限访问的真实来源，不复制原文到习惯记录。</p>
      {!sources.items.length && <p>没有仍可访问的原始来源。手工确认与自动积累分别计数。</p>}
      {sources.items.map(source => <article className="habit-source" key={source.message_id}><small>{new Date(source.created_at).toLocaleString("zh-CN")}</small><code>{source.message_id}</code><button type="button" disabled={!source.available || !onOpenConversation} onClick={() => onOpenConversation?.(source.conversation_id)}>打开来源会话</button></article>)}
    </ResourceDialog>}
    {preview && <ResourceDialog title={`技能资源：${preview.name}`} busy={busy} close={() => setPreview(null)}><pre className="habit-skill-preview">{preview.files["SKILL.md"] || "没有 SKILL.md 资源"}</pre></ResourceDialog>}
  </section>;
}
