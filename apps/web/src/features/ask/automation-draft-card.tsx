"use client";

import { useEffect, useState } from "react";
import { CalendarClock } from "lucide-react";
import { api } from "@/lib/api/platform";
import "./resource-list.css";

type Schedule = { id: string; name: string; enabled: boolean; goal: string; kind: string; timezone: string;
  starts_at: string; cron: string; interval_seconds: number | null };

export function AutomationDraftCard({ id }: { id: string }) {
  const [item, setItem] = useState<Schedule | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api<{ items: Schedule[] }>("/automations").then((data) => {
      if (active) { setItem(data.items.find((row) => row.id === id) || null); setError(data.items.some((row) => row.id === id) ? "" : "任务已删除或无权查看。"); }
    }).catch((reason) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [id]);
  return <section className="automation-chat-card" aria-label="对话自动任务">
    <header><CalendarClock size={18}/><strong>{item?.name || "自动任务草稿"}</strong></header>
    {item && <><p>{item.goal}</p><p>{item.kind === "cron" ? `周期：${item.cron}` : item.kind === "interval" ? `每${item.interval_seconds}秒` : "单次任务"} · {item.timezone}</p>
      <p>开始：{new Date(item.starts_at).toLocaleString("zh-CN", { timeZone: item.timezone })}</p>
      <p role="status">{item.enabled ? "已启用，可在自动任务列表管理。" : "草稿已保存，启用后才会执行。"}</p>
      <button type="button" className="settings-primary" disabled={busy || item.enabled} onClick={async () => {
        setBusy(true); setError("");
        try { await api(`/automations/${id}/state`, { method: "PUT", body: JSON.stringify({ enabled: true }) }); setItem({ ...item, enabled: true }); }
        catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
      }}>{busy ? "正在启用…" : item.enabled ? "已启用" : "启用自动任务"}</button></>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
