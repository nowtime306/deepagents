"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./repositories.css";
import { ResourceDialog } from "./resource-dialog";

type Settings = {
  enabled: boolean;
  hour: number;
  minute: number;
  retain: number;
  revision: number;
  next_due_at: string | null;
};
type Job = {
  id: string;
  status: string;
  phase: string;
  reason: string | null;
  backup_name: string | null;
  created_at: string;
  verification: { records_match?: boolean; files_verified?: number } | null;
};
type Data = {
  settings: Settings;
  maintenance: { active: boolean; started_at: string | null };
  items: Job[];
};
type Capacity = {
  host: {
    logical_cpus: number;
    system: string;
    disk_free: number;
    disk_total: number;
    memory_total: number | null;
    memory_available: number | null;
    cpu_percent: number | null;
    alerts?: string[];
  };
  queue: { status: string; n: number }[];
  sandboxes: { kind: string; state: string; n: number }[];
  note: string;
};
const phases: Record<string, string> = {
  QUEUED: "等待空闲",
  DEFERRED: "忙碌，已延期",
  STOPPING: "安全停止后台",
  COPYING: "备份数据与源码",
  VERIFYING: "隔离恢复校验",
  RESTARTING: "恢复原有后台",
  FINISHED: "已结束",
  CANCELLED: "已取消",
  ADMISSION: "启动检查",
  RESTORED_REVIEW: "恢复副本，待核对",
};
const states: Record<string, string> = {
  PENDING: "排队",
  RUNNING: "进行中",
  SUCCEEDED: "已通过校验",
  FAILED: "未完成",
  DELETED: "已按保留策略轮转",
};
const reasons: Record<string, string> = {
  SERVICE_CONTROL_PROFILE_MISMATCH:
    "当前站点使用独立数据库、数据目录或端口，固定部署脚本不能控制此站点，备份已在准入阶段停止；未启停其他站点。",
  ACTIVE_TASKS: "有任务、消息投递或人工接管，五分钟后再检查",
  UNMANAGED_SERVICE: "检测到未受本项目守护管理的后台，请核对后启动",
  BACKUP_INTERRUPTED_REVIEW: "上次备份被中断，已恢复原有后台，未自动重做备份",
  ROTATION_REVIEW: "轮转校验未通过，文件已保留",
  CANCELLED_BY_ADMIN: "管理员取消了尚未开始的备份",
  API_RESTART_NOT_READY: "网页后台尚未恢复就绪",
  SERVICES_RESTART_NOT_READY: "部分后台尚未恢复心跳",
  MANUAL_STOP_PRESERVED:
    "备份后检测到新的人工停止要求，已保留对应后台的停止状态",
};
const gb = (n: number | null) =>
  n === null ? "未报告" : `${(n / 1024 ** 3).toFixed(1)} GB`;

export function OperationsBackups() {
  const [data, setData] = useState<Data | null>(null),
    [capacity, setCapacity] = useState<Capacity | null>(null);
  const [form, setForm] = useState<Settings | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function reload() {
    const [d, c] = await Promise.all([
      api<Data>("/operations/backups"),
      api<Capacity>("/operations/capacity"),
    ]);
    setData(d);
    setCapacity(c);
  }
  useEffect(() => {
    let active = true;
    async function load() {
      try {
        const [d, c] = await Promise.all([
          api<Data>("/operations/backups"),
          api<Capacity>("/operations/capacity"),
        ]);
        if (active) {
          setData(d);
          setCapacity(c);
          setError("");
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, []);
  async function act(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save(event: FormEvent) {
    event.preventDefault();
    if (!form) return;
    await act(async () => {
      await api("/operations/backups/schedule", {
        method: "PUT",
        body: JSON.stringify({
          enabled: form.enabled,
          hour: form.hour,
          minute: form.minute,
          retain: form.retain,
          revision: form.revision,
        }),
      });
      setForm(null);
    });
  }
  return (
    <section
      aria-label="备份与容量"
      className="repository-panel phase-eight-settings"
    >
      <h2>备份与容量</h2>
      <p>
        备份包含数据、受保护的凭证和未提交源码。空闲时短暂停止网页与执行后台，完成隔离恢复校验后启动原有实例；有正在执行的任务则延期。
      </p>
      {error && (
        <p role="status">
          连接正在恢复或读取失败：{error}。备份期间请等待后台恢复。
        </p>
      )}
      {data?.maintenance.active && (
        <p role="status">正在离线备份，新操作暂时暂停；当前任务不会被重放。</p>
      )}
      {capacity && (
        <div className="repository-card">
          <h3>本机观测</h3>
          {capacity.host.alerts?.map((message) => (
            <p
              role="status"
              key={message}
            >
              {message}
            </p>
          ))}
          <p>
            {capacity.host.system} · {capacity.host.logical_cpus} 逻辑处理器 ·
            CPU{" "}
            {capacity.host.cpu_percent === null
              ? "等待下一次采样"
              : `${capacity.host.cpu_percent}%`}
          </p>
          <p>
            可用内存 {gb(capacity.host.memory_available)} /{" "}
            {gb(capacity.host.memory_total)} · 磁盘剩余{" "}
            {gb(capacity.host.disk_free)} / {gb(capacity.host.disk_total)}
          </p>
          <p>
            排队任务{" "}
            {capacity.queue.find((q) => q.status === "PENDING")?.n || 0} ·
            执行中 {capacity.queue.find((q) => q.status === "RUNNING")?.n || 0}{" "}
            · 等待审批{" "}
            {capacity.queue.find((q) => q.status === "WAITING_APPROVAL")?.n ||
              0}
          </p>
          <p>
            保留中的编程环境{" "}
            {capacity.sandboxes
              .filter((s) => s.kind === "coding")
              .reduce((n, s) => n + s.n, 0)}{" "}
            · 浏览器环境{" "}
            {capacity.sandboxes
              .filter((s) => s.kind === "browser")
              .reduce((n, s) => n + s.n, 0)}
          </p>
          <small>{capacity.note}</small>
        </div>
      )}
      {data && (
        <>
          <p>
            每日备份：
            {data.settings.enabled
              ? `北京时间 ${String(data.settings.hour).padStart(2, "0")}:${String(data.settings.minute).padStart(2, "0")}`
              : "尚未开启"}{" "}
            · 保留最近 {data.settings.retain} 份已登记且校验通过的备份
          </p>
          <button
            disabled={busy || data.maintenance.active}
            onClick={() => setForm(data.settings)}
          >
            配置定时备份
          </button>
          <button
            disabled={
              busy ||
              data.items.some((j) => ["PENDING", "RUNNING"].includes(j.status))
            }
            onClick={() => {
              if (
                window.confirm(
                  "加入离线备份队列？空闲时会短暂停止平台后台并自动恢复；现有任务不会被取消。",
                )
              )
                void act(() =>
                  api("/operations/backups/jobs", { method: "POST" }),
                );
            }}
          >
            立即排队备份
          </button>
          {form && (
            <ResourceDialog title="编辑备份计划" busy={busy} close={() => setForm(null)}>{error && <p role="alert">{error}</p>}<form
              onSubmit={save}
              className="repository-form"
            >
              <label>
                <input
                  type="checkbox"
                  checked={form.enabled}
                  onChange={(e) =>
                    setForm({ ...form, enabled: e.target.checked })
                  }
                />
                开启每日备份
              </label>
              <label>
                北京时间（小时）
                <input
                  type="number"
                  min={0}
                  max={23}
                  required
                  value={form.hour}
                  onChange={(e) =>
                    setForm({ ...form, hour: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                分钟
                <input
                  type="number"
                  min={0}
                  max={59}
                  required
                  value={form.minute}
                  onChange={(e) =>
                    setForm({ ...form, minute: Number(e.target.value) })
                  }
                />
              </label>
              <label>
                保留已验证份数
                <input
                  type="number"
                  min={2}
                  max={30}
                  required
                  value={form.retain}
                  onChange={(e) =>
                    setForm({ ...form, retain: Number(e.target.value) })
                  }
                />
              </label>
              <small>
                至少保留两份。历史手工备份和来源不明的目录不会被清理。
              </small>
              <button disabled={busy}>保存备份设置</button>
              <button
                type="button"
                onClick={() => setForm(null)}
              >
                取消
              </button>
            </form></ResourceDialog>
          )}
          <h3>备份记录</h3>
          {data.items.length ? (
            data.items.map((job) => (
              <article
                className="repository-card"
                key={job.id}
              >
                <strong>
                  {states[job.status] || job.status} ·{" "}
                  {phases[job.phase] || job.phase}
                </strong>
                <p>
                  {new Date(job.created_at).toLocaleString("zh-CN")}
                  {job.backup_name && ` · ${job.backup_name}`}
                </p>
                {job.reason && (
                  <p>
                    {reasons[job.reason] ||
                      `本次未完成：${job.reason}，请查看本项目后台日志后重新排队。`}
                  </p>
                )}
                {job.verification?.records_match && (
                  <p>
                    隔离恢复记录一致，已校验 {job.verification.files_verified}{" "}
                    个文件。
                  </p>
                )}
                {job.status === "PENDING" && (
                  <button
                    disabled={busy}
                    onClick={() =>
                      void act(() =>
                        api(`/operations/backups/jobs/${job.id}`, {
                          method: "DELETE",
                        }),
                      )
                    }
                  >
                    取消排队备份
                  </button>
                )}
              </article>
            ))
          ) : (
            <p>尚无由此调度器登记的备份。已有手工备份会继续保留。</p>
          )}
        </>
      )}
    </section>
  );
}
