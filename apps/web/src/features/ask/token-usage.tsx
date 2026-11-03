"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { RouteSelection } from "@/features/settings/model-routes";

export type Usage = {
  run_id?: string;
  routed?: boolean;
  calls: number;
  reported_calls: number;
  unknown_calls: number;
  pending_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
};

export function TokenUsage({
  usage,
  label = "本轮",
}: {
  usage?: Usage;
  label?: string;
}) {
  if (!usage?.calls) return null;
  const incomplete = usage.unknown_calls > 0 || usage.pending_calls > 0;
  return (
    <div
      className="token-usage"
      aria-label={label + " Token 用量"}
    >
      {label} · 输入 {usage.input_tokens?.toLocaleString("zh-CN") ?? "—"} · 输出{" "}
      {usage.output_tokens?.toLocaleString("zh-CN") ?? "—"} Token ·{" "}
      {usage.calls} 次调用
      {incomplete && (
        <span>
          （仅含已报告用量
          {usage.unknown_calls > 0 ? `，${usage.unknown_calls} 次未知` : ""}
          {usage.pending_calls > 0 ? `，${usage.pending_calls} 次统计中` : ""}）
        </span>
      )}
      {usage.run_id && usage.routed && <RouteSelection run={usage.run_id} />}
    </div>
  );
}

type EstimateRequest = {
  mode?: "ask" | "work";
  model_id: string;
  message?: string;
  conversation_id?: string | null;
  task_id?: string;
  agent_id?: string | null;
  agent_version_id?: string | null;
  attachment_ids?: string[];
};
type Estimate = {
  reference_input_estimate?: number;
  input_estimate: number;
  output_reserve: number;
  first_call_reserve: number;
  history_limited: boolean;
  turn_token_limit: number | null;
  note: string;
};

export function DraftEstimate({
  request,
  enabled = true,
}: {
  request: EstimateRequest;
  enabled?: boolean;
}) {
  const [result, setResult] = useState<{
    body: string;
    value?: Estimate;
    error?: string;
  } | null>(null);
  const body = JSON.stringify(request);
  useEffect(() => {
    if (!enabled || !request.model_id) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api<Estimate>("/usage/estimate", {
        method: "POST",
        body,
        signal: controller.signal,
      })
        .then((value) => {
          if (!controller.signal.aborted) setResult({ body, value });
        })
        .catch((error) => {
          if (!controller.signal.aborted)
            setResult({ body, error: error.message });
        });
    }, 450);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [body, enabled, request.model_id]);
  if (!enabled || !request.model_id) return null;
  const current = result?.body === body ? result : null;
  if (!current) return <p className="token-estimate">正在估算 Token…</p>;
  if (current.error)
    return (
      <p className="token-estimate">Token 估算暂不可用：{current.error}</p>
    );
  const value = current.value!;
  return (
    <details className="token-estimate">
      <summary>
        预计输入约 {value.input_estimate.toLocaleString("zh-CN")} · 单次输出上限{" "}
        {value.output_reserve.toLocaleString("zh-CN")} Token
      </summary>
      <p>{value.note}</p>
      {!!value.reference_input_estimate && (
        <p>
          其中引用会话预计输入约{" "}
          {value.reference_input_estimate.toLocaleString("zh-CN")} Token。
        </p>
      )}
      {value.history_limited && (
        <p>历史仅粗估最近 100 条消息，上下文压缩后的实际输入可能不同。</p>
      )}
      {value.turn_token_limit &&
        value.first_call_reserve > value.turn_token_limit && (
          <p>当前预估超过本轮预算，请减少附件或缩短上下文。</p>
        )}
    </details>
  );
}

export function WorkTokenUsage({
  run,
  active,
}: {
  run: string;
  active: boolean;
}) {
  const [result, setResult] = useState<{ id: string; value: Usage } | null>(
    null,
  );
  useEffect(() => {
    const controller = new AbortController();
    const load = () =>
      api<{ total: Usage; routed?: boolean }>(`/work-runs/${run}/usage`, {
        signal: controller.signal,
      })
        .then((data) => {
          if (!controller.signal.aborted)
            setResult({
              id: run,
              value: { ...data.total, run_id: run, routed: data.routed },
            });
        })
        .catch(() => {});
    void load();
    const timer = active ? setInterval(() => void load(), 2000) : null;
    return () => {
      controller.abort();
      if (timer) clearInterval(timer);
    };
  }, [run, active]);
  return (
    <TokenUsage
      usage={result?.id === run ? result.value : undefined}
      label="任务累计（含子工作流）"
    />
  );
}
