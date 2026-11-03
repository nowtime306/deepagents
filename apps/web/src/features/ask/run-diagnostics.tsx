"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { useRunSnapshot, useLiveSnapshot } from "./run-snapshot";
import { TeamProgress } from "./agent-team";

type Call = {
  call_id: string;
  run_id: string;
  model_id: string;
  model_name: string | null;
  model_revision: number | null;
  mode: string;
  status: string;
  input_tokens: number | null;
  output_tokens: number | null;
  cache_read_tokens: number | null;
  cache_creation_tokens: number | null;
  reasoning_tokens: number | null;
  estimated_input_tokens: number | null;
  output_token_reserve: number | null;
  duration_ms: number | null;
  first_token_ms: number | null;
  estimated_cost: string | null;
  input_rate: string | null;
  output_rate: string | null;
  currency: string | null;
  failure_code: string | null;
  role_name?: string;
};
type Diagnostic = {
  run_id: string;
  mode: string;
  status: string;
  error_code: string | null;
  reason: string;
  budget: {
    snapshot_available: boolean;
    model_call_limit: number | null;
    token_limit: number | null;
    calls_recorded: number;
    reported_tokens: number | null;
    unknown_calls: number;
    pending_calls: number;
  };
  context: {
    events?: {
      event: string;
      data: {
        message_count?: number;
        estimated_tokens?: Record<string, number>;
        sources?: number;
        reference_tokens?: number;
        cutoff_index?: number;
        identical_results?: number;
        unknown_tool_calls?: number;
      };
      timestamp: string;
    }[];
    attachment_count: number;
    image_count: number | null;
    attachment_text_characters: number;
    max_input_estimate: number | null;
  };
  search: {
    configuration_known: boolean;
    enabled: boolean;
    mode: string | null;
    server_managed: boolean;
    tool_started: number;
    tool_returned: number;
    note: string;
  };
  calls: Call[];
  execution?: {
    durable: boolean;
    claimed_at: string | null;
    lease_until: string | null;
  };
  note: string;
  team_enabled?: boolean;
};

const labels: Record<string, string> = {
  KNOWN: "已报告",
  UNKNOWN: "用量未知",
  PENDING: "统计中",
  USAGE_NOT_REPORTED: "未返回完整有效用量",
  USAGE_INTERRUPTED: "调用中断，未取得完整统计",
  MODEL_REQUEST_FAILED: "模型请求失败",
  MODEL_REQUEST_TIMEOUT: "模型请求超时",
  MODEL_REQUEST_CANCELLED: "模型请求已取消",
};
const number = (value: number | null) =>
  value === null ? "未报告" : value.toLocaleString("zh-CN");
const time = (value: number | null) =>
  value === null ? "未采集" : (value / 1000).toFixed(2) + " 秒";

export function RunDiagnostics({
  id,
  mode = "ask",
  active = false,
}: {
  id: string;
  mode?: "ask" | "work";
  active?: boolean;
}) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className="run-diagnostics"
      onToggle={(event) => setOpen(event.currentTarget.open)}
    >
      <summary>运行诊断</summary>
      {open && (
        <DiagnosticContent
          key={id}
          id={id}
          mode={mode}
          active={active}
        />
      )}
    </details>
  );
}

function DiagnosticContent({
  id,
  mode,
  active,
}: {
  id: string;
  mode: string;
  active: boolean;
}) {
  const pushed = useRunSnapshot<Diagnostic>(id, mode, "diagnostics");
  const liveSnapshot = useLiveSnapshot(id, mode);
  const [loaded, setData] = useState<Diagnostic | null>(null);
  const data = pushed ?? loaded;
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    if (liveSnapshot) return;
    const controller = new AbortController();
    const load = () =>
      api<Diagnostic>(
        `/${mode === "work" ? "work-runs" : "runs"}/${id}/diagnostics`,
        { signal: controller.signal },
      )
        .then((result) => {
          if (!controller.signal.aborted) {
            setData(result);
            setError("");
          }
        })
        .catch((reason) => {
          if (!controller.signal.aborted) setError(reason.message);
        });
    void load();
    const timer = active && mode === "work" ? setInterval(() => void load(), 2000) : null;
    return () => {
      controller.abort();
      if (timer) clearInterval(timer);
    };
  }, [id, mode, active, liveSnapshot, reload]);
  return (
    <div
      className="diagnostic-content"
      aria-label="运行诊断详情"
    >
      <button
        type="button"
        disabled={liveSnapshot}
        onClick={() => setReload((old) => old + 1)}
      >
        {liveSnapshot ? active ? "实时推送" : "运行快照" : "刷新诊断"}
      </button>
      {error && <p role="alert">{error}</p>}
      {!data && !error && <p>正在读取诊断…</p>}
      {data && (
        <>
          <p className="diagnostic-reason">
            {data.reason}{" "}
            {data.error_code && <small>（{data.error_code}）</small>}
          </p>
          <h4>运行预算</h4>
          {data.team_enabled && <TeamProgress id={id} mode={mode === "work" ? "work" : "ask"} active={active} />}
          <p>
            {data.budget.snapshot_available
              ? `调用上限 ${data.budget.model_call_limit} 次 · Token 预算 ${data.budget.token_limit?.toLocaleString("zh-CN")}`
              : "旧运行未保存预算快照，无法还原当时上限。"}
          </p>
          <p>
            记录调用 {data.budget.calls_recorded} 次 · 已报告{" "}
            {number(data.budget.reported_tokens)} Token
            {data.budget.unknown_calls > 0 &&
              ` · ${data.budget.unknown_calls} 次用量未知`}
            {data.budget.pending_calls > 0 &&
              ` · ${data.budget.pending_calls} 次统计中`}
          </p>
          <h4>上下文与联网</h4>
          {data.mode === "ask" && (
            <p>
              {data.execution?.durable
                ? "由独立对话后台执行，API 重启不重放请求。"
                : data.status === "PENDING"
                  ? "请求已持久排队，等待对话后台。"
                  : "历史或兼容执行记录。"}
            </p>
          )}
          <p>
            输入附件 {data.context.attachment_count} 份 · 已提取文字{" "}
            {data.context.attachment_text_characters} 字 · 本地估算输入最大值{" "}
            {number(data.context.max_input_estimate)} Token
          </p>
          <p>
            {!data.search.configuration_known
              ? "此运行未保存联网配置。"
              : !data.search.enabled
                ? "此运行未启用联网搜索。"
                : data.search.server_managed
                  ? "已授权服务端原生搜索，实际搜索事件未独立采集。"
                  : `已授权应用搜索 · 已观察到工具返回 ${data.search.tool_returned} 次`}
          </p>
          <small>{data.search.note}</small>
          {!!data.context.events?.length && (
            <details className="diagnostic-context">
              <summary>上下文与收敛记录</summary>
              {data.context.events.map((event, index) => (
                <p key={index}>
                  {event.event === "context.observed"
                    ? `图状态 ${event.data.message_count} 条消息 · 用户约 ${event.data.estimated_tokens?.human || 0}、工具约 ${event.data.estimated_tokens?.tool || 0}、助手约 ${event.data.estimated_tokens?.ai || 0} Token（粗估）`
                    : event.event === "context.compacted"
                      ? `上下文已压缩，截点 ${event.data.cutoff_index}`
                      : event.event === "context.references"
                        ? `临时引用 ${event.data.sources} 个本人会话 · 约 ${event.data.reference_tokens} Token（粗估），未写入检查点`
                      : event.event === "checkpoint.repaired"
                        ? `已将 ${event.data.unknown_tool_calls} 个未完成工具结果标记为未知，没有重放`
                        : "相同工具结果重复，已停止无进展调用"}
                </p>
              ))}
            </details>
          )}
          <h4>逐次模型调用</h4>
          {!data.calls.length && (
            <p>此运行尚无模型调用账本，不能据此断言没有消耗。</p>
          )}
          {data.calls.map((call, index) => (
            <details
              className="diagnostic-call"
              key={call.call_id}
            >
              <summary>
                第 {index + 1} 次 · {call.model_name || call.model_id} ·{" "}
                {call.role_name && `角色 ${call.role_name === "lead" ? "主助手" : call.role_name} · `}
                {labels[call.status] || call.status}· 耗时{" "}
                {time(call.duration_ms)}
              </summary>
              <p>
                {call.run_id !== data.run_id ? "子工作流调用" : "本运行调用"} ·
                模型修订 {call.model_revision ?? "未保存"}
              </p>
              <dl className="diagnostic-grid">
                <div>
                  <dt>输入 Token</dt>
                  <dd>{number(call.input_tokens)}</dd>
                </div>
                <div>
                  <dt>输出 Token</dt>
                  <dd>{number(call.output_tokens)}</dd>
                </div>
                <div>
                  <dt>其中缓存读取</dt>
                  <dd>{number(call.cache_read_tokens)}</dd>
                </div>
                <div>
                  <dt>其中缓存写入</dt>
                  <dd>{number(call.cache_creation_tokens)}</dd>
                </div>
                <div>
                  <dt>其中推理 Token</dt>
                  <dd>{number(call.reasoning_tokens)}</dd>
                </div>
                <div>
                  <dt>首次流式输出等待</dt>
                  <dd>{time(call.first_token_ms)}</dd>
                </div>
                <div>
                  <dt>调用时粗估输入</dt>
                  <dd>{number(call.estimated_input_tokens)}</dd>
                </div>
                <div>
                  <dt>单次输出预留</dt>
                  <dd>{number(call.output_token_reserve)}</dd>
                </div>
              </dl>
              {call.failure_code && (
                <p>{labels[call.failure_code] || "调用未完成"}</p>
              )}
              <p>
                本次费用估算：
                {call.estimated_cost === null
                  ? "未计价"
                  : `${call.estimated_cost} ${call.currency}`}
                · 历史输入/输出单价（每百万 Token）：
                {call.input_rate ?? "未设置"} / {call.output_rate ?? "未设置"}
              </p>
            </details>
          ))}
          <small>{data.note}</small>
        </>
      )}
    </div>
  );
}

export function HarnessPolicies() {
  const [result, setResult] = useState<{
    ask: { model_calls: number; token_limit: number; permissions: string };
    work: { model_calls: number; token_limit: number; permissions: string };
    note: string;
  } | null>(null);
  const [error, setError] = useState("");
  return (
    <details
      className="run-diagnostics"
      onToggle={(event) => {
        if (event.currentTarget.open && !result)
          api<NonNullable<typeof result>>("/harness/policies")
            .then(setResult)
            .catch((reason) => setError(reason.message));
      }}
    >
      <summary>当前默认模式策略</summary>
      {error && <p role="alert">{error}</p>}
      {result && (
        <div className="diagnostic-content">
          <p>
            Ask：{result.ask.model_calls} 次调用 /{" "}
            {result.ask.token_limit.toLocaleString("zh-CN")} Token ·{" "}
            {result.ask.permissions}
          </p>
          <p>
            Work：{result.work.model_calls} 次调用 /{" "}
            {result.work.token_limit.toLocaleString("zh-CN")} Token ·{" "}
            {result.work.permissions}
          </p>
          <small>{result.note}</small>
        </div>
      )}
    </details>
  );
}
