"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./repositories.css";

type Data = { services: { kind: string; online: number; last_heartbeat: string }[];
  events: { kind: string; event: string; reason: string | null; created_at: string }[]; note: string };
const labels: Record<string, string> = { api: "网页服务", ask: "对话后台", work: "任务后台", ops: "备份运维后台" };
const events: Record<string, string> = { started: "已启动", stopped: "已停止", fenced: "已保护性停止" };

export function ServiceStatus() {
  const [data, setData] = useState<Data | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const result = await api<Data>("/operations/services");
        if (active) { setData(result); setError(""); }
      } catch (reason) { if (active) setError((reason as Error).message); }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { active = false; clearInterval(timer); };
  }, []);
  return <section aria-label="平台后台状态" className="repository-panel service-status-panel" id="ops-services">
    <h2>后台运行状态</h2>
    {error && <p role="status">后台状态暂不可用：{error}</p>}
    {!data && !error && <p>正在读取后台状态…</p>}
    {data && <>
      <p>{data.note}</p>
      <div className="repository-card">{Object.entries(labels).map(([kind, label]) => {
        const row = data.services.find((item) => item.kind === kind);
        return <p key={kind}><strong>{label}</strong><br />
          {error ? "连接正在恢复" : row?.online ? `${row.online}个实例在线` : "未检测到在线实例"}</p>;
      })}</div>
      <details><summary>最近的启动与恢复记录</summary>
        {data.events.map((event, index) => <p key={index}>{labels[event.kind] || "平台服务"} · {events[event.event] || "状态更新"}
          {event.reason === "SERVICE_STOP_REQUESTED" ? "（按要求停止）" : event.event === "fenced" ? "（连接或运行守护失效，已停止旧操作）" : ""}
          <br /><small>{new Date(event.created_at).toLocaleString("zh-CN")}</small></p>)}
        {!data.events.length && <p>暂时没有运行记录。</p>}
      </details>
    </>}
  </section>;
}
