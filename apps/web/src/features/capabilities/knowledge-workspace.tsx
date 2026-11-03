"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowLeft, BookOpen, Search, Upload, X } from "lucide-react";
import { api, ApiError } from "@/lib/api/platform";
import { MarkdownText } from "@/components/thread/markdown-text";
import { KnowledgeProcessing, DocumentRevisionActions } from "./knowledge-processing";
import { KnowledgeOCR } from "./knowledge-ocr";
import { KnowledgeEvaluation } from "./knowledge-evaluation";
import { MultimodalKnowledge } from "./multimodal-knowledge";
import { QualityGate } from "./quality-gate";
import { MediaRetrieval, VideoRetrieval } from "./media-retrieval";
import "./knowledge-workspace.css";

export type Knowledge = {
  id: string; name: string; description: string; documents: number;
  embedding_model_id: string | null; rerank_model_id: string | null; keyword_min_coverage: number;
};
type Document = { id: string; name: string; size: number; chunks: number; vector_status: string;
  revision?: number; status?: string; error?: string | null; last_parse_error?: string | null };
type SearchResult = { mode: string; reranked: boolean; score_type?: string; keyword_min_coverage?: number;
  keyword_filtered?: number; guidance?: string; warning?: string; items: { chunk_id: string; name: string;
    location: string; text: string; score: number; keyword_coverage?: number; vector_score?: number | null }[] };
type Tab = "文档" | "检索测试" | "配置" | "处理记录" | "评测" | "图片资料" | "OCR导入";
const tabs: Tab[] = ["文档", "检索测试", "配置", "处理记录", "评测", "图片资料"];

function documentState(document: Document, embedding: boolean) {
  const value = document.vector_status?.toUpperCase();
  if (document.last_parse_error || document.error || ["FAILED", "UNKNOWN"].includes(value))
    return { kind: "warning", text: document.last_parse_error ? "更新失败 · 原修订保留" : value === "UNKNOWN" ? "结果待核查" : "索引异常 · 可核查" };
  if (["PENDING", "RUNNING", "PROCESSING", "INDEXING"].includes(value)) return { kind: "processing", text: "索引处理中" };
  if (value === "READY") return { kind: "ready", text: "向量已就绪" };
  if (!embedding && ["NONE", "", undefined].includes(value)) return { kind: "ready", text: "关键词可用" };
  return { kind: "warning", text: embedding ? "向量未就绪 · 关键词可用" : "索引状态待核查" };
}

export function KnowledgeWorkspace({ knowledge, onBack, onChanged, canManage, canSearch, canBind, bound, onBind, bindingKnown = true }: {
  knowledge: Knowledge; onBack: () => void; onChanged: () => Promise<void>;
  canManage: boolean; canSearch: boolean; canBind: boolean; bound: boolean;
  onBind: (checked: boolean) => Promise<void>; bindingKnown?: boolean;
}) {
  const base = `/capabilities/knowledge/${knowledge.id}`;
  const [tab, setTab] = useState<Tab>("文档"), [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true), [loadError, setLoadError] = useState("");
  const [filter, setFilter] = useState(""), [stateFilter, setStateFilter] = useState("all");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [uploadOpen, setUploadOpen] = useState(false), [file, setFile] = useState<File | null>(null);
  const [uploadError, setUploadError] = useState(""), [uploadNotice, setUploadNotice] = useState("");
  const [query, setQuery] = useState(""), [searchBusy, setSearchBusy] = useState(false), [results, setResults] = useState<SearchResult | null>(null);
  const [rerankModels, setRerankModels] = useState<{ id: string; name: string }[]>([]);
  const [rerank, setRerank] = useState(knowledge.rerank_model_id || "");
  const [coverage, setCoverage] = useState(Math.round((knowledge.keyword_min_coverage ?? 0) * 100));
  const mutation = useRef(false);
  const uploadButton = useRef<HTMLButtonElement>(null);
  const reload = useCallback(async (signal?: AbortSignal) => {
    try {
      const data = await api<{ items: Document[] }>(base + "/documents", { signal });
      if (!signal?.aborted) { setDocuments(data.items); setLoadError(""); }
    } catch (reason) {
      if (!signal?.aborted) setLoadError((reason as Error).message);
    } finally { if (!signal?.aborted) setLoading(false); }
  }, [base]);
  useEffect(() => {
    const controller = new AbortController();
    void api<{ items: Document[] }>(base + "/documents", { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setDocuments(data.items); })
      .catch((reason) => { if (!controller.signal.aborted) setLoadError(reason.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [base]);
  useEffect(() => {
    if (tab !== "配置" || !canManage) return;
    const controller = new AbortController();
    void api<{ items: { id: string; name: string }[] }>("/capabilities/rerank-models", { signal: controller.signal })
      .then((data) => { if (!controller.signal.aborted) setRerankModels(data.items); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [tab, canManage]);
  async function refreshDocuments() { setLoading(true); setLoadError(""); await reload(); }
  async function changed() { await refreshDocuments(); await onChanged(); }
  async function operate(operation: () => Promise<unknown>, message: string) {
    if (mutation.current || !canManage) return;
    mutation.current = true; setBusy(true); setError(""); setNotice("");
    try { await operation(); await changed(); setNotice(message); }
    catch (reason) { setError((reason as Error).message); }
    finally { mutation.current = false; setBusy(false); }
  }
  function chooseFile(value: File | null) {
    setFile(value); setUploadError(""); setUploadNotice("");
    if (value && !/\.(txt|md|pdf|docx|xlsx)$/i.test(value.name)) setUploadError("请选择 TXT、Markdown、PDF、DOCX 或 XLSX 文件。");
    else if (value && value.size > 4 * 1024 * 1024) setUploadError("单文件不能超过 4 MB，请缩小文件后重试。");
  }
  async function upload() {
    if (!file || mutation.current || !canManage || invalidFile) return;
    mutation.current = true; setBusy(true); setUploadError(""); setUploadNotice(""); setNotice("正在上传并处理…");
    try {
      const body = new FormData(); body.set("file", file);
      const result = await api<{ duplicate?: boolean; message?: string }>(base + "/documents", { method: "POST", body });
      const message = result.duplicate ? result.message || "文档已存在，没有重复导入。" : "文档已导入，请查看解析与索引状态。";
      await changed(); setUploadNotice(message); setNotice(message); setFile(null);
    } catch (reason) {
      setNotice("");
      setUploadError(reason instanceof ApiError && reason.status < 500 ? reason.message : "上传结果暂未确认，请先刷新文档列表核对，避免重复导入。" );
    } finally { mutation.current = false; setBusy(false); }
  }
  const shown = documents.filter((document) => document.name.toLowerCase().includes(filter.trim().toLowerCase()) &&
    (stateFilter === "all" || documentState(document, !!knowledge.embedding_model_id).kind === stateFilter));
  const invalidFile = !!file && (!/\.(txt|md|pdf|docx|xlsx)$/i.test(file.name) || file.size > 4 * 1024 * 1024);
  return <section className="knowledge-workspace">
    <header className="knowledge-workspace-heading"><div><button type="button" onClick={onBack}><ArrowLeft size={15}/>返回知识库</button>
      <h2>{knowledge.name}</h2><p>{knowledge.description || "集中管理可供助手检索的资料。"}</p></div>
      <span>{loading ? "正在读取文档…" : `${documents.length} 份文档`} · {knowledge.embedding_model_id ? "关键词 + 向量" : "关键词检索"}</span></header>
    <nav className="knowledge-workspace-tabs" role="tablist" aria-label="知识库功能" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const current = Math.max(0, tabs.indexOf(tab));
      const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (current + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      setTab(tabs[next]); (event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]')[next])?.focus();
    }}>{tabs.map((item) => <button key={item} type="button" role="tab" aria-selected={tab === item}
      aria-controls="knowledge-workspace-panel" onClick={() => { setTab(item); setError(""); setNotice(""); }}>{item}</button>)}</nav>
    {error && <p role="alert" className="platform-error">{error}</p>}{notice && <p role="status" className="knowledge-workspace-notice">{notice}</p>}
    <div id="knowledge-workspace-panel" role="tabpanel" aria-label={tab}>
    {tab === "文档" && <section role="region" aria-label="知识文档列表" aria-busy={loading}>
      <div className="knowledge-document-toolbar"><div><h3>文档</h3><p>先确认资料可用，再到检索测试核对召回结果。</p></div>
        <div><button type="button" className="knowledge-secondary" onClick={() => setTab("OCR导入")}>{canManage ? "OCR导入" : "OCR记录"}</button>
          {canManage && <button ref={uploadButton} type="button" className="settings-primary" disabled={busy} onClick={() => setUploadOpen(true)}><Upload size={15}/>上传文档</button>}
          <button type="button" className="knowledge-secondary" disabled={loading || busy} onClick={() => void refreshDocuments()}>刷新文档</button></div></div>
      <div className="knowledge-document-filters"><label><Search size={15}/><input aria-label="搜索文档" placeholder="搜索文件名" value={filter} onChange={(event) => setFilter(event.target.value)}/></label>
        <select aria-label="文档状态筛选" value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}><option value="all">全部状态</option><option value="ready">可检索</option><option value="warning">需要关注</option><option value="processing">处理中</option></select>
        <span>{shown.length} / {documents.length} 份文档</span></div>
      {loading && <p role="status" className="knowledge-empty">正在加载文档…</p>}
      {loadError ? <div className="knowledge-empty" role="alert"><p>文档读取失败：{loadError}</p><button type="button" disabled={loading} onClick={() => void refreshDocuments()}>重试读取文档</button></div> : <>
      {!loading && !documents.length && <div className="knowledge-empty"><BookOpen size={30}/><h3>还没有文档</h3><p>{canManage ? "上传第一份文档后即可检索；扫描件可通过 OCR 导入。" : "当前知识库还没有可查看的文档。"}</p></div>}
      {!loading && documents.length > 0 && !shown.length && <p className="knowledge-empty">没有匹配的文档，请调整搜索或状态筛选。</p>}
      {shown.length > 0 && <div className="knowledge-document-table"><table aria-label="知识文档表"><thead><tr><th>文档名称</th><th>大小 / 分块</th><th>索引状态</th><th>操作</th></tr></thead><tbody>{shown.map((document) => {
        const state = documentState(document, !!knowledge.embedding_model_id);
        return <tr key={document.id}><td><strong title={document.name}>{document.name}</strong><small>修订 {document.revision || 1}</small></td>
          <td>{Math.ceil(document.size / 1024)} KB<small>{document.chunks} 个片段</small></td><td><span className="knowledge-document-status" data-state={state.kind}>{state.text}</span>
            {(document.error || document.last_parse_error) && <small>{document.last_parse_error || document.error}</small>}</td><td><div className="knowledge-document-actions">
            <DocumentRevisionActions knowledge={knowledge.id} document={{ id: document.id, revision: document.revision || 1 }} canManage={canManage && !busy} onChanged={changed}/>
            {canManage && knowledge.embedding_model_id && <button type="button" disabled={busy} onClick={() => {
              if (confirm("将再次请求向量模型并产生费用；若上次结果未知，可能重复计费。确认重新索引？")) void operate(() => api(`${base}/documents/${document.id}/reindex`, { method: "POST", body: JSON.stringify({ acknowledge_duplicate: true }) }), "重新索引请求已完成，请核对当前状态。");
            }}>重新索引</button>}
            {canManage && <button type="button" disabled={busy} className="knowledge-delete" onClick={() => {
              if (confirm(`删除文档“${document.name}”？`)) void operate(() => api(`${base}/documents/${document.id}`, { method: "DELETE" }), "文档已删除。");
            }}>删除</button>}</div></td></tr>;
      })}</tbody></table></div>}</>}
    </section>}
    {tab === "OCR导入" && <><button type="button" className="knowledge-secondary" onClick={() => setTab("文档")}>返回文档</button><h3>扫描件与图片导入</h3><KnowledgeOCR id={knowledge.id} canManage={canManage} onChanged={changed}/></>}
    {tab === "检索测试" && <section><h3>检索测试</h3><p className="knowledge-section-help">检查问题能否召回正确资料。这是资料检索，不会生成回答。{knowledge.embedding_model_id && " 向量检索可能产生模型用量。"}</p>
      <form className="knowledge-retrieval-form" onSubmit={async (event) => {
        event.preventDefault(); if (!canSearch || searchBusy || !query.trim()) return;
        setSearchBusy(true); setResults(null); setError("");
        try { setResults(await api<SearchResult>(base + "/search", { method: "POST", body: JSON.stringify({ query: query.trim() }) })); }
        catch (reason) { setError((reason as Error).message); } finally { setSearchBusy(false); }
      }}><label>检索问题<textarea aria-label="检索问题" placeholder="输入一个你希望资料能够回答的问题" maxLength={2000} required value={query} onChange={(event) => setQuery(event.target.value)}/></label>
        <button type="submit" className="settings-primary" disabled={!canSearch || searchBusy || !query.trim()}><Search size={15}/>{searchBusy ? "正在检索…" : "检索测试"}</button></form>
      {!canSearch && <p>当前角色没有知识检索权限。</p>}{searchBusy && <p role="status">正在检索资料…</p>}
      {results && <div className="knowledge-results"><p>{results.mode === "hybrid" ? "混合检索" : "关键词检索"}{results.reranked ? " · 已重排" : ""} · 候选证据 {results.items.length} 个片段
        {results.keyword_min_coverage !== undefined && ` · 关键词门槛 ${Math.round(results.keyword_min_coverage * 100)}%`}{results.keyword_filtered ? ` · 筛除 ${results.keyword_filtered} 个弱关键词匹配` : ""}</p>
        {!!results.items.length && <p role="status">已找到候选资料，请核对原文与适用范围。检索分数不代表回答置信度。</p>}{results.warning && <p>{results.warning}</p>}
        {results.items.map((item) => <article key={item.chunk_id}><header><strong>{item.name}</strong><span>{item.location}</span></header><MarkdownText>{item.text}</MarkdownText>
          <small>引用 ID：{item.chunk_id} · {results.score_type === "rerank" ? "重排分数" : "融合排名分数"} {item.score}
            {item.keyword_coverage !== undefined && ` · 关键词覆盖 ${Math.round(item.keyword_coverage * 100)}%`}{item.vector_score != null && ` · 向量相似度 ${item.vector_score.toFixed(3)}`}（分数不是回答置信度）</small></article>)}
        {!results.items.length && <div className="knowledge-empty"><h3>未找到相关内容</h3><p>请检查是否已上传相关资料、文档索引是否可用，或换一种提问方式。</p></div>}</div>}
    </section>}
    {tab === "配置" && <div className="knowledge-config">
      <section><h3>助手使用范围</h3><p>{bindingKnown ? bound ? "此知识库已绑定到通用助手。" : "此知识库尚未绑定到通用助手。" : "当前权限下未读取助手绑定状态。"} 上传资料与授权助手使用是两个独立操作。</p>
        {canBind && <label className="knowledge-binding"><input type="checkbox" checked={bound} disabled={busy} onChange={async (event) => {
          setBusy(true); setError(""); try { await onBind(event.target.checked); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
        }}/>绑定到通用助手</label>}</section>
      <section><h3>检索模型</h3><p>向量模型：{knowledge.embedding_model_id || "未配置，使用关键词检索"}</p><p>创建后不支持原地切换向量模型；如需更换，请新建知识库并重新导入资料。</p>
        <label>重排模型<select aria-label="知识库重排模型" disabled={busy || !canManage} value={rerank} onChange={async (event) => {
          const value = event.target.value, previous = rerank; setRerank(value); setBusy(true); setError("");
          try { await api(base + "/rerank", { method: "PUT", body: JSON.stringify({ rerank_model_id: value || null }) }); await onChanged(); setNotice("重排模型已保存。"); }
          catch (reason) { setRerank(previous); setError((reason as Error).message); } finally { setBusy(false); }
        }}><option value="">不使用重排</option>{rerank && !rerankModels.some((model) => model.id === rerank) && <option value={rerank}>{rerank}</option>}{rerankModels.map((model) => <option key={model.id} value={model.id}>{model.name}</option>)}</select></label>
        <p>启用向量或重排时，检索可能请求模型并计入用量。</p></section>
      <section><h3>关键词召回策略</h3><form onSubmit={(event) => { event.preventDefault(); void operate(() => api(base + "/retrieval", { method: "PUT", body: JSON.stringify({ keyword_min_coverage: coverage / 100 }) }), "检索策略已保存。"); }}>
        <label>最低关键词覆盖率（%）<input aria-label="最低关键词覆盖率" type="number" min={0} max={100} step={1} required disabled={!canManage || busy} value={coverage} onChange={(event) => setCoverage(Number(event.target.value))}/></label>
        {canManage && <button type="submit" className="knowledge-secondary" disabled={busy}>保存检索策略</button>}</form><p>默认 0%，排除仅单字匹配；门槛只作用于关键词候选，不限制向量召回，也不是回答置信度。提高门槛可能漏掉同义表达。</p></section>
      <KnowledgeProcessing id={knowledge.id} canManage={canManage} view="settings"/>
    </div>}
    {tab === "处理记录" && <><h3>处理记录</h3><KnowledgeProcessing id={knowledge.id} canManage={canManage} view="operations"/></>}
    {tab === "评测" && <><KnowledgeEvaluation id={knowledge.id} documents={documents} canManage={canManage} paid={!!(knowledge.embedding_model_id || rerank)}/><QualityGate key={knowledge.id} id={knowledge.id} canManage={canManage}/></>}
    {tab === "图片资料" && <><MultimodalKnowledge id={knowledge.id} canManage={canManage} canSearch={canSearch}/>{canSearch && <MediaRetrieval id={knowledge.id}/>}<VideoRetrieval id={knowledge.id} canManage={canManage}/></>}
    </div>
    <Dialog.Root open={uploadOpen} onOpenChange={setUploadOpen}><Dialog.Portal><Dialog.Overlay className="knowledge-upload-overlay"/><Dialog.Content className="knowledge-upload-dialog" onCloseAutoFocus={(event) => { event.preventDefault(); uploadButton.current?.focus(); }}>
      <div className="knowledge-upload-heading"><Dialog.Title>上传文档</Dialog.Title><Dialog.Close aria-label="关闭上传文档"><X size={18}/></Dialog.Close></div>
      <Dialog.Description>支持 TXT、Markdown、PDF、DOCX、XLSX；单文件最多 4 MB。扫描件请使用 OCR 导入，Excel 公式仅作为文本。</Dialog.Description>
      <form onSubmit={(event) => { event.preventDefault(); void upload(); }}><label>选择文件<input aria-label="上传知识文档" type="file" accept=".txt,.md,.pdf,.docx,.xlsx" disabled={busy} onChange={(event) => { chooseFile(event.target.files?.[0] || null); event.currentTarget.value = ""; }}/></label>
        {file && <p className="knowledge-upload-file">已选择：{file.name} · {Math.ceil(file.size / 1024)} KB</p>}
        {knowledge.embedding_model_id && <p>此库使用向量模型，导入与索引可能产生模型用量。</p>}
        {uploadError && <p role="alert" className="platform-error">{uploadError}</p>}{uploadNotice && <p role="status">{uploadNotice}</p>}
        {busy && <p role="status">正在上传并处理…</p>}
        <footer><Dialog.Close type="button">关闭</Dialog.Close><button type="submit" className="settings-primary" disabled={busy || !file || invalidFile || !canManage}>{busy ? "正在上传并处理…" : "开始上传"}</button></footer></form>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </section>;
}
