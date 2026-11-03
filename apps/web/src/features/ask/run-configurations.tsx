"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ConfigurationHistory } from "@/features/settings/configuration-history";
import "@/features/settings/configuration-history.css";

type Mode = "ask" | "work" | "workflow" | "resource";
type Evidence = {
  status: string; error_code: string | null;
  can_recover: boolean; can_view_global_history: boolean;
  configuration_scope: { mode: string; run_id: string };
  business_version: Record<string, unknown>; note: string;
  stages: { phase: number; created_at: string; confirmed_by: string | null; reason: string }[];
  items: {
    snapshot_id: string; phase: number; resource_kind: string; resource_id: string; revision: string;
    resolved_at: string; used_at: string | null; state: string; available: boolean;
    credential_changed: boolean; snapshot: Record<string, unknown>; current: Record<string, unknown> | null;
    differences: string[]; calls: { call_id: string; role_name: string; model_revision: number;
      status: string; effective_parameters: Record<string, unknown> | null }[];
  }[];
};
const fields: Record<string, string> = {
  id: "资源标识", revision: "配置修订", model: "模型标识", kind: "模型用途", provider: "服务商", protocol: "接口协议",
  max_output_tokens: "最大输出长度", temperature: "温度", supports_images: "图片能力声明", supports_tools: "工具能力声明",
  service_target: "服务目标", vector_fingerprint: "向量身份摘要", embedding_compatibility_key: "已核对兼容身份",
  credential_revision: "凭据摘要", name: "名称", version: "技能版本", content_digest: "内容摘要", file_count: "文件数量",
  service_target_digest: "完整服务目标摘要（不含密钥）",
  transport: "连接方式", target: "执行目标", target_digest: "目标摘要", tools: "工具声明", enabled: "当前启用状态",
  published_version_id: "发布版本标识", published_version: "业务发布版本", role: "成员角色",
  embedding_model_id: "向量模型", rerank_model_id: "重排模型", keyword_min_coverage: "关键词最低覆盖率",
  chunk_size: "分块大小", chunk_overlap: "分块重叠", processing_revision: "资料处理修订",
  index_compatibility: "已记录的索引兼容信息", route_revision: "路由修订", candidates: "候选模型", failures: "故障阈值", cooldown: "冷却时间",
};
const kinds: Record<string, string> = { model: "模型", mcp: "MCP工具", skill: "技能", agent: "智能体",
  agent_version: "智能体发布版本", knowledge: "知识库检索策略", memory: "记忆检索策略", route: "模型路由" };
function text(value: unknown): string {
  if (value === null || value === undefined) return "未记录 / 未设置";
  if (typeof value === "boolean") return value ? "已声明支持" : "不支持 / 未启用";
  return typeof value === "object" ? JSON.stringify(value, null, 2) : String(value);
}

export function RunConfigurations({ id, mode }: { id: string; mode: Mode }) {
  return <Panel key={`${mode}:${id}`} id={id} mode={mode}/>;
}
function Panel({ id, mode }: { id: string; mode: Mode }) {
  const [open, setOpen] = useState(false), [reload, setReload] = useState(0);
  const [data, setData] = useState<Evidence | null>(null), [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void api<Evidence>(`/resource-runs/${mode}/${id}/configurations`, { signal: controller.signal })
      .then((result) => { if (!controller.signal.aborted) { setData(result); setError(""); } })
      .catch((reason: Error) => { if (!controller.signal.aborted) { setData(null); setError(reason.message); } });
    return () => controller.abort();
  }, [id, mode, open, reload]);
  return <details className="configuration-history" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>本次运行使用的配置</summary>
    {open && <div className="configuration-history-content">
      <button type="button" onClick={() => setReload((value) => value + 1)}>刷新运行配置</button>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p role="status">正在读取运行证据…</p>}
      {data && <>
        <p>{data.note}</p>
        {data.configuration_scope.run_id !== id && <p>配置与所属主运行共用：{data.configuration_scope.run_id}。以下为该主运行的实际资源证据。</p>}
        {mode === "work" && data.can_recover && data.error_code === "RESOURCE_RECOVERY_CONFIRMATION_REQUIRED" &&
          <Recovery id={id} onConfirmed={() => setReload((value) => value + 1)}/>}
        <p>业务版本：{Object.keys(data.business_version).length ? text(data.business_version) : "历史未记录"}</p>
        {!data.items.length && <p>历史未记录。没有以当前配置补造旧运行。</p>}
        {data.stages.map((stage) => <section key={stage.phase}>
          <h4>执行阶段 {stage.phase}{stage.confirmed_by ? " · 已明确确认恢复" : " · 首次执行"}</h4>
          <p>{new Date(stage.created_at).toLocaleString("zh-CN")}</p>
          {data.items.filter((item) => item.phase === stage.phase).map((item) => <details key={item.snapshot_id}>
            <summary>{kinds[item.resource_kind] || item.resource_kind} · {item.resource_id} · 修订 {item.revision} · {item.used_at ? "已发起调用 / 已加载" : "已解析，尚无使用证据"}</summary>
            <p>解析时间：{new Date(item.resolved_at).toLocaleString("zh-CN")} · 当前{item.available ? "可用" : "已停用、删除或未知"}</p>
            {item.credential_changed && <p>恢复时更新了同一接收方的凭据，未保存明文密钥。</p>}
            {Object.entries(item.snapshot).map(([field, value]) => <dl className="configuration-field" key={field}>
              <dt>{fields[field] || field}</dt><dd><span>本次实际配置</span><pre>{text(value)}</pre></dd>
              {item.current && field in item.current && <dd><span>当前配置{item.differences.includes(field) ? " · 已变化" : ""}</span><pre>{text(item.current[field])}</pre></dd>}
            </dl>)}
            {item.calls.map((call) => <p key={call.call_id}>模型调用 · {call.role_name === "lead" ? "主助手" : call.role_name} · 修订 {call.model_revision} · {call.status === "KNOWN" ? "已报告用量" : call.status === "PENDING" ? "调用中 / 未结束" : "用量或结果未知"} · 实际参数 {call.effective_parameters && Object.keys(call.effective_parameters).length ? text(call.effective_parameters) : "供应商未报告"}</p>)}
            {data.can_view_global_history && (item.resource_kind === "model" || item.resource_kind === "mcp" || item.resource_kind === "route") && <ConfigurationHistory kind={item.resource_kind} resourceId={item.resource_id}/>}
          </details>)}
        </section>)}
      </>}
    </div>}
  </details>;
}

type RecoveryPreview = { phase: number; confirmation_digest: string; can_confirm: boolean;
  manual_review: string[]; changes: { resource_id: string; before: Record<string, unknown>; after: Record<string, unknown> }[] };
function Recovery({ id, onConfirmed }: { id: string; onConfirmed: () => void }) {
  const [preview, setPreview] = useState<RecoveryPreview | null>(null), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  const endpoint = `/resource-runs/work/${id}/configuration-recovery`;
  return <section aria-label="配置恢复确认"><p>恢复所需的接收方或协议已变化。请先查看差异，再决定是否继续。</p>
    <button type="button" disabled={busy} onClick={async () => {
      setBusy(true); setConfirmed(false); setMessage("");
      try { setPreview(await api<RecoveryPreview>(endpoint)); }
      catch (reason) { setPreview(null); setMessage((reason as Error).message); }
      finally { setBusy(false); }
    }}>查看恢复差异</button>
    {preview && <>
      {preview.changes.map((change) => <div key={change.resource_id}><strong>{change.resource_id}</strong>
        <p>原阶段 {preview.phase}</p><pre>{text(change.before)}</pre><p>拟用于新阶段</p><pre>{text(change.after)}</pre></div>)}
      {preview.manual_review.map((reason) => <p key={reason}>{reason}</p>)}
      {preview.can_confirm && <><label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/>
        我已核对新的数据接收方和配置，同意建立新阶段继续原检查点</label>
        <button type="button" disabled={busy || !confirmed} onClick={async () => {
          setBusy(true); setMessage("");
          try {
            await api(endpoint, { method: "POST", body: JSON.stringify({ expected_phase: preview.phase,
              confirmation_digest: preview.confirmation_digest }) });
            setPreview(null); setConfirmed(false); setMessage("已记录确认并排队继续，旧阶段证据保留。"); onConfirmed();
          } catch (reason) { setMessage((reason as Error).message); setConfirmed(false); }
          finally { setBusy(false); }
        }}>确认并继续</button></>}
    </>}
    {message && <p role="status">{message}</p>}
  </section>;
}
