"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./repositories.css";
import { ResourceDialog } from "./resource-dialog";

type Budget = {
  currency: string;
  amount: string;
  period_days: number;
  revision: number;
  enabled: boolean;
  settled: string;
  frozen: string;
  unknown_extra_calls?: number;
  window_start: string;
};
type Price = {
  id: string;
  name: string;
  currency: string;
  scheme: string;
  source: string;
  effective_at: string;
  input_per_million: string;
  output_per_million: string;
  cache_read_per_million: string | null;
  cache_write_per_million: string | null;
};
type Hold = {
  call_id: string;
  currency: string;
  reserved: string;
  actual: string | null;
  status: string;
  model_id: string;
  confirmed_at: string | null;
};
const priceBlank = {
  model_id: "",
  currency: "CNY",
  scheme: "uniform",
  input_per_million: "",
  output_per_million: "",
  cache_read_per_million: "",
  cache_write_per_million: "",
  source: "",
};

export function Billing({ isAdmin }: { isAdmin: boolean }) {
  const [editor, setEditor] = useState<"budget" | "price" | null>(null);
  const [priceQuery, setPriceQuery] = useState("");
  const [budgets, setBudgets] = useState<Budget[]>([]),
    [prices, setPrices] = useState<Price[]>([]),
    [holds, setHolds] = useState<Hold[]>([]);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [calibration, setCalibration] = useState<
    {
      model_id: string;
      model_revision: number;
      samples: number;
      mean_error: string;
      mean_ratio: string;
    }[]
  >([]);
  const [currency, setCurrency] = useState("CNY"),
    [amount, setAmount] = useState(""),
    [days, setDays] = useState(30),
    [enabled, setEnabled] = useState(false);
  const [price, setPrice] = useState(priceBlank),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function reload() {
    const [b, p, h, c] = await Promise.all([
      api<{ items: Budget[] }>("/billing/budgets"),
      api<{ items: Price[] }>("/billing/prices"),
      api<{ items: Hold[] }>("/billing/holds"),
      api<{ items: typeof calibration }>("/billing/calibration"),
    ]);
    setBudgets(b.items);
    setPrices(p.items);
    setHolds(h.items);
    setCalibration(c.items);
  }
  useEffect(() => {
    let active = true;
    void Promise.all([
      api<{ items: Budget[] }>("/billing/budgets"),
      api<{ items: Price[] }>("/billing/prices"),
      api<{ items: Hold[] }>("/billing/holds"),
      api<{ items: typeof calibration }>("/billing/calibration"),
    ])
      .then(([b, p, h, c]) => {
        if (active) {
          setBudgets(b.items);
          setPrices(p.items);
          setHolds(h.items);
          setCalibration(c.items);
        }
      })
      .catch((e) => {
        if (active) setError(e.message);
      });
    if (isAdmin)
      void api<{ items: { id: string; name: string }[] }>("/admin/models")
        .then((v) => {
          if (active) setModels(v.items);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    return () => {
      active = false;
    };
  }, [isAdmin]);
  async function act(work: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await work();
      await reload();
      setNotice(message); setEditor(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function saveBudget(event: FormEvent) {
    event.preventDefault();
    await act(
      () =>
        api(`/billing/budgets/${currency}`, {
          method: "PUT",
          body: JSON.stringify({
            amount,
            period_days: days,
            enabled,
            revision: budgets.find((b) => b.currency === currency)?.revision,
          }),
        }),
      "个人预算已保存",
    );
  }
  async function savePrice(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      await api("/billing/prices", {
        method: "POST",
        body: JSON.stringify({
          ...price,
          cache_read_per_million:
            price.scheme === "split" ? price.cache_read_per_million : null,
          cache_write_per_million:
            price.scheme === "split" ? price.cache_write_per_million : null,
        }),
      });
      setPrice(priceBlank);
    }, "已新增价格版本，历史调用保持原价格");
  }
  return (
    <section
      className="repository-panel phase-eight-settings"
      aria-label="费用与预算"
    >
      <h2>费用与预算</h2>
      <p>
        以下金额是模型调用估算，实际以供应商账单为准。搜索、图像和音视频服务的额外费用不在此预算内。
      </p>
      {error && !editor && <p role="alert">{error}</p>}
      {notice && <p role="status">{notice}</p>}
      <header className="resource-list-heading"><h3>我的周期预算 · {budgets.length}</h3><button className="settings-primary" onClick={() => { setCurrency("CNY"); setAmount(""); setDays(30); setEnabled(false); setError(""); setEditor("budget"); }}>新增预算</button></header>
      {!budgets.length && <p>尚未设置周期预算。</p>}
      <p>
        币种分别统计。缺少价格时，已启用预算的调用会被阻止。未报告金额的调用持续冻结；实际报告超额会保留并阻止下一调用。
      </p>
      {budgets.map((b) => (
        <article
          className="repository-card"
          key={b.currency}
        >
          <strong>
            {b.currency} · {b.enabled ? "已启用" : "已停用"}
          </strong>
          <p>
            上限 {b.amount} / {b.period_days} 天 · 已结算 {b.settled} · 冻结{" "}
            {b.frozen}
            {!!b.unknown_extra_calls && <span> · 另有{b.unknown_extra_calls}项金额未知，暂停新消费，请先核对</span>}
          </p>
          <small>
            周期起点：{new Date(b.window_start).toLocaleString("zh-CN")}
          </small>
          <button
            disabled={busy}
            onClick={() => {
              setEditor("budget"); setError("");
              setCurrency(b.currency);
              setAmount(b.amount);
              setDays(b.period_days);
              setEnabled(b.enabled);
            }}
          >
            编辑预算
          </button>
        </article>
      ))}
      {editor === "budget" && <ResourceDialog title="配置个人预算" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}<form
        onSubmit={saveBudget}
        className="repository-form"
      >
        <label>
          预算币种
          <select
            value={currency}
            onChange={(e) => {
              setCurrency(e.target.value);
              const b = budgets.find((v) => v.currency === e.target.value);
              setAmount(b?.amount || "");
              setDays(b?.period_days || 30);
              setEnabled(b?.enabled || false);
            }}
          >
            <option>CNY</option>
            <option>USD</option>
          </select>
        </label>
        <label>
          周期金额
          <input
            required
            type="number"
            min="0.000001"
            step="0.000001"
            value={amount}
            onChange={(e) => setAmount(e.target.value)}
          />
        </label>
        <label>
          周期天数
          <input
            required
            type="number"
            min={1}
            max={366}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => setEnabled(e.target.checked)}
          />
          启用个人金额预算
        </label>
        <button disabled={busy}>保存个人预算</button>
      </form></ResourceDialog>}
      <details>
        <summary>调用预留与金额核对（最近100项）</summary>
        {holds.map((h) => (
          <article
            key={h.call_id}
            className="repository-card"
          >
            <p>
              {h.currency} · 预留 {h.reserved} ·{" "}
              {h.actual === null ? "金额未知，继续冻结" : `结算 ${h.actual}`} ·{" "}
              {h.status}
            </p>
            {isAdmin && h.actual === null && h.status !== "PENDING" && (
              <button
                disabled={busy}
                onClick={() => {
                  const actual = window.prompt(
                    "已核对供应商账单后，填写此调用的实际金额。不会补写未知Token。",
                  );
                  if (actual === null) return;
                  const note = window.prompt(
                    "请填写账单核对依据（至少5字，不要填写密钥）",
                  );
                  if (!note) return;
                  void act(
                    () =>
                      api(`/billing/holds/${h.call_id}/reconcile`, {
                        method: "POST",
                        body: JSON.stringify({ actual, note }),
                      }),
                    "金额已核对；原始Token记录保持不变",
                  );
                }}
              >
                核对未知金额
              </button>
            )}
          </article>
        ))}
      </details>
      <details>
        <summary>Token估算偏差与校准样本</summary>
        {calibration.length ? (
          calibration.map((c) => (
            <p key={`${c.model_id}-${c.model_revision}`}>
              {models.find((m) => m.id === c.model_id)?.name || "模型"} · 修订{" "}
              {c.model_revision} · {c.samples}项 · 平均输入误差{" "}
              {Number(c.mean_error).toFixed(1)} Token · 实际/估算{" "}
              {Number(c.mean_ratio).toFixed(2)}
            </p>
          ))
        ) : (
          <p>尚无服务商报告的有效样本，不能推断估算精度。</p>
        )}
      </details>
      <section aria-label="价格版本列表"><header className="resource-list-heading"><h3>价格版本（最近200项）</h3>{isAdmin && <button className="settings-primary" onClick={() => { setPrice(priceBlank); setError(""); setEditor("price"); }}>新增价格版本</button>}</header>
      <label className="resource-list-search"><input aria-label="搜索价格版本" placeholder="搜索模型或价格来源" value={priceQuery} onChange={(e) => setPriceQuery(e.target.value)}/><span>{prices.length}项</span></label>
      {!prices.length && <p>还没有价格版本。</p>}
        {prices.filter((p) => `${p.name} ${p.source}`.toLowerCase().includes(priceQuery.toLowerCase())).map((p) => (
          <article
            className="repository-card"
            key={p.id}
          >
            <strong>
              {p.name} · {p.currency}
            </strong>
            <p>
              每百万Token：输入 {p.input_per_million} · 输出{" "}
              {p.output_per_million}
              {p.scheme === "split" &&
                ` · 缓存读取 ${p.cache_read_per_million} · 缓存写入 ${p.cache_write_per_million}`}
            </p>
            <p>
              {p.source} · {new Date(p.effective_at).toLocaleString("zh-CN")}
            </p>
          </article>
        ))}
      </section>
      {isAdmin && editor === "price" && (
        <ResourceDialog title="新增价格版本" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}<form
          onSubmit={savePrice}
          className="repository-form"
        >
          <h3>新增价格版本</h3>
          <p>
            只影响新增调用；填写当前核对过的价格，系统不自动抓取或换算币种。
          </p>
          <label>
            定价模型
            <select
              required
              value={price.model_id}
              onChange={(e) => setPrice({ ...price, model_id: e.target.value })}
            >
              <option value="">请选择</option>
              {models.map((m) => (
                <option
                  key={m.id}
                  value={m.id}
                >
                  {m.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            价格币种
            <select
              value={price.currency}
              onChange={(e) => setPrice({ ...price, currency: e.target.value })}
            >
              <option>CNY</option>
              <option>USD</option>
            </select>
          </label>
          <label>
            计价方案
            <select
              value={price.scheme}
              onChange={(e) => setPrice({ ...price, scheme: e.target.value })}
            >
              <option value="uniform">所有输入均一价格</option>
              <option value="split">区分普通输入和缓存读写</option>
            </select>
          </label>
          {(
            [
              ["input_per_million", "每百万普通输入价格"],
              ["output_per_million", "每百万输出价格"],
              ...(price.scheme === "split"
                ? [
                    ["cache_read_per_million", "每百万缓存读取价格"],
                    ["cache_write_per_million", "每百万缓存写入价格"],
                  ]
                : []),
            ] as [keyof typeof price, string][]
          ).map(([key, label]) => (
            <label key={key}>
              {label}
              <input
                type="number"
                required
                min="0"
                step="0.000001"
                value={price[key]}
                onChange={(e) => setPrice({ ...price, [key]: e.target.value })}
              />
            </label>
          ))}
          <label>
            价格来源与核对说明
            <input
              required
              minLength={1}
              maxLength={1000}
              value={price.source}
              onChange={(e) => setPrice({ ...price, source: e.target.value })}
            />
          </label>
          <button disabled={busy}>新增不可变价格版本</button>
        </form></ResourceDialog>
      )}
    </section>
  );
}
