"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "./resource-dialog";
import "./monitoring-dashboard.css";
import { MonitoringDetails } from "./monitoring-details";

type Rule = { id: string; name: string; scope: "personal" | "system"; metric: string; target: string;
  threshold: number | string; duration_seconds: number; cooldown_seconds: number; enabled: boolean;
  revision?: number; channel_ids: string[]; can_edit?: boolean };
type Alert = { id: string; name: string; status: string; value: number | string | null; acknowledged_at: string | null;
  first_seen_at: string; last_seen_at: string; recovered_at: string | null; can_ack: boolean };
type Snapshot = { services: { kind: string; online: number; last_heartbeat: string }[];
  queues: { mode: string; status: string; count: number; oldest_seconds: number | string | null }[];
  host: { cpu_percent: number | null; memory_available: number | null; memory_total: number | null;
    disk_free: number | null; disk_total: number | null };
  usage: { calls: number; input_tokens: number | string | null; output_tokens: number | string | null; unknown_tokens: number }[];
  costs: { currency: string | null; estimated_cost: string | null; unknown_calls: number }[];
  environments: { kind: string; state: string; count: number }[]; unknown_cost_calls?: number };
type Sample = { sampled_at: string | null; stale: boolean; age_seconds: number | null; snapshot: Snapshot | null;
  window?: { from: string; to: string; usage: { calls: number; input_tokens: number | string | null; output_tokens: number | string | null; unknown_tokens: number };
    costs: Snapshot["costs"]; unknown_cost_calls: number };
  runs: { total: number; succeeded: number; failed: number; duration_samples: number; p95_seconds: number | null } };
const prefix = "/operations/monitoring";
const labels: Record<string, string> = { service_offline: "后台失联", queue_wait_seconds: "排队等待秒数",
  failure_rate: "近1小时失败率（失败/发起）", memory_available_bytes: "可用内存字节", disk_free_bytes: "可用磁盘字节",
  budget_ratio: "本人预算已占用比例", api: "API接口", ask: "对话后台", work: "执行后台", ops: "运维后台",
  workflow: "工作流", coding: "代码环境", browser: "浏览器环境" };
function time(value: string | null) { return value ? new Date(value).toLocaleString("zh-CN") : "未采集"; }
function bytes(value: number | null | undefined) { return value == null ? "未采集" : `${(value / 1024 ** 3).toFixed(2)} GiB`; }
function initial(personal: boolean): Rule { return { id: "", name: "", scope: personal ? "personal" : "system",
  metric: personal ? "failure_rate" : "service_offline", target: personal ? "" : "api", threshold: 1,
  duration_seconds: 0, cooldown_seconds: 300, enabled: true, channel_ids: [] }; }

export function MonitoringAlerts({ personal = false, canManage = true }: { personal?: boolean; canManage?: boolean }) {
  const [tab, setTab] = useState("events"), [rules, setRules] = useState<Rule[]>([]), [events, setEvents] = useState<Alert[]>([]);
  const [next, setNext] = useState<string | null>(null), [editing, setEditing] = useState<Rule | null>(null);
  const [nextRule, setNextRule] = useState<string | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false);
  const [channels, setChannels] = useState<{ id: string; name: string }[]>([]);
  async function reload() {
    const [r, e] = await Promise.all([api<{ items: Rule[]; next_cursor: string | null }>(prefix + "/alerts/rules"),
      api<{ items: Alert[]; next_cursor: string | null }>(prefix + "/alerts/events")]);
    setRules(r.items.filter(row => !personal || row.scope === "personal"));
    setNextRule(r.next_cursor);
    setEvents(e.items); setNext(e.next_cursor);
  }
  useEffect(() => {
    let active = true;
    Promise.all([api<{ items: Rule[]; next_cursor: string | null }>(prefix + "/alerts/rules"),
      api<{ items: Alert[]; next_cursor: string | null }>(prefix + "/alerts/events")]).then(([r, e]) => {
        if (active) { setRules(r.items.filter(row => !personal || row.scope === "personal")); setNextRule(r.next_cursor); setEvents(e.items); setNext(e.next_cursor); }
      }).catch(reason => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [personal]);
  async function action(work: () => Promise<unknown>) {
    setBusy(true); setError("");
    try { await work(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  function open(rule: Rule) {
    setEditing(rule);
    void api<{ items: { id: string; name: string }[] }>("/notification-channels")
      .then(result => setChannels(result.items)).catch(() => setChannels([]));
  }
  function save(event: FormEvent) {
    event.preventDefault(); if (!editing) return;
    void action(async () => {
      const { id, name, scope, metric, target, threshold, duration_seconds, cooldown_seconds, enabled, channel_ids, revision } = editing;
      const body = { name, scope, metric, target, threshold, duration_seconds, cooldown_seconds, enabled, channel_ids, revision };
      await api(prefix + "/alerts/rules" + (id ? `/${id}` : ""),
        { method: id ? "PUT" : "POST", body: JSON.stringify(body) });
      await reload(); setEditing(null);
    });
  }
  return <section className="monitor-alerts" aria-label={personal ? "本人监测告警" : "监测告警"}>
    <header><div><h2>{personal ? "本人监测告警" : "告警与规则"}</h2><p>确认表示已知悉；指标恢复后才显示已恢复。</p></div>
      <div><button type="button" onClick={() => void action(reload)} disabled={busy}>刷新告警</button>
        {canManage && <button type="button" onClick={() => open(initial(personal))}>新增告警规则</button>}</div></header>
    <div role="tablist" aria-label="告警列表"><button role="tab" aria-selected={tab === "events"} onClick={() => setTab("events")}>事件记录</button>
      <button role="tab" aria-selected={tab === "rules"} onClick={() => setTab("rules")}>规则列表</button></div>
    {error && <p role="alert">{error}</p>}
    {tab === "rules" ? <div className="monitor-list">{rules.length === 0 && <p>暂无告警规则</p>}
      {rules.map(rule => <article key={rule.id}><div><strong>{rule.name}</strong><small>{labels[rule.metric]} · {rule.enabled ? "已启用" : "已停用"} · 修订{rule.revision}</small></div>
        {canManage && rule.can_edit !== false && <button onClick={() => open(rule)}>编辑规则</button>}</article>)}
      {nextRule && <button disabled={busy} onClick={() => void action(async () => {
        const result = await api<{ items: Rule[]; next_cursor: string | null }>(prefix + "/alerts/rules?before=" + encodeURIComponent(nextRule));
        setRules(old => [...old, ...result.items.filter(item => (!personal || item.scope === "personal") && !old.some(row => row.id === item.id))]);
        setNextRule(result.next_cursor);
      })}>加载更多规则</button>}</div>
      : <div className="monitor-list">{events.length === 0 && <p>暂无告警事件</p>}{events.map(event => <article key={event.id}>
        <div><strong>{event.name}</strong><small>{time(event.first_seen_at)} · 最近{time(event.last_seen_at)}</small></div>
        <span data-state={event.status}>{event.status === "RECOVERED" ? "已恢复" : event.acknowledged_at ? "已确认 · 仍在告警" : "待处理"}</span>
        {!event.acknowledged_at && event.can_ack && <button disabled={busy} onClick={() => void action(async () => {
          await api(prefix + `/alerts/events/${event.id}/acknowledge`, { method: "POST" }); await reload();
        })}>确认告警</button>}</article>)}{next && <button disabled={busy} onClick={() => void action(async () => {
          const result = await api<{ items: Alert[]; next_cursor: string | null }>(prefix + "/alerts/events?before=" + encodeURIComponent(next));
          setEvents(old => [...old, ...result.items.filter(item => !old.some(row => row.id === item.id))]); setNext(result.next_cursor);
        })}>加载更早告警</button>}</div>}
    {editing && <ResourceDialog title={editing.id ? "编辑告警规则" : "新增告警规则"} busy={busy} close={() => setEditing(null)}>
      <form onSubmit={save} className="monitor-rule-form">
        <label>规则名称<input required maxLength={120} value={editing.name} onChange={e => setEditing({ ...editing, name: e.target.value })} /></label>
        <label>告警指标<select value={editing.metric} onChange={e => setEditing({ ...editing, metric: e.target.value,
          target: e.target.value === "service_offline" ? "api" : e.target.value === "queue_wait_seconds" ? "work" : e.target.value === "budget_ratio" ? "CNY" : "" })}>
          {(personal ? ["failure_rate", "budget_ratio"] : ["service_offline", "queue_wait_seconds", "failure_rate", "memory_available_bytes", "disk_free_bytes"]).map(key => <option key={key} value={key}>{labels[key]}</option>)}</select></label>
        {["service_offline", "queue_wait_seconds", "budget_ratio"].includes(editing.metric) && <label>告警目标<select value={editing.target} onChange={e => setEditing({ ...editing, target: e.target.value })}>
          {(editing.metric === "service_offline" ? ["api", "ask", "work", "ops"] : editing.metric === "queue_wait_seconds" ? ["ask", "work", "workflow"] : ["CNY", "USD"]).map(key => <option value={key} key={key}>{labels[key] || key}</option>)}</select></label>}
        <label>阈值<input type="number" min="0" step="any" required value={editing.threshold} onChange={e => setEditing({ ...editing, threshold: e.target.value })} /></label>
        <small>资源低于阈值告警，其余达到阈值告警。失败率和预算用0～1比例，失联阈值为1；未知值不会填0。</small>
        <label>持续时间（秒）<input type="number" min="0" max="86400" value={editing.duration_seconds} onChange={e => setEditing({ ...editing, duration_seconds: Number(e.target.value) })} /></label>
        <label>通知冷却（秒）<input type="number" min="30" max="604800" value={editing.cooldown_seconds} onChange={e => setEditing({ ...editing, cooldown_seconds: Number(e.target.value) })} /></label>
        <label>外发通道（可选）<select multiple value={editing.channel_ids} onChange={e => setEditing({ ...editing, channel_ids: Array.from(e.target.selectedOptions, option => option.value) })}>
          {channels.map(channel => <option value={channel.id} key={channel.id}>{channel.name}</option>)}</select></label>
        <label><input type="checkbox" checked={editing.enabled} onChange={e => setEditing({ ...editing, enabled: e.target.checked })} />启用规则</label>
        <p>站内事件持久保存。外发仅使用本人已授权通道，不自动重放结果未知的投递。</p>
        <small>当前单机最多500条、每账号最多50条规则。</small>
        <footer><button type="button" disabled={busy} onClick={() => setEditing(null)}>取消</button><button disabled={busy}>保存规则</button></footer>
      </form></ResourceDialog>}
  </section>;
}

export function MonitoringDashboard({ canManage }: { canManage: boolean }) {
  const [hours, setHours] = useState(1), [sample, setSample] = useState<Sample | null>(null);
  const [history, setHistory] = useState<{ sampled_at: string; snapshot: Snapshot }[]>([]);
  const [error, setError] = useState(""), [loading, setLoading] = useState(true), [refresh, setRefresh] = useState(0);
  const version = useRef(0);
  useEffect(() => {
    const current = ++version.current;
    let active = true;
    async function load() {
      try {
        const [summary, series] = await Promise.all([api<Sample>(`${prefix}?hours=${hours}`),
          api<{ items: typeof history }>(`${prefix}/series?hours=${hours}`)]);
        if (active && current === version.current) { setSample(summary); setHistory(series.items); setError(""); }
      } catch (reason) { if (active) { setError((reason as Error).message); setSample(null); setHistory([]); } }
      finally { if (active) setLoading(false); }
    }
    void load(); const timer = setInterval(() => void load(), 30000);
    return () => { active = false; clearInterval(timer); };
  }, [hours, refresh]);
  const data = sample?.snapshot, pending = data?.queues.filter(row => row.status === "PENDING").reduce((sum, row) => sum + row.count, 0);
  const completed = sample ? sample.runs.succeeded + sample.runs.failed : 0;
  const points = history.map((row, index) => {
    const value = row.snapshot.host.cpu_percent;
    if (value == null) return "";
    const command = index > 0 && history[index - 1].snapshot.host.cpu_percent != null ? "L" : "M";
    return `${command}${history.length > 1 ? index * 100 / (history.length - 1) : 50},${100 - Math.min(100, value)}`;
  });
  return <section className="monitor-dashboard" aria-label="系统监测大盘">
    <header className="monitor-heading"><div><span>系统 / 可观测性</span><h1>监测大盘</h1><p>全局匿名汇总 · 不读取对话或文件正文</p></div>
      <div><label>统计时间范围<select value={hours} onChange={e => { setLoading(true); setSample(null); setHistory([]); setHours(Number(e.target.value)); }}><option value={1}>过去1小时</option><option value={24}>过去24小时</option><option value={168}>过去7天</option></select></label>
        <button disabled={loading} onClick={() => { setLoading(true); setRefresh(value => value + 1); }}>刷新大盘</button></div></header>
    {error && <p role="alert">读取失败：{error} <button onClick={() => setRefresh(value => value + 1)}>重试采集结果</button></p>}
    {loading && !sample && <p role="status">正在读取真实采集结果…</p>}
    {sample && <p className={sample.stale ? "monitor-warning" : "monitor-note"} role="status">{sample.stale ? "采集过期或尚未采集，请检查运维后台；不能据此认定健康。" : "采集正常"} · 最近采集：{time(sample.sampled_at)} · 历史保留7天</p>}
    <div className="monitor-cards">
      <article><span>待执行</span><strong>{pending == null ? "未采集" : pending}</strong><small>全局当前队列快照</small></article>
      <article><span>运行成功率</span><strong>{completed ? `${(sample!.runs.succeeded / completed * 100).toFixed(1)}%` : "暂无样本"}</strong><small>所选范围内成功 /（成功＋失败）</small></article>
      <article><span>已完成运行 P95</span><strong>{sample?.runs.p95_seconds == null ? "暂无样本" : `${sample.runs.p95_seconds.toFixed(2)}秒`}</strong><small>{sample?.runs.duration_samples ?? 0}个结束样本，不是首Token延迟</small></article>
      <article><span>可用内存</span><strong>{bytes(data?.host.memory_available)}</strong><small>主机观测，不是可承诺吞吐量</small></article>
      <article><span>可用磁盘</span><strong>{bytes(data?.host.disk_free)}</strong><small>包含附件、索引和备份存储</small></article>
      <article><span>未知或未计价调用</span><strong>{data ? data.unknown_cost_calls ?? data.costs.reduce((sum, row) => sum + row.unknown_calls, 0) : "未采集"}</strong><small>不会显示为免费</small></article>
      <article><span>所选范围输入 / 输出 Token</span><strong>{sample?.window ? `${sample.window.usage.input_tokens == null ? "未知" : Number(sample.window.usage.input_tokens).toLocaleString("zh-CN")} / ${sample.window.usage.output_tokens == null ? "未知" : Number(sample.window.usage.output_tokens).toLocaleString("zh-CN")}` : "未采集"}</strong>
        <small>{sample?.window ? `${sample.window.usage.calls}次调用 · 未完整报告Token ${sample.window.usage.unknown_tokens}次` : "需读取所选窗口用量"}</small></article>
      <article><span>所选范围费用估算</span><strong>{sample?.window ? sample.window.costs.filter(row => row.currency).map(row => `${row.currency} ${row.estimated_cost == null ? "未计价" : row.estimated_cost}`).join("；") || "暂无已定价调用" : "未采集"}</strong>
        <small>{sample?.window ? `未知或未计价${sample.window.unknown_cost_calls}次，按实际调用发生时间归属；不是供应商账单` : "未计价不能当作免费"}</small></article>
    </div>
    {sample?.window && <p className="monitor-note">用量窗口：{time(sample.window.from)} — {time(sample.window.to)}（结束时间不含）；队列/主机为最新快照，累计账本另列。</p>}
    <section className="monitor-panel" aria-label="后台实例监测"><h2>后台与执行环境</h2>{!data ? <p>尚无持久采样，请启动运维后台。</p> : <>
      <div className="monitor-service-grid">{["api", "ask", "work", "ops"].map(kind => { const service = data.services.find(row => row.kind === kind);
        const healthy = !sample?.stale && (service?.online ?? 0) > 0;
        return <article key={kind}><strong>{labels[kind]}</strong><span data-state={healthy ? "RECOVERED" : "FIRING"}>{healthy ? `${service!.online}个在线` : sample?.stale ? "采集过期" : "未发现有效心跳"}</span><small>{time(service?.last_heartbeat ?? null)}</small></article>;
      })}</div><p>环境预留：{data.environments.length ? data.environments.map(row => `${labels[row.kind] || row.kind} ${row.state} ×${row.count}`).join("；") : "当前没有预留环境"}</p>
      {data.queues.filter(row => row.status === "PENDING").map(row => <p key={row.mode}>{labels[row.mode] || row.mode}最老等待：{Number(row.oldest_seconds || 0).toFixed(0)}秒</p>)}</>}
    </section>
    <section className="monitor-panel" aria-label="资源历史趋势"><h2>CPU历史趋势</h2><p>持久采样每30秒，历史按时间范围降采样；无数据不补造。</p>
      {history.some(row => row.snapshot.host.cpu_percent != null) ? <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="CPU使用率历史"><path d={points.join(" ")} fill="none" stroke="currentColor" strokeWidth="1" /></svg> : <p>暂无可展示的CPU历史样本</p>}
    </section>
    <section className="monitor-panel"><h2>费用与Token累计快照</h2><p>以下为保留账本的累计值，按币种分别显示；不自动换汇，不代表供应商账单。</p>
      {!data ? <p>未采集</p> : <><p>输入{data.usage.some(row => row.input_tokens != null) ? data.usage.reduce((sum, row) => sum + Number(row.input_tokens || 0), 0).toLocaleString("zh-CN") : "未知"} / 输出{data.usage.some(row => row.output_tokens != null) ? data.usage.reduce((sum, row) => sum + Number(row.output_tokens || 0), 0).toLocaleString("zh-CN") : "未知"} Token；未报告完整Token {data.usage.reduce((sum, row) => sum + row.unknown_tokens, 0)}次</p>
        {data.costs.map(row => <p key={row.currency || "unknown"}>{row.currency || "未定币种"}：{row.estimated_cost == null ? "未计价" : row.estimated_cost} · 未计价{row.unknown_calls}次</p>)}</>}
    </section>
    <MonitoringDetails hours={hours} />
    <MonitoringAlerts canManage={canManage} />
  </section>;
}
