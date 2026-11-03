"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { Bell, CalendarClock, Layers, Plus, X } from "lucide-react";
import { api } from "@/lib/api/platform";
import { NotificationChannels } from "./notification-channels";
import "./automations.css";
import "./resource-list.css";
import { usePagedResources } from "./use-paged-resources";

type Choice = { id: string; name: string; version: number };
type Schedule = {
  id: string;
  name: string;
  goal: string;
  agent_version_id: string;
  mode: string;
  kind: string;
  timezone: string;
  starts_at: string;
  interval_seconds: number | null;
  cron: string;
  next_due_at: string | null;
  enabled: boolean;
  completed_at: string | null;
  revision: number;
  last_error: string | null;
  budget: { model_calls: number; token_limit: number };
  environment: string;
  network_domains: string[];
  missed_policy: string;
  overlap_policy: string;
  notify_channel_ids: string[];
};
type Item = {
  id: string;
  position: number;
  goal: string;
  status: string;
  error_code: string | null;
  target_id: string | null;
};
type Batch = {
  id: string;
  name: string;
  mode: string;
  status: string;
  items: Item[];
  last_error: string | null;
};
type Notice = {
  id: string;
  title: string;
  message: string;
  target_kind: string;
  target_id: string | null;
  read_at: string | null;
  created_at: string;
};
type Occurrence = {
  id: string;
  scheduled_for: string;
  status: string;
  result_status: string | null;
  target_id: string | null;
  error_code: string | null;
};
const labels: Record<string, string> = {
  ACTIVE: "执行中",
  PAUSED: "已暂停",
  COMPLETED: "已结束",
  CANCELLED: "已取消",
  RUNNING: "执行中",
  WAITING: "待执行",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  UNKNOWN: "结果未知，需核对",
  DISPATCHED: "已入队",
  SKIPPED_MISSED: "已跳过错过时刻",
  SKIPPED_BUSY: "已有任务，已跳过",
  REJECTED: "配置或权限已失效",
};
const localTime = () => {
  const d = new Date(Date.now() + 3600000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
};

export function Automations({
  canManage,
  canExecute,
  canNotify,
  canManageNotifications,
  isAdmin,
  onOpen,
}: {
  canManage: boolean;
  canExecute: boolean;
  canNotify: boolean;
  canManageNotifications: boolean;
  isAdmin: boolean;
  onOpen: (kind: string, id: string | null) => void;
}) {
  const [tab, setTab] = useState("定时任务");
  const [query, setQuery] = useState("");
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const batchQuery = tab === "批量执行" ? query : "";
  const { items: batches, setItems: setBatches, cursor: batchCursor, loading: batchLoading,
    error: batchError, more: moreBatches, reload: reloadBatches } = usePagedResources<Batch>("/batches", batchQuery);
  const [notices, setNotices] = useState<Notice[]>([]);
  const [unread, setUnread] = useState(0);
  const [choices, setChoices] = useState<Choice[]>([]);
  const [channels, setChannels] = useState<{ id: string; name: string }[]>([]);
  const [notifyIds, setNotifyIds] = useState<string[]>([]);
  const [eventCredential, setEventCredential] = useState<{
    event_key: string;
    event_path: string;
  } | null>(null);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const [editor, setEditor] = useState<"schedule" | "batch" | null>(null);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("");
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("ask");
  const [kind, setKind] = useState("once");
  const [timezone, setTimezone] = useState(
    Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
  );
  const [start, setStart] = useState(localTime);
  const [interval, setIntervalSeconds] = useState(3600);
  const [cron, setCron] = useState("0 9 * * 1-5");
  const [calls, setCalls] = useState(12);
  const [tokens, setTokens] = useState(64000);
  const [batchCalls, setBatchCalls] = useState(60);
  const [batchTokens, setBatchTokens] = useState(128000);
  const [missed, setMissed] = useState("skip");
  const [overlap, setOverlap] = useState("skip");
  const [environment, setEnvironment] = useState("coding");
  const [domains, setDomains] = useState("");
  const [preview, setPreview] = useState<string[]>([]);
  const [history, setHistory] = useState<{
    row: Schedule;
    items: Occurrence[];
    next_cursor?: string | null;
  } | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    const [scheduled, grouped, options, availableChannels] = await Promise.all([
      api<{ items: Schedule[] }>("/automations", { signal }),
      api<{ items: Batch[] }>("/batches" + (batchQuery.trim() ? "?q=" + encodeURIComponent(batchQuery.trim()) : ""), { signal }),
      api<{ items: Choice[] }>("/automations/targets"),
      api<{ items: { id: string; name: string }[] }>(
        "/automations/channel-options",
      ),
    ]);
    if (signal?.aborted) return;
    setSchedules(scheduled.items);
    setBatches((current) => [...grouped.items, ...current.filter((item) => !grouped.items.some((next) => next.id === item.id))]);
    setChoices(options.items);
    setChannels(availableChannels.items);
    if (canNotify) {
      const result = await api<{ items: Notice[]; unread: number }>(
        "/notifications",
      );
      setNotices(result.items);
      setUnread(result.unread);
    }
  }, [canNotify, batchQuery, setBatches]);
  useEffect(() => {
    let active = true;
    const controller = new AbortController();
    const refresh = () => {
      if (active) void load(controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); });
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      active = false; controller.abort();
      window.clearInterval(timer);
    };
  }, [load]);
  const action = async (operation: () => Promise<unknown>) => {
    setPending(true);
    setError("");
    try {
      await operation();
      await load();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };
  function openEditor(type: "schedule" | "batch", row: Schedule | null = null) {
    setEditor(type);
    setEditing(row);
    setName(row?.name || "");
    setVersion(row?.agent_version_id || choices[0]?.id || "");
    setGoal(row?.goal || "");
    setMode(row?.mode || "ask");
    setKind(row?.kind || "once");
    setTimezone(
      row?.timezone ||
        Intl.DateTimeFormat().resolvedOptions().timeZone ||
        "Asia/Shanghai",
    );
    if (row) {
      const point = new Date(row.starts_at);
      const parts = new Intl.DateTimeFormat("sv-SE", {
        timeZone: row.timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hourCycle: "h23",
      }).format(point);
      setStart(parts.replace(" ", "T"));
    } else setStart(localTime());
    setIntervalSeconds(row?.interval_seconds || 3600);
    setCron(row?.cron || "0 9 * * 1-5");
    setCalls(row?.budget.model_calls || 12);
    setTokens(row?.budget.token_limit || 64000);
    setMissed(row?.missed_policy || "skip");
    setOverlap(row?.overlap_policy || "skip");
    setEnvironment(row?.environment || "coding");
    setDomains(row?.network_domains.join("\n") || "");
    setNotifyIds(row?.notify_channel_ids || []);
    setPreview([]);
    setError("");
  }
  function payload() {
    const common = {
      name,
      agent_version_id: version,
      mode,
      budget: { model_calls: calls, token_limit: tokens },
      environment,
      network_domains: domains
        .split(/[,\n]/)
        .map((s) => s.trim())
        .filter(Boolean),
      notify_channel_ids: notifyIds,
    };
    return {
      ...common,
      kind,
      goal,
      timezone,
      starts_at: start,
      interval_seconds: kind === "interval" ? interval : null,
      cron: kind === "cron" ? cron : "",
      missed_policy: missed,
      overlap_policy: overlap,
      ...(editing ? { revision: editing.revision } : {}),
    };
  }
  function submit(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      if (editor === "batch") {
        const data = payload();
        const common = {
          name: data.name,
          agent_version_id: data.agent_version_id,
          mode: data.mode,
          budget: data.budget,
          environment: data.environment,
          network_domains: data.network_domains,
          notify_channel_ids: data.notify_channel_ids,
        };
        await api("/batches", {
          method: "POST",
          body: JSON.stringify({
            ...common,
            goals: goal.split("\n---\n").map((s) => s.trim()),
            model_call_limit: batchCalls,
            token_limit: batchTokens,
          }),
        });
      } else {
        const result = await api<{ event_key?: string; event_path?: string }>(
          editing ? `/automations/${editing.id}` : "/automations",
          {
            method: editing ? "PUT" : "POST",
            body: JSON.stringify(payload()),
          },
        );
        if (result.event_key && result.event_path)
          setEventCredential({
            event_key: result.event_key,
            event_path: result.event_path,
          });
      }
      setEditor(null);
    });
  }

  return (
    <section className="automatic-page">
      <header className="automatic-heading">
        <div>
          <h1>自动任务</h1>
          <p>让已发布的智能体按时工作，或一次处理多个独立目标。</p>
        </div>
        {canManage && canExecute && tab !== "通知" && (
          <button
            className="automatic-primary"
            onClick={() =>
              openEditor(tab === "批量执行" ? "batch" : "schedule")
            }
          >
            <Plus size={16} />
            {tab === "批量执行" ? "新建批次" : "新建任务"}
          </button>
        )}
      </header>
      <nav
        className="automatic-tabs"
        aria-label="自动任务分类"
      >
        {["定时任务", "批量执行", ...(canNotify ? ["通知"] : [])].map(
          (label, i) => (
            <button
              key={label}
              aria-pressed={tab === label}
              onClick={() => setTab(label)}
            >
              {i === 0 ? (
                <CalendarClock size={16} />
              ) : i === 1 ? (
                <Layers size={16} />
              ) : (
                <Bell size={16} />
              )}
              {label}
              {label === "通知" && unread > 0 ? ` · ${unread}` : ""}
            </button>
          ),
        )}
      </nav>
      {batchError && <p role="alert">{batchError}<button onClick={() => void (batchCursor ? moreBatches() : reloadBatches()).catch(() => {})}>重试加载批次</button></p>}
      {tab !== "通知" && <label className="resource-list-search"><input aria-label="搜索自动任务" placeholder="搜索任务名称或目标" disabled={pending} value={query} onChange={(event) => setQuery(event.target.value)}/><span>已加载{tab === "定时任务" ? schedules.length : batches.length}项</span></label>}
      {eventCredential && (
        <article
          className="automatic-event-key"
          role="status"
        >
          <h2>事件签名密钥（仅显示这一次）</h2>
          <p>
            请保存到调用端的密钥管理中，关闭后无法再次查看。不要放进问题或智能体提示词。
          </p>
          <code>{eventCredential.event_path}</code>
          <code>{eventCredential.event_key}</code>
          <p>
            请求头 X-Agent-Timestamp 为当前秒数；X-Agent-Signature 为
            sha256=HMAC-SHA256(密钥, 时间戳 + "." + 原始 JSON)。正文包含唯一
            event_id 和 data，时钟允许相差五分钟。
          </p>
          <button onClick={() => setEventCredential(null)}>
            我已保存，关闭显示
          </button>
        </article>
      )}
      {error && (
        <p
          className="automatic-error"
          role="alert"
        >
          {error}
        </p>
      )}
      {tab === "定时任务" && (
        <div className="automatic-list">
          {schedules.length === 0 && (
            <p className="automatic-empty">
              还没有定时任务。先发布一个智能体，再为它安排工作。
            </p>
          )}
          {schedules.length > 0 && !schedules.some((row) => `${row.name} ${row.goal}`.toLowerCase().includes(query.trim().toLowerCase())) && <p className="automatic-empty">没有匹配的任务。</p>}
          {schedules.filter((row) => `${row.name} ${row.goal}`.toLowerCase().includes(query.trim().toLowerCase())).map((row) => (
            <article key={row.id}>
              <div className="automatic-row">
                <div>
                  <h2>{row.name}</h2>
                  <p>
                    {row.mode === "ask" ? "只读对话" : "工作任务"} ·{" "}
                    {row.kind === "once"
                      ? "一次性"
                      : row.kind === "interval"
                        ? `每 ${row.interval_seconds} 秒`
                        : row.cron}{" "}
                    · {row.timezone}
                  </p>
                </div>
                <span className="automatic-status">
                  {row.completed_at
                    ? "已结束"
                    : row.enabled
                      ? "已启用"
                      : "已暂停"}
                </span>
              </div>
              <p className="automatic-goal">{row.goal}</p>
              <p>
                下次执行：
                {row.next_due_at
                  ? new Date(row.next_due_at).toLocaleString("zh-CN", {
                      timeZone: row.timezone,
                    })
                  : "无"}
              </p>
              {row.last_error && (
                <p role="status">需要处理：{row.last_error}</p>
              )}
              <footer>
                {canManage && (
                  <>
                    <button
                      disabled={
                        pending ||
                        !!row.completed_at ||
                        (!row.enabled && !canExecute)
                      }
                      onClick={() =>
                        void action(() =>
                          api(`/automations/${row.id}/state`, {
                            method: "PUT",
                            body: JSON.stringify({ enabled: !row.enabled }),
                          }),
                        )
                      }
                    >
                      {row.enabled ? "暂停并取消在途" : "启用"}
                    </button>
                    <button
                      disabled={!!row.completed_at}
                      onClick={() => openEditor("schedule", row)}
                    >
                      编辑
                    </button>
                    <button
                      onClick={() => {
                        if (
                          window.confirm(
                            "删除此计划并取消在途请求？执行历史会保留。",
                          )
                        )
                          void action(() =>
                            api(`/automations/${row.id}`, { method: "DELETE" }),
                          );
                      }}
                    >
                      删除计划
                    </button>
                  </>
                )}
                <button
                  onClick={() =>
                    void action(async () => {
                      const result = await api<{ items: Occurrence[]; next_cursor?: string | null }>(
                        `/automations/${row.id}/occurrences`,
                      );
                      setHistory({ row, items: result.items, next_cursor: result.next_cursor });
                    })
                  }
                >
                  执行记录
                </button>
              </footer>
            </article>
          ))}
        </div>
      )}
      {tab === "批量执行" && (
        <div className="automatic-list">
          {batches.length === 0 && (
            <p className="automatic-empty">
              每批最多 20 个目标，共享总预算；失败默认暂停，未知结果须核对。
            </p>
          )}
          {batches.length > 0 && !batches.some((row) => row.name.toLowerCase().includes(query.trim().toLowerCase())) && <p className="automatic-empty">没有匹配的批次。</p>}
          {batches.filter((row) => row.name.toLowerCase().includes(query.trim().toLowerCase())).map((row) => (
            <article key={row.id}>
              <div className="automatic-row">
                <h2>{row.name}</h2>
                <span className="automatic-status">
                  {labels[row.status] || row.status}
                </span>
              </div>
              {row.last_error && <p>需要处理：{row.last_error}</p>}
              <ol>
                {row.items.map((item) => (
                  <li key={item.id}>
                    <span>{item.goal}</span>
                    <span>{labels[item.status] || item.status}</span>
                    {item.target_id && (
                      <button
                        onClick={() =>
                          onOpen(
                            row.mode === "ask" ? "chat" : "work",
                            item.target_id,
                          )
                        }
                      >
                        查看任务
                      </button>
                    )}
                  </li>
                ))}
              </ol>
              {canManage &&
                !["COMPLETED", "CANCELLED"].includes(row.status) && (
                  <footer>
                    {row.status === "ACTIVE" ? (
                      <button
                        disabled={pending}
                        onClick={() =>
                          void action(() =>
                            api(`/batches/${row.id}/action`, {
                              method: "POST",
                              body: JSON.stringify({ action: "pause" }),
                            }),
                          )
                        }
                      >
                        暂停后续项目
                      </button>
                    ) : (
                      <button
                        disabled={pending || !canExecute}
                        onClick={() => {
                          const unknown = row.items.some(
                            (i) => i.status === "UNKNOWN",
                          );
                          if (
                            unknown &&
                            !window.confirm(
                              "请先核对已发生的未知请求。继续只执行剩余未开始项目，不重试旧请求。确认已核对？",
                            )
                          )
                            return;
                          void action(() =>
                            api(`/batches/${row.id}/action`, {
                              method: "POST",
                              body: JSON.stringify({
                                action: "resume",
                                acknowledge_unknown: unknown,
                              }),
                            }),
                          );
                        }}
                      >
                        继续剩余项目
                      </button>
                    )}
                    <button
                      disabled={pending}
                      onClick={() => {
                        if (
                          window.confirm(
                            "取消批次的剩余项目并请求停止在途任务？",
                          )
                        )
                          void action(() =>
                            api(`/batches/${row.id}/action`, {
                              method: "POST",
                              body: JSON.stringify({ action: "cancel" }),
                            }),
                          );
                      }}
                    >
                      取消批次
                    </button>
                  </footer>
                )}
            </article>
          ))}
        </div>
      )}
      {tab === "批量执行" && batchCursor && <button disabled={batchLoading} onClick={() => void moreBatches().catch(() => {})}>加载更多批次</button>}
      {tab === "通知" && (
        <>
          <button
            disabled={pending || !unread}
            onClick={() =>
              void action(() =>
                api("/notifications/read-all", { method: "POST" }),
              )
            }
          >
            全部标为已读
          </button>
          <div className="automatic-list">
            {!notices.length && (
              <p className="automatic-empty">
                任务完成、失败或需要处理时，会在这里通知你。
              </p>
            )}
            {notices.map((notice) => (
              <article
                key={notice.id}
                className={!notice.read_at ? "automatic-unread" : ""}
              >
                <h2>{notice.title}</h2>
                <p>{notice.message}</p>
                <time>
                  {new Date(notice.created_at).toLocaleString("zh-CN")}
                </time>
                <footer>
                  {!notice.read_at && (
                    <button
                      onClick={() =>
                        void action(() =>
                          api(`/notifications/${notice.id}/read`, {
                            method: "POST",
                          }),
                        )
                      }
                    >
                      标为已读
                    </button>
                  )}
                  <button
                    onClick={() =>
                      notice.target_kind === "batch"
                        ? setTab("批量执行")
                        : onOpen(notice.target_kind, notice.target_id)
                    }
                  >
                    {notice.target_kind === "monitoring" ? "查看告警" : notice.target_kind === "work"
                      ? "前往任务工作台"
                      : "查看任务"}
                  </button>
                </footer>
              </article>
            ))}
          </div>
          <NotificationChannels isAdmin={isAdmin} canManage={canManageNotifications} />
        </>
      )}
      {history && (
        <div className="automatic-overlay">
          <section
            role="dialog"
            aria-modal="true"
            aria-labelledby="automatic-history-title"
            className="automatic-dialog"
          >
            <header>
              <h2 id="automatic-history-title">
                {history.row.name} · 执行记录
              </h2>
              <button
                aria-label="关闭记录"
                onClick={() => setHistory(null)}
              >
                <X size={18} />
              </button>
            </header>
            {!history.items.length && <p>尚未触发</p>}
            {history.next_cursor && <button disabled={pending} onClick={() => {
              const current = history;
              setPending(true); setError("");
              void api<{ items: Occurrence[]; next_cursor?: string | null }>(`/automations/${current.row.id}/occurrences?before=${encodeURIComponent(current.next_cursor!)}`)
                .then((result) => setHistory((value) => value?.row.id === current.row.id ? { ...value,
                  items: [...value.items, ...result.items.filter((item) => !value.items.some((old) => old.id === item.id))], next_cursor: result.next_cursor } : value))
                .catch((reason) => setError(reason.message)).finally(() => setPending(false));
            }}>加载更早触发记录</button>}
            {history.items.map((item) => (
              <article key={item.id}>
                <p>
                  {new Date(item.scheduled_for).toLocaleString("zh-CN")} ·{" "}
                  {labels[item.result_status || item.status] || item.status}
                </p>
                {item.error_code && <p>{item.error_code}</p>}
                {item.target_id && (
                  <button
                    onClick={() =>
                      onOpen(
                        history.row.mode === "ask" ? "chat" : "work",
                        item.target_id,
                      )
                    }
                  >
                    查看任务
                  </button>
                )}
              </article>
            ))}
          </section>
        </div>
      )}
      {editor && (
        <div className="automatic-overlay">
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="automatic-editor-title"
            className="automatic-dialog"
            onSubmit={submit}
          >
            <header>
              <h2 id="automatic-editor-title">
                {editing
                  ? "编辑任务"
                  : editor === "batch"
                    ? "新建批次"
                    : "新建定时任务"}
              </h2>
              <button
                type="button"
                aria-label="关闭编辑"
                onClick={() => setEditor(null)}
              >
                <X size={18} />
              </button>
            </header>
            <p>
              使用固定发布版本；新计划保存后默认暂停，启用后才会产生模型消耗。
            </p>
            <label>
              名称
              <input
                required
                maxLength={120}
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              已发布智能体
              <select
                required
                value={version}
                onChange={(e) => setVersion(e.target.value)}
              >
                <option value="">请选择</option>
                {choices.map((choice) => (
                  <option
                    key={choice.id}
                    value={choice.id}
                  >
                    {choice.name} · 版本 {choice.version}
                  </option>
                ))}
              </select>
            </label>
            <label>
              执行方式
              <select
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                <option value="ask">只读对话</option>
                <option value="work">工作任务（写入和命令仍需审批）</option>
              </select>
            </label>
            <label>
              {editor === "batch"
                ? "各项目标（用单独一行 --- 分隔）"
                : "任务目标"}
              <textarea
                required
                rows={4}
                maxLength={editor === "batch" ? 160000 : 8000}
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
              />
            </label>
            {editor === "schedule" && (
              <>
                <label>
                  触发方式
                  <select
                    disabled={!!editing}
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                  >
                    <option value="once">一次性</option>
                    <option value="interval">固定间隔</option>
                    <option value="cron">Cron</option>
                    <option value="webhook">签名事件触发</option>
                  </select>
                </label>
                <div className="automatic-grid">
                  <label>
                    任务时区
                    <input
                      required
                      value={timezone}
                      onChange={(e) => setTimezone(e.target.value)}
                      placeholder="Asia/Shanghai"
                    />
                  </label>
                  <label>
                    首次执行时间
                    <input
                      required
                      type="datetime-local"
                      value={start}
                      onChange={(e) => setStart(e.target.value)}
                    />
                  </label>
                </div>
                <p>时间按上方任务时区解释。</p>
                {kind === "interval" && (
                  <label>
                    执行间隔（秒，至少 60）
                    <input
                      required
                      type="number"
                      min={60}
                      value={interval}
                      onChange={(e) =>
                        setIntervalSeconds(Number(e.target.value))
                      }
                    />
                  </label>
                )}
                {kind === "cron" && (
                  <label>
                    五字段 Cron
                    <input
                      required
                      value={cron}
                      onChange={(e) => setCron(e.target.value)}
                    />
                    <small>
                      分 时 日 月 星期；例如 0 9 * *
                      1-5。夏令时缺失时刻跳过，重复时刻只执行一次。
                    </small>
                  </label>
                )}
                <div className="automatic-grid">
                  <label>
                    错过执行时刻
                    <select
                      value={missed}
                      onChange={(e) => setMissed(e.target.value)}
                    >
                      <option value="skip">跳过（超过一分钟）</option>
                      <option value="once">补一次</option>
                    </select>
                  </label>
                  <label>
                    上一次仍在执行
                    <select
                      value={overlap}
                      onChange={(e) => setOverlap(e.target.value)}
                    >
                      <option value="skip">跳过本次</option>
                      <option value="queue">保留一个待执行时刻</option>
                    </select>
                  </label>
                </div>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() =>
                    void action(async () =>
                      setPreview(
                        (
                          await api<{ items: string[] }>(
                            "/automations/preview",
                            { method: "POST", body: JSON.stringify(payload()) },
                          )
                        ).items,
                      ),
                    )
                  }
                >
                  预览执行时间
                </button>
                {preview.length > 0 && (
                  <ul aria-label="执行时间预览">
                    {preview.map((point) => (
                      <li key={point}>
                        {new Date(point).toLocaleString("zh-CN", {
                          timeZone: timezone,
                        })}
                        （{timezone}）
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
            {mode === "work" && (
              <>
                <label>
                  工作环境
                  <select
                    value={environment}
                    onChange={(e) => setEnvironment(e.target.value)}
                  >
                    <option value="coding">编程和文件</option>
                    <option value="browser">浏览器</option>
                  </select>
                </label>
                <label>
                  授权访问的域名（每行一个，留空禁止出网）
                  <textarea
                    value={domains}
                    onChange={(e) => setDomains(e.target.value)}
                  />
                </label>
              </>
            )}
            <div className="automatic-grid">
              <label>
                单项最多模型调用
                <input
                  required
                  type="number"
                  min={1}
                  max={mode === "ask" ? 12 : 40}
                  value={calls}
                  onChange={(e) => setCalls(Number(e.target.value))}
                />
              </label>
              <label>
                单项 Token 预算
                <input
                  required
                  type="number"
                  min={1024}
                  max={mode === "ask" ? 64000 : 80000}
                  value={tokens}
                  onChange={(e) => setTokens(Number(e.target.value))}
                />
              </label>
            </div>
            <fieldset>
              <legend>通知渠道（可选）</legend>
              <p>站内通知默认开启。外部渠道仅发送任务名称和状态。</p>
              {channels.length === 0 && <p>暂无可用通道，可在“通知”页配置。</p>}
              {channels.map((channel) => (
                <label
                  className="automatic-check"
                  key={channel.id}
                >
                  <input
                    type="checkbox"
                    checked={notifyIds.includes(channel.id)}
                    onChange={(e) =>
                      setNotifyIds(
                        e.target.checked
                          ? [...notifyIds, channel.id]
                          : notifyIds.filter((id) => id !== channel.id),
                      )
                    }
                  />
                  {channel.name}
                </label>
              ))}
            </fieldset>
            {editor === "batch" && (
              <>
                <p>最多 20 项，同时最多 2 项；失败默认暂停。创建后开始执行。</p>
                <div className="automatic-grid">
                  <label>
                    批次总调用上限
                    <input
                      required
                      type="number"
                      min={1}
                      max={800}
                      value={batchCalls}
                      onChange={(e) => setBatchCalls(Number(e.target.value))}
                    />
                  </label>
                  <label>
                    批次总 Token 预算
                    <input
                      required
                      type="number"
                      min={1024}
                      max={1600000}
                      value={batchTokens}
                      onChange={(e) => setBatchTokens(Number(e.target.value))}
                    />
                  </label>
                </div>
              </>
            )}
            {error && (
              <p
                role="alert"
                className="automatic-error"
              >
                {error}
              </p>
            )}
            <footer>
              <button
                type="button"
                onClick={() => setEditor(null)}
              >
                取消
              </button>
              <button
                className="automatic-primary"
                disabled={pending || !version}
              >
                {pending
                  ? "正在保存…"
                  : editor === "batch"
                    ? "创建并执行"
                    : "保存任务"}
              </button>
            </footer>
          </form>
        </div>
      )}
    </section>
  );
}
