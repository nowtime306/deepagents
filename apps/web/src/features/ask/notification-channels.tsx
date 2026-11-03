"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { ConfigurationHistory } from "../settings/configuration-history";
import { api } from "@/lib/api/platform";
import "./resource-list.css";

type Channel = {
  id: string;
  name: string;
  kind: string;
  host_hint: string;
  has_secret: boolean;
  enabled: boolean;
  allow_private: boolean;
  revision: number;
};
type TargetSummary = { kind: string; host: string; identity: string | null };
function targetText(target?: TargetSummary | null) {
  return target?.identity
    ? `${names[target.kind] || target.kind} · ${target.host} · 目标标识 ${target.identity.slice(0, 12)}`
    : "历史未记录接收目标";
}
const deliveryErrors: Record<string, string> = {
  CHANNEL_TARGET_CHANGED_REVIEW_REQUIRED: "接收目标已变化，已暂停。请对比目标并确认后重新投递。",
  DELIVERY_HISTORY_MISSING: "历史未记录接收目标或消息内容，请创建新通知并确认。",
  DELIVERY_LEASE_EXPIRED: "本次投递结果未知，请先核对收件情况。",
  CHANNEL_UNAVAILABLE: "通道不存在或已停用，请核对通道状态。",
  NOTIFICATION_PERMISSION_REVOKED: "通知权限已撤销，请联系管理员核对。",
  CHANNEL_PRIVATE_PERMISSION_REVOKED: "内网通知授权已撤销，请联系管理员核对。",
};
type Delivery = {
  id: string;
  channel_name: string | null;
  status: string;
  attempts: number;
  error_code: string | null;
  created_at: string;
  retry_of?: string | null;
  target_summary?: TargetSummary | null;
  current_target_summary?: TargetSummary | null;
};
const names: Record<string, string> = {
  webhook: "签名 Webhook",
  feishu: "飞书 / Lark",
  wecom: "企业微信",
  slack: "Slack",
};
const states: Record<string, string> = {
  PENDING: "待投递",
  SENDING: "正在投递",
  SUCCEEDED: "渠道已接收",
  FAILED: "未成功",
  UNKNOWN: "结果未知，需核对",
  CANCELLED: "已取消",
  PAUSED: "已暂停，待确认接收目标",
};

export function NotificationChannels({ isAdmin, canManage }: { isAdmin: boolean; canManage: boolean }) {
  const [query, setQuery] = useState("");
  const [channels, setChannels] = useState<Channel[]>([]);
  const [deliveries, setDeliveries] = useState<Delivery[]>([]);
  const [editing, setEditing] = useState<Channel | null | undefined>(undefined);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("webhook");
  const [endpoint, setEndpoint] = useState("");
  const [secret, setSecret] = useState("");
  const [privateAllowed, setPrivateAllowed] = useState(false);
  const [clear, setClear] = useState(false);
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const load = useCallback(async () => {
    const [c, d] = await Promise.all([
      api<{ items: Channel[] }>("/notification-channels"),
      api<{ items: Delivery[] }>("/notifications/deliveries"),
    ]);
    setChannels(c.items);
    setDeliveries(d.items);
  }, []);
  useEffect(() => {
    const refresh = () => void load().catch((e: Error) => setError(e.message));
    refresh();
    const timer = window.setInterval(
      () => void load().catch((e: Error) => setError(e.message)),
      5000,
    );
    return () => window.clearInterval(timer);
  }, [load]);
  async function act(operation: () => Promise<unknown>) {
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
  }
  function edit(row: Channel | null) {
    setEditing(row);
    setName(row?.name || "");
    setKind(row?.kind || "webhook");
    setPrivateAllowed(row?.allow_private || false);
    setEndpoint("");
    setSecret("");
    setClear(false);
  }
  function submit(e: FormEvent) {
    e.preventDefault();
    void act(async () => {
      await api(
        editing
          ? `/notification-channels/${editing.id}`
          : "/notification-channels",
        {
          method: editing ? "PUT" : "POST",
          body: JSON.stringify({
            name,
            kind,
            endpoint: endpoint || undefined,
            secret: secret || undefined,
            clear_secret: clear,
            allow_private: privateAllowed,
            enabled: true,
            revision: editing?.revision,
          }),
        },
      );
      setEditing(undefined);
      setSecret("");
      setEndpoint("");
    });
  }
  return (
    <section className="automatic-channel-section">
      <header className="resource-list-heading"><h2>消息通道</h2>{canManage && <button className="settings-primary" onClick={() => edit(null)}>添加通知通道</button>}</header>
      <p>
        仅向你选中的渠道发送任务名称和状态。地址与密钥加密保存；不会发送问题、回答或文档。新建通道不会发送测试消息。
      </p>
      <label className="resource-list-search"><input aria-label="搜索通知通道" placeholder="搜索通道名称" value={query} onChange={(e) => setQuery(e.target.value)}/><span>{channels.length}个通道</span></label>
      {!channels.length && <p>还没有通知通道。</p>}
      {error && (
        <p
          role="alert"
          className="automatic-error"
        >
          {error}
        </p>
      )}
      <div className="automatic-list">
      {channels.length > 0 && !channels.some((item) => item.name.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的记录。</p>}
        {channels.filter((row) => row.name.toLowerCase().includes(query.toLowerCase())).map((row) => (
          <article key={row.id}>
            <div className="automatic-row">
              <h3>{row.name}</h3>
              <span>{row.enabled ? "已启用" : "已停用"}</span>
            </div>
            <p>
              {names[row.kind]} · {row.host_hint}
              {row.allow_private ? " · 已授权内网" : ""}
            </p>
            {canManage && <footer>
              <button onClick={() => edit(row)}>编辑通道</button>
              {row.enabled && (
                <button
                  disabled={pending}
                  onClick={() =>
                    void act(() =>
                      api(`/notification-channels/${row.id}`, {
                        method: "DELETE",
                      }),
                    )
                  }
                >
                  停用
                </button>
              )}
            </footer>}
            <ConfigurationHistory kind="notification" resourceId={row.id} name={row.name}/>
          </article>
        ))}
      </div>
      <h2 className="automatic-channel-title">投递记录</h2>
      <p>
        “渠道已接收”表示接口接受，不代表收件人已经阅读。结果未知不会自动重发。
      </p>
      <div className="automatic-list">
        {deliveries.map((row) => (
          <article key={row.id} id={`notification-delivery-${row.id}`}>
            <div className="automatic-row">
              <h3>{row.channel_name || "通道已移除"}</h3>
              <span>{states[row.status] || row.status}</span>
            </div>
            <p>
              {new Date(row.created_at).toLocaleString("zh-CN")} · 已尝试{" "}
              {row.attempts} 次
            </p>
            <p>原接收目标：{targetText(row.target_summary)}</p>
            {row.target_summary?.identity !== row.current_target_summary?.identity &&
              <p>当前接收目标：{targetText(row.current_target_summary)}</p>}
            {row.retry_of && <p>重新投递自：{deliveries.some((item) => item.id === row.retry_of) ?
              <a href={`#notification-delivery-${row.retry_of}`}>{row.retry_of}</a> : row.retry_of}</p>}
            {row.error_code && <p>{deliveryErrors[row.error_code] || "投递未确认完成，请核对通道状态及收件情况。"}</p>}
            {canManage && ["FAILED", "UNKNOWN", "PAUSED"].includes(row.status) && (
              <button
                disabled={pending || !row.target_summary?.identity || !row.current_target_summary?.identity}
                onClick={() => {
                  const changed = row.target_summary?.identity !== row.current_target_summary?.identity;
                  if (window.confirm(
                    `原接收目标：${targetText(row.target_summary)}\n当前接收目标：${targetText(row.current_target_summary)}\n${changed ? "确认将冻结的原消息重新投递到以上当前接收目标？" : "确认向以上原接收目标重新投递？"}结果未知可能已送达，重新投递可能重复。原记录会保留。`,
                  ))
                    void act(() =>
                      api(`/notifications/deliveries/${row.id}/retry`, {
                        method: "POST",
                        body: JSON.stringify({ acknowledge_duplicate: true, confirm_target: changed,
                          expected_target: row.current_target_summary?.identity }),
                      }),
                    );
                }}
              >
                {row.target_summary?.identity && row.current_target_summary?.identity ? "核对目标并重新投递" : "历史未记录，无法自动重发"}
              </button>
            )}
          </article>
        ))}
      </div>
      {canManage && editing !== undefined && (
        <div className="automatic-overlay">
          <form
            role="dialog"
            aria-modal="true"
            aria-labelledby="channel-title"
            className="automatic-dialog"
            onSubmit={submit}
          >
            <h2 id="channel-title">
              {editing ? "编辑通知通道" : "添加通知通道"}
            </h2>
            <label>
              通道名称
              <input
                required
                value={name}
                maxLength={120}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              渠道类型
              <select
                disabled={!!editing}
                value={kind}
                onChange={(e) => setKind(e.target.value)}
              >
                {Object.entries(names).map(([value, label]) => (
                  <option
                    key={value}
                    value={value}
                  >
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Webhook 地址
              <input
                required={!editing}
                type="password"
                autoComplete="new-password"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                placeholder={editing ? "已保存，留空保持不变" : "https://…"}
              />
            </label>
            {["webhook", "feishu"].includes(kind) && (
              <>
                <label>
                  签名密钥
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={secret}
                    onChange={(e) => setSecret(e.target.value)}
                    placeholder={
                      editing?.has_secret
                        ? "已保存，留空保持不变"
                        : kind === "webhook"
                          ? "必填，与接收器约定的密钥"
                          : "按机器人安全设置填写"
                    }
                  />
                </label>
                {editing && kind !== "webhook" && (
                  <label className="automatic-check">
                    <input
                      type="checkbox"
                      checked={clear}
                      onChange={(e) => setClear(e.target.checked)}
                    />
                    清除已有签名密钥
                  </label>
                )}
              </>
            )}
            {isAdmin && (
              <label className="automatic-check">
                <input
                  type="checkbox"
                  checked={privateAllowed}
                  onChange={(e) => setPrivateAllowed(e.target.checked)}
                />
                明确允许访问内网通知地址（仍禁止管理及元数据地址）
              </label>
            )}
            <p>保存变更后，旧的待投递消息不会自动改投新地址，需核对后重发。</p>
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
                onClick={() => {
                  setEditing(undefined);
                  setSecret("");
                  setEndpoint("");
                }}
              >
                取消
              </button>
              <button
                disabled={pending}
                className="automatic-primary"
              >
                保存通道
              </button>
            </footer>
          </form>
        </div>
      )}
    </section>
  );
}
