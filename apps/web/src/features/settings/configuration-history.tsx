"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./configuration-history.css";

type Kind = "model" | "mcp" | "route" | "knowledge" | "memory" | "repository" | "notification";
type Change = {
  id: number; resource_name: string; operation: string; before_revision: number | null;
  after_revision: number | null; created_at: string; actor_id: string | null; source: string;
  actor_name?: string; change_id: string; baseline: boolean; categories: string[];
  changes: Record<string, { before: unknown; after: unknown; credential_change?: string }>;
  related_runs?: { mode: string; run_id: string; phase: number; revision: string }[];
};
const fields: Record<string, string> = {
  revision: "配置修订",
  name: "显示名称", model: "模型标识", base_url: "服务地址", url: "服务地址", api_key_ciphertext: "访问密钥",
  token_ciphertext: "访问凭据", secret_ciphertext: "签名密钥", endpoint_ciphertext: "通知目标", enabled: "启用状态",
  is_default: "默认模型", temperature: "温度", max_output_tokens: "最大输出长度", kind: "用途",
  protocol: "协议", provider: "服务商", supports_images: "图片输入能力", supports_tools: "工具调用能力",
  transport: "连接方式", allow_private: "内网访问授权", oauth_required: "身份授权要求", command_json: "执行命令",
  tools: "工具定义", primary_model: "主模型", candidates: "候选模型", failures: "失败阈值", cooldown: "冷却时间",
  require_images: "要求图片输入", require_tools: "要求工具调用", require_native: "要求原生联网",
  embedding_model_id: "向量模型", rerank_model_id: "重排模型", chunk_size: "分块大小", chunk_overlap: "分块重叠",
  keyword_min_coverage: "关键词最低覆盖率", processing_revision: "处理修订", slug: "仓库身份", ref: "基准分支或提交",
  subdirectory: "源码范围", target_key: "兼容目标", source_key: "兼容来源", dimensions: "向量维度",
  evidence_digest: "兼容核对证据",
};
const operations: Record<string, string> = { INSERT: "创建", UPDATE: "修改", DELETE: "删除", BASELINE: "上线基线" };

function valueText(value: unknown): string {
  if (value === null || value === undefined) return "未记录 / 未设置";
  if (typeof value === "boolean") return value ? "是" : "否";
  if (typeof value !== "object") return String(value);
  if (Array.isArray(value)) return value.length ? value.map(valueText).join("、") : "无";
  const safe = value as { configured?: boolean; credential_revision?: number; origin?: string; digest?: string; redacted?: boolean };
  if (typeof safe.configured === "boolean")
    return `${safe.configured ? "已设置" : "未设置"}${safe.credential_revision ? ` · 凭据修订 ${safe.credential_revision}` : ""}`;
  if (safe.origin || safe.redacted)
    return `${safe.origin || "内容已隐藏"}${safe.digest ? ` · 摘要 ${safe.digest.slice(0, 12)}` : ""}`;
  return "内容已隐藏";
}

export function ConfigurationHistory({ kind, resourceId, name }: { kind: Kind; resourceId: string; name?: string }) {
  return <HistoryPanel key={`${kind}:${resourceId}`} kind={kind} resourceId={resourceId} name={name}/>;
}

function HistoryPanel({ kind, resourceId, name }: { kind: Kind; resourceId: string; name?: string }) {
  const [open, setOpen] = useState(false), [refresh, setRefresh] = useState(0);
  const [items, setItems] = useState<Change[] | null>(null), [error, setError] = useState("");
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    void api<{ items: Change[] }>(`/resource-configurations/${kind}/${encodeURIComponent(resourceId)}/changes`,
      { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) { setItems(data.items); setError(""); } })
      .catch((reason: Error) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [open, refresh, kind, resourceId]);
  return <details className="configuration-history" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary aria-label={`${name || "资源"}修改记录`}>修改记录</summary>
    {open && <div className="configuration-history-content">
      <div className="configuration-history-heading"><p>配置修改不等于业务版本发布；以下为已成功提交的记录。</p>
        <button type="button" onClick={() => { setItems(null); setError(""); setRefresh((count) => count + 1); }}>刷新记录</button></div>
      {error ? <p role="alert">{error}</p> : !items ? <p role="status">正在加载修改记录…</p> : !items.length ?
        <p>历史未记录。不会用当前配置补造过去的修改。</p> : items.map((item) => <section className="configuration-change" key={item.id}>
          <header><strong>{item.baseline ? "上线基线" : operations[item.operation] || "配置修改"} · {item.resource_name}</strong>
            <time dateTime={item.created_at}>{new Date(item.created_at).toLocaleString("zh-CN")}</time></header>
          <p>{item.actor_id ? `操作人：${item.actor_name || `用户 ${item.actor_id}`}` : `系统来源：${item.source || "历史未记录"}`}</p>
          <p>配置修订：{item.before_revision ?? "未记录"} → {item.after_revision ?? "未记录"}</p>
          {!!item.categories?.length && <p>变化分类：{item.categories.join("、")}</p>}
          {item.baseline && <p>上线时建立的基线，无法还原此前的修改。</p>}
          {Object.entries(item.changes || {}).map(([field, change]) => <dl className="configuration-field" key={field}>
            <dt>{fields[field] || field}{change.credential_change ? ` · ${change.credential_change}` : ""}</dt>
            <dd><span>修改前</span><p>{valueText(change.before)}</p></dd>
            <dd><span>修改后</span><p>{valueText(change.after)}</p></dd>
          </dl>)}
          <p className="configuration-correlation">同次修改标识：{item.change_id || "历史未记录"}</p>
          {!!item.related_runs?.length && <details><summary>关联运行（最多10条本人运行）</summary>
            {item.related_runs.map((run) => <RelatedRun key={`${run.mode}:${run.run_id}:${run.phase}`} run={run}/>)}</details>}
        </section>)}
    </div>}
  </details>;
}

function RelatedRun({ run }: { run: { mode: string; run_id: string; phase: number; revision: string } }) {
  const [evidence, setEvidence] = useState<string | null>(null), [error, setError] = useState("");
  return <section><p>{({ ask: "对话", work: "任务", workflow: "工作流", resource: "资源处理" } as Record<string, string>)[run.mode] || run.mode} · 阶段 {run.phase} · {run.run_id}</p>
    <button type="button" onClick={async () => {
      try {
        const data = await api<{ items: { resource_kind: string; resource_id: string; revision: string; used_at: string | null }[] }>(`/resource-runs/${run.mode}/${run.run_id}/configurations`);
        setEvidence(data.items.map((item) => `${item.resource_kind} / ${item.resource_id} / 修订 ${item.revision} / ${item.used_at ? "已使用" : "仅已解析"}`).join("\n")); setError("");
      } catch (reason) { setEvidence(null); setError((reason as Error).message); }
    }}>查看关联运行配置</button>
    {evidence !== null && <pre>{evidence || "历史未记录"}</pre>}{error && <p role="alert">{error}</p>}
  </section>;
}
