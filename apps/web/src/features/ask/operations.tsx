"use client";

import { FormEvent, useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "@/lib/api/platform";
import { HarnessPolicies } from "./run-diagnostics";
import { ServiceStatus } from "./service-status";
import { Billing } from "./billing";
import { ExtraCosts } from "./extra-costs";
import { OperationsBackups } from "./operations-backups";
import { ResourceDialog } from "./resource-dialog";
import { MonitoringAlerts } from "./monitoring-dashboard";

type Usage = {
  call_id: string;
  run_id: string;
  mode: string;
  model_id: string;
  status: string;
  input_tokens: number | null;
  output_tokens: number | null;
  estimated_cost: string | null;
  currency: string | null;
  created_at: string;
  cache_read_tokens?: number | null;
  cache_creation_tokens?: number | null;
  reasoning_tokens?: number | null;
};
type Summary = {
  currency: string | null;
  estimated_cost: string | null;
  input_tokens: string | null;
  output_tokens: string | null;
  calls: number;
  unknown_calls: number;
  unpriced_calls: number;
};
type Audit = {
  id: number;
  method: string;
  resource_path: string;
  status_code: number;
  created_at: string;
};

const modeLabels: Record<string, string> = { ask: "智能对话", work: "自主任务", workflow: "工作流", resource: "资源处理" };
function resourceLabel(path: string) {
  const name = path.replace(/^\/api\/v1\//, "").split("/")[0];
  return ({ workflows: "工作流", conversations: "对话", work: "任务", auth: "登录与会话", admin: "平台配置", agents: "智能体", knowledge: "知识库", billing: "费用与预算", operations: "运营记录", repositories: "代码仓库" } as Record<string, string>)[name] || "工作空间资源";
}

export function Operations({ isAdmin }: { isAdmin: boolean }) {
  const [rateOpen, setRateOpen] = useState(false);
  const [usage, setUsage] = useState<Usage[]>([]),
    [summary, setSummary] = useState<Summary[]>([]),
    [audit, setAudit] = useState<Audit[]>([]);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]),
    [model, setModel] = useState("");
  const [input, setInput] = useState("0"),
    [output, setOutput] = useState("0"),
    [currency, setCurrency] = useState("CNY"),
    [next, setNext] = useState<number | null>(null);
  const [rates, setRates] = useState<
    {
      model_id: string;
      input_per_million: string;
      output_per_million: string;
      currency: string;
    }[]
  >([]);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function reload() {
    const [u, a, m, r] = await Promise.all([
      api<{ items: Usage[]; summary: Summary[] }>("/operations/usage"),
      api<{ items: Audit[]; next_before: number | null }>("/operations/audit"),
      api<{ items: { id: string; name: string }[] }>(isAdmin ? "/admin/models" : "/models"),
      api<{ items: typeof rates }>("/operations/rates"),
    ]);
    setUsage(u.items);
    setSummary(u.summary);
    setAudit(a.items);
    setNext(a.next_before);
    setModels(m.items);
    setRates(r.items);
  }
  useEffect(() => {
    reload().catch((reason) => setError(reason.message));
  }, []);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      await api(`/operations/rates/${encodeURIComponent(model)}`, {
        method: "PUT",
        body: JSON.stringify({
          input_per_million: input,
          output_per_million: output,
          currency,
        }),
      });
      await reload(); setRateOpen(false);
    });
  }
  const totalCalls = summary.reduce((sum, item) => sum + item.calls, 0);
  function tokens(field: "input_tokens" | "output_tokens") {
    return summary.some((item) => item[field] !== null)
      ? summary.reduce((sum, item) => sum + Number(item[field] || 0), 0).toLocaleString("zh-CN")
      : "—";
  }
  return (
    <section
      className="run-history operations-page"
      aria-label="运营管理"
    >
      <header className="operations-heading">
        <div><span className="operations-eyebrow">工作空间 / 运营</span>
          <h1>运营管理</h1>
          <p>用量、费用与运行状态，集中掌握。</p>
        </div>
        <button className="settings-primary" disabled={busy} aria-label="刷新运营记录"
          onClick={() => void action(reload)}><RefreshCw size={14} />刷新运营记录</button>
      </header>
      <nav className="operations-jump" aria-label="运营区块">
        <a href="#ops-overview">用量概览</a><a href="#ops-calls">模型调用</a>
        <a href="#ops-billing">费用预算</a>{isAdmin && <><a href="#ops-services">服务状态</a><a href="#ops-backups">备份与容量</a></>}
        <a href="#ops-audit">操作审计</a>
      </nav>
      {error && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      <h2 id="ops-overview" className="operations-section-title">累计用量与估算</h2>
      <div className="operations-metrics">
        <article className="operations-metric"><span>累计模型调用</span><strong>{totalCalls.toLocaleString("zh-CN")}</strong><small>当前账号 · 已登记调用</small></article>
        <article className="operations-metric"><span>输入 Token</span><strong>{tokens("input_tokens")}</strong><small>服务已报告部分，不含未知用量</small></article>
        <article className="operations-metric"><span>输出 Token</span><strong>{tokens("output_tokens")}</strong><small>服务已报告部分，不含未知用量</small></article>
      </div>
      <p className="operations-note">
        统计覆盖账本启用后的聊天、任务、工作流及已登记的资源处理调用（含知识库入库、检索和重排）。服务未返回用量或尚未结束时标记未知；未设置单价的调用不计作零费用。不同币种分别汇总。
      </p>
      <div className="operations-price-summary">{summary.map((item, index) => (
        <span key={index}>{item.currency || "未定价"} · 已知部分估算 {item.estimated_cost ?? "未计算"} · 未知用量 {item.unknown_calls} 次 · 未计价 {item.unpriced_calls} 次</span>
      ))}</div>
      <HarnessPolicies />
      {isAdmin && (
        <section aria-label="模型估算单价列表"><header className="resource-list-heading"><h3>模型估算单价</h3><button className="settings-primary" onClick={() => { setError(""); setRateOpen(true); }}>设置模型估算单价</button></header>
          {!rates.length && <p>还没有估算单价。</p>}
          {rates.map((rate) => <article className="repository-card" key={rate.model_id}><strong>{models.find((m) => m.id === rate.model_id)?.name || rate.model_id}</strong><p>{rate.currency} · 每百万输入 {rate.input_per_million} / 输出 {rate.output_per_million}</p><button onClick={() => { setModel(rate.model_id); setInput(rate.input_per_million); setOutput(rate.output_per_million); setCurrency(rate.currency); setError(""); setRateOpen(true); }}>编辑估算单价</button></article>)}
          {rateOpen && <ResourceDialog title="设置模型估算单价" busy={busy} close={() => setRateOpen(false)}>{error && <p role="alert">{error}</p>}
          <form
            className="agent-form"
            onSubmit={save}
          >
            <label>
              计价模型
              <select
                aria-label="计价模型"
                required
                value={model}
                onChange={(e) => {
                  const id = e.target.value;
                  setModel(id);
                  const rate = rates.find((r) => r.model_id === id);
                  setInput(rate?.input_per_million || "0");
                  setOutput(rate?.output_per_million || "0");
                  setCurrency(rate?.currency || "CNY");
                }}
              >
                <option value="">请选择模型</option>
                {models.map((m) => (
                  <option
                    value={m.id}
                    key={m.id}
                  >
                    {m.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              每百万输入 Token 单价
              <input
                type="number"
                min="0"
                max="1000000"
                step="0.000001"
                required
                value={input}
                onChange={(e) => setInput(e.target.value)}
              />
            </label>
            <label>
              每百万输出 Token 单价
              <input
                type="number"
                min="0"
                max="1000000"
                step="0.000001"
                required
                value={output}
                onChange={(e) => setOutput(e.target.value)}
              />
            </label>
            <label>
              估算币种
              <select
                aria-label="估算币种"
                value={currency}
                onChange={(e) => setCurrency(e.target.value)}
              >
                <option value="CNY">人民币 CNY</option>
                <option value="USD">美元 USD</option>
              </select>
            </label>
            <p>只影响以后开始的调用，历史估算保留当时单价。</p>
            <button disabled={busy}>保存估算单价</button>
          </form></ResourceDialog>}</section>
      )}
      <h2 id="ops-calls" className="operations-section-title">最近模型调用<span aria-hidden="true">最近 {usage.length} 条</span></h2>
      <div className="operations-records" tabIndex={0} role="region" aria-label="模型调用记录">
      <table><thead><tr><th scope="col">模型 / 用途</th><th scope="col">输入 / 输出</th><th scope="col">费用估算</th><th scope="col">调用时间</th><th scope="col">明细</th></tr></thead>
      <tbody>{usage.map((item) => (
        <tr key={item.call_id}>
          <td><strong title={item.model_id}>{models.find((model) => model.id === item.model_id)?.name || "历史模型"}</strong><small>{modeLabels[item.mode] || "模型调用"}</small></td>
          <td>
            {item.status === "KNOWN"
              ? `${item.input_tokens} / ${item.output_tokens} Token`
              : item.status === "PENDING"
                ? "调用尚未结束或未返回统计"
                : "用量未知"}
          </td>
          <td>{item.estimated_cost === null ? "金额未计算" : `${item.estimated_cost} ${item.currency}`}</td>
          <td>{new Date(item.created_at).toLocaleString("zh-CN")}</td>
          <td><details>
            <summary>Token 细分</summary>
            <p>其中缓存读取 {item.cache_read_tokens ?? "未报告"} · 缓存写入 {item.cache_creation_tokens ?? "未报告"} · 推理 {item.reasoning_tokens ?? "未报告"}</p>
            <small>以上是输入/输出的细分，不重复相加；当前金额未单独计算缓存折扣。</small>
          </details></td>
        </tr>
      ))}</tbody></table>
      {!usage.length && <p className="operations-empty">暂无模型调用记录。</p>}
      </div>
      <div id="ops-billing"><Billing isAdmin={isAdmin}/><ExtraCosts isAdmin={isAdmin}/></div>
      {isAdmin && <div className="operations-service-grid"><ServiceStatus /><section id="ops-backups"><OperationsBackups /></section></div>}
      <h2 id="ops-audit" className="operations-section-title">操作审计<span aria-hidden="true">已加载 {audit.length} 条</span></h2>
      <p className="operations-note">
        仅记录操作类型、资源编号、结果和时间，不保存输入正文、凭证或文件内容。
      </p>
      <div className="operations-records" tabIndex={0} role="region" aria-label="操作审计记录">
        <table><thead><tr><th scope="col">操作</th><th scope="col">资源</th><th scope="col">结果</th><th scope="col">时间</th></tr></thead>
          <tbody>{audit.map((item) => <tr key={item.id}>
            <td><span className="operations-method">{item.method}</span></td>
            <td><strong>{resourceLabel(item.resource_path)}</strong><code>{item.resource_path}</code></td>
            <td><span className="operations-result" data-failed={item.status_code >= 400}>{item.status_code < 400 ? "成功" : "未完成"} · {item.status_code}</span></td>
            <td>{new Date(item.created_at).toLocaleString("zh-CN")}</td>
          </tr>)}</tbody>
        </table>
        {!audit.length && <p className="operations-empty">暂无操作记录。</p>}
      </div>
      {next !== null && (
        <button
          className="settings-primary operations-pagination"
          disabled={busy}
          onClick={() =>
            void action(async () => {
              const result = await api<{
                items: Audit[];
                next_before: number | null;
              }>(`/operations/audit?before=${next}`);
              setAudit((old) => [...old, ...result.items]);
              setNext(result.next_before);
            })
          }
        >
          加载更多审计记录
        </button>
      )}
      <MonitoringAlerts personal />
    </section>
  );
}
