"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "./resource-dialog";

type Price = { id: string; model_id: string; item: string; unit: string; currency: string; unit_price: string };
type Line = { call_id: string; item: string; currency: string; amount: string; billed_at?: string };
type Result = Line & { status: string; platform_amount: string | null };
type Import = { id: string; name: string };
type Usage = { id: string; item: string; quantity: string | null; amount: string | null; source: string };
const labels: Record<string, string> = { match: "一致", difference: "金额差异", unknown: "平台金额未知",
  currency_mismatch: "币种不同", platform_missing: "平台缺失", provider_missing: "供应商缺失" };
export function ExtraCosts({ isAdmin }: { isAdmin: boolean }) {
  const [prices, setPrices] = useState<Price[]>([]), [imports, setImports] = useState<Import[]>([]);
  const [priceCursor, setPriceCursor] = useState<string | null>(null), [billCursor, setBillCursor] = useState<string | null>(null);
  const [usage, setUsage] = useState<Usage[]>([]);
  const [billFile, setBillFile] = useState<File | null>(null), [billName, setBillName] = useState("账单");
  const [columns, setColumns] = useState<string[]>([]), [mapping, setMapping] = useState<Record<string, string>>({});
  const [requestIds, setRequestIds] = useState("{}");
  const [periodStart, setPeriodStart] = useState(""), [periodEnd, setPeriodEnd] = useState("");
  const [scopeModel, setScopeModel] = useState(""), [scopeCurrency, setScopeCurrency] = useState("CNY");
  const [rows, setRows] = useState<Line[]>([]), [results, setResults] = useState<Result[] | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [open, setOpen] = useState(false);
  const [model, setModel] = useState(""), [item, setItem] = useState(""), [unit, setUnit] = useState("call");
  const [currency, setCurrency] = useState("CNY"), [price, setPrice] = useState("0"), [source, setSource] = useState("");
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const reload = useCallback(async () => {
    const [p, b, m, u] = await Promise.all([api<{ items: Price[]; next_cursor: string | null }>("/operations/extra-costs/prices"),
      api<{ items: Import[]; next_cursor: string | null }>("/operations/extra-costs/bills"), api<{ items: typeof models }>("/operations/extra-costs/models"), api<{ items: Usage[] }>("/operations/extra-costs/usage")]);
    setPrices(p.items); setImports(b.items); setModels(m.items); setUsage(u.items);
    setPriceCursor(p.next_cursor); setBillCursor(b.next_cursor);
  }, []);
  useEffect(() => { let cancelled = false; void Promise.resolve().then(() => { if (!cancelled) return reload(); }).catch(e => { if (!cancelled) setError(e.message); }); return () => { cancelled = true; }; }, [reload]);
  async function action(operation: () => Promise<void>) {
    setBusy(true); setError(""); try { await operation(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  function billBody() {
    return { rows, name: billName, ...(periodStart || periodEnd ? {
      period_start: periodStart ? new Date(periodStart).toISOString() : null,
      period_end: periodEnd ? new Date(periodEnd).toISOString() : null,
      model_ids: scopeModel ? [scopeModel] : [], currency: scopeCurrency,
    } : {}) };
  }
  async function parseFile(file: File, applyMapping = false) {
    const data = new FormData(); data.append("file", file); data.append("request_ids", requestIds);
    if (applyMapping) data.append("mapping", JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([,value]) => value))));
    const parsed = await api<{ rows: Line[]; columns: string[]; needs_mapping: boolean }>("/operations/extra-costs/bills/parse", { method: "POST", body: data });
    setRows(parsed.rows); setColumns(parsed.columns);
    if (!applyMapping) setMapping(Object.fromEntries(["call_id", "item", "currency", "amount", "billed_at"].map(key => [key, parsed.columns.includes(key) ? key : ""])));
  }
  function save(event: FormEvent) {
    event.preventDefault(); void action(async () => {
      await api("/operations/extra-costs/prices", { method: "POST", body: JSON.stringify({ model_id: model,
        item, unit, currency, unit_price: price, source, effective_at: new Date().toISOString() }) });
      await reload(); setOpen(false);
    });
  }
  return <section aria-label="额外费用与账单核对">
    <header><h3>额外费用与账单核对</h3>{isAdmin && <button onClick={() => setOpen(true)}>新增计费版本</button>}</header>
    <p>Token/缓存价格单独保留。额外费用按明确单位记录，未报告数量保持未知，不代表完整供应商账单。</p>
    {error && <p role="alert">{error}</p>}
    {prices.length ? prices.map(p => <article key={p.id}>{models.find(m => m.id === p.model_id)?.name || p.model_id} · {p.item} · {p.unit_price} {p.currency}/{p.unit}</article>)
      : <p>尚无额外费用价格版本。</p>}
    {priceCursor && <button disabled={busy} onClick={() => void action(async () => {
      const result = await api<{ items: Price[]; next_cursor: string | null }>(`/operations/extra-costs/prices?before=${encodeURIComponent(priceCursor)}`);
      setPrices(old => [...old, ...result.items.filter(item => !old.some(row => row.id === item.id))]);
      setPriceCursor(result.next_cursor);
    })}>加载更早价格版本</button>}
    <h4>最近500条额外费用</h4>{usage.map(u => <article key={u.id}>{u.item} · {u.quantity ?? "数量未知"} · {u.amount ?? "金额未知"} · {u.source === "manual" ? "人工记录" : "服务上报"}
      {isAdmin && u.quantity === null && <ReconcileUsage id={u.id} refresh={reload} />}</article>)}
    <label>上传标准CSV或JSON账单<input type="file" accept=".csv,.json" disabled={busy} onChange={event => {
      const file = event.target.files?.[0]; if (!file) return; setResults(null); setRows([]); setColumns([]); setBillFile(file); setBillName(file.name);
      void action(() => parseFile(file));
    }} /></label>
    <p>支持映射调用ID、计费项、币种、金额和带时区账单时间。没有指定账期时只核对提交ID；不自动换汇或改写账本。</p>
    {!!columns.length && <details open={!rows.length}><summary>账单字段映射</summary>
      {["call_id", "item", "currency", "amount", "billed_at"].map(key => <label key={key}>{({ call_id: "调用ID", item: "计费项", currency: "币种", amount: "金额", billed_at: "账单时间（账期核对必填）" } as Record<string, string>)[key]}
        <select value={mapping[key] || ""} onChange={e => { setMapping(old => ({ ...old, [key]: e.target.value })); setRows([]); setResults(null); }}>
          <option value="">请选择来源列</option>{columns.map(column => <option key={column} value={column}>{column}</option>)}
        </select></label>)}
      <label>外部请求ID对照（可选JSON：外部ID→内部调用UUID）<textarea value={requestIds} maxLength={64000} onChange={e => { setRequestIds(e.target.value); setRows([]); setResults(null); }} /></label>
      <button disabled={!billFile || busy} onClick={() => void action(() => parseFile(billFile!, true))}>应用字段映射</button>
    </details>}
    <label>账单名称<input value={billName} maxLength={120} onChange={e => setBillName(e.target.value)} /></label>
    <details><summary>完整账期范围（可选，最长31天）</summary>
      <label>开始时间<input type="datetime-local" value={periodStart} onChange={e => { setPeriodStart(e.target.value); setResults(null); }} /></label>
      <label>结束时间<input type="datetime-local" value={periodEnd} onChange={e => { setPeriodEnd(e.target.value); setResults(null); }} /></label>
      <label>该供应商账单覆盖模型<select value={scopeModel} onChange={e => { setScopeModel(e.target.value); setResults(null); }}>
        <option value="">请选择模型</option>{models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
      <label>账期币种<select value={scopeCurrency} onChange={e => { setScopeCurrency(e.target.value); setResults(null); }}><option>CNY</option><option>USD</option></select></label>
      <p>按调用发生时间核对，费用补录时间仅用于审计。需明确模型、币种及每行billed_at；其他模型/币种不计入本账期。普通账号仅可选择本人发生过调用的模型。</p>
    </details>
    <button disabled={!rows.length || busy} onClick={() => void action(async () => {
      const result = await api<{ items: Result[] }>("/operations/extra-costs/bills/preview", {
        method: "POST", body: JSON.stringify(billBody()) }); setResults(result.items);
    })}>预览核对 {rows.length ? `（${rows.length}条）` : ""}</button>
    <button disabled={!results || busy} onClick={() => void action(async () => {
      await api("/operations/extra-costs/bills/import", { method: "POST", body: JSON.stringify(billBody()) });
      await reload(); setResults(null); setRows([]);
    })}>确认导入</button>
    {results?.map((r, i) => <article key={`${r.call_id}-${r.item}-${i}`}>
      {r.item} · {labels[r.status] || r.status} · 供应商 {r.amount ?? "未知"} / 平台 {r.platform_amount ?? "未知"} {r.currency}
    </article>)}
    <h4>核对记录（已加载{imports.length}份）</h4>{imports.map(b => <p key={b.id}>{b.name} · <a
      href={`/api/v1/operations/extra-costs/bills/${b.id}/download`}>下载核对结果</a></p>)}
    {billCursor && <button disabled={busy} onClick={() => void action(async () => {
      const result = await api<{ items: Import[]; next_cursor: string | null }>(`/operations/extra-costs/bills?before=${encodeURIComponent(billCursor)}`);
      setImports(old => [...old, ...result.items.filter(item => !old.some(row => row.id === item.id))]);
      setBillCursor(result.next_cursor);
    })}>加载更早核对记录</button>}
    {open && <ResourceDialog title="新增额外计费版本" busy={busy} close={() => setOpen(false)}>
      <form onSubmit={save}>
        <label>模型<select value={model} onChange={e => setModel(e.target.value)} required>
          <option value="">请选择模型</option>{models.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
        <label>计费项标识<input required pattern="[a-zA-Z0-9_.-]+" value={item} onChange={e => setItem(e.target.value)} /></label>
        <label>单位<select value={unit} onChange={e => setUnit(e.target.value)}>
          <option value="call">每次</option><option value="image">每张图片</option>
          <option value="audio_second">音频每秒</option><option value="video_second">视频每秒</option></select></label>
        <label>币种<select value={currency} onChange={e => setCurrency(e.target.value)}><option>CNY</option><option>USD</option></select></label>
        <label>单位价格<input required type="number" min="0" step="0.000000001" value={price} onChange={e => setPrice(e.target.value)} /></label>
        <label>价格来源<input required maxLength={500} value={source} onChange={e => setSource(e.target.value)} /></label>
        <p>立即生效，保存后不能覆盖历史版本。真实合同价格需另行核验。</p>
        <button disabled={busy}>保存价格版本</button>
      </form>
    </ResourceDialog>}
  </section>;
}

function ReconcileUsage({ id, refresh }: { id: string; refresh: () => Promise<void> }) {
  const [quantity, setQuantity] = useState(""), [note, setNote] = useState("");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  return <details><summary>核对未报告用量</summary><form onSubmit={event => {
    event.preventDefault(); setBusy(true); setError("");
    void api(`/operations/extra-costs/usage/${id}/reconcile`, { method: "POST",
      body: JSON.stringify({ quantity, note }) }).then(refresh).catch(e => setError(e.message)).finally(() => setBusy(false));
  }}><label>确认数量<input required type="number" min="0" step="0.000000001" value={quantity} onChange={e => setQuantity(e.target.value)} /></label>
    <label>核对依据<input required maxLength={500} value={note} onChange={e => setNote(e.target.value)} /></label>
    <p>此操作仅一次，保留原价格及未知来源记录；不能改写已确认费用。</p>
    {error && <p role="alert">{error}</p>}<button disabled={busy}>确认实际数量</button></form></details>;
}
