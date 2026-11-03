"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { RunDiagnostics } from "./run-diagnostics";

type Instance = { id: string; kind: string; node: string | null; fault_domain: string | null; release: string | null;
  live: boolean | null; ready: boolean | null; stale: boolean; heartbeat_at: string };
type Failure = { id: string; mode: "ask" | "work" | "workflow"; error_code: string; can_open: boolean; resource_id: string | null; created_at: string };
type Trend = { time: string; started: number; failed: number; succeeded: number };
const prefix = "/operations/monitoring";
const kinds: Record<string, string> = { api: "API", ask: "对话后台", work: "执行后台", ops: "运维后台", workflow: "工作流" };
const state = (value: boolean | null) => value == null ? "未独立观测" : value ? "是" : "否";

function WorkflowFailure({ row }: { row: Failure }) {
  const [nodes, setNodes] = useState<{ node_id: string; event: string; created_at: string }[] | null>(null);
  const [error, setError] = useState("");
  return <div><button onClick={() => {
    setError("");
    void api<{ items: NonNullable<typeof nodes> }>(`/workflows/${row.resource_id}/runs/${row.id}/events`)
      .then(result => setNodes(result.items)).catch(reason => setError(reason.message));
  }}>查看此工作流运行节点</button>{error && <p role="alert">{error}</p>}
    {nodes && <ul>{nodes.length ? nodes.map((node, index) => <li key={index}>{node.node_id} · {node.event} · {new Date(node.created_at).toLocaleString("zh-CN")}</li>) : <li>没有节点事件，运行可能在启动前失败。</li>}</ul>}</div>;
}

export function MonitoringDetails({ hours }: { hours: number }) {
  const [instances, setInstances] = useState<Instance[]>([]), [runs, setRuns] = useState<Trend[]>([]), [failures, setFailures] = useState<Failure[]>([]);
  const [error, setError] = useState(""), [loading, setLoading] = useState(true), [refresh, setRefresh] = useState(0);
  const [opened, setOpened] = useState<string | null>(null);
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [i, r, e] = await Promise.all([api<{ items: Instance[] }>(prefix + "/instances"),
          api<{ items: Trend[] }>(prefix + `/run-trend?hours=${hours}`), api<{ items: Failure[] }>(prefix + `/errors?hours=${hours}`)]);
        if (active) { setInstances(i.items); setRuns(r.items); setFailures(e.items); setError(""); }
      } catch (reason) {
        if (active) { setError((reason as Error).message); setInstances([]); setRuns([]); setFailures([]); }
      } finally { if (active) setLoading(false); }
    }
    void load(); const timer = setInterval(() => void load(), 30000);
    return () => { active = false; clearInterval(timer); };
  }, [hours, refresh]);
  const peak = Math.max(1, ...runs.map(row => Number(row.started)));
  return <>
    <section className="monitor-panel"><h2>部署实例与就绪观测</h2>
      <p>存活：本节点进程是否存在，远端未探测为未知。就绪：15秒心跳及数据库准入锁；不是独立HTTP探测。当前共享数据库和文件单写，同机备用不代表物理高可用。</p>
      {error && <p role="alert">{error} <button onClick={() => setRefresh(value => value + 1)}>重试实例与运行详情</button></p>}
      {loading && <p>正在读取实例与运行记录…</p>}
      {!loading && !error && !instances.length && <p>没有实例注册，不能判断服务已就绪。</p>}
      <div className="monitor-service-grid">{instances.map(item => <article key={item.id}><strong>{kinds[item.kind] || item.kind}</strong>
        <small>节点：{item.node || "未上报"} · 故障域：{item.fault_domain || "未上报"}</small><small>版本：{item.release || "未上报"}</small>
        <span>进程存活：{state(item.live)}</span><span data-state={item.ready && !item.stale ? "RECOVERED" : "FIRING"}>准入就绪：{state(item.ready)}{item.stale ? " · 心跳过期" : ""}</span>
        <small>{new Date(item.heartbeat_at).toLocaleString("zh-CN")}</small></article>)}</div>
    </section>
    <section className="monitor-panel"><h2>运行趋势</h2><p>按真实运行创建时间分桶，显示当前成功/失败状态；空桶没有运行。与CPU采样曲线来源不同。</p>
      {!runs.length ? <p>所选范围暂无运行记录</p> : <div className="monitor-run-bars" role="list" aria-label="运行趋势桶">
        {runs.map(row => <div role="listitem" key={row.time} title={`${new Date(row.time).toLocaleString("zh-CN")}：发起${row.started}、成功${row.succeeded}、失败${row.failed}`}>
          <span style={{ height: `${Math.max(3, Number(row.started) / peak * 100)}%` }} /><small>{row.started}</small></div>)}</div>}
    </section>
    <section className="monitor-panel"><h2>最近错误运行</h2><p>所选时间范围最近50条；他人运行仅显示脱敏状态，详情仍受原有归属权限控制。</p>
      {!failures.length ? <p>暂无可展示的错误运行</p> : failures.map(row => <article className="monitor-failure" key={row.id}>
        <strong>{kinds[row.mode] || row.mode} · {row.error_code}</strong><small>{new Date(row.created_at).toLocaleString("zh-CN")} · {row.id}</small>
        {row.can_open ? row.mode === "workflow" ? <WorkflowFailure row={row} /> : <>
          <button onClick={() => setOpened(opened === row.id ? null : row.id)}>{opened === row.id ? "收起运行详情" : "查看运行详情"}</button>
          {opened === row.id && <RunDiagnostics id={row.id} mode={row.mode} active={false} />}</> : <p>仅显示系统状态；无权查看此运行正文或工具输出。</p>}
      </article>)}
    </section>
  </>;
}
