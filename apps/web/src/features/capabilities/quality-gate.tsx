"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Policy = { enforce_publish: boolean; minimum_questions: number; recall_target: number | string;
  citation_target: number | string; no_answer_target: number | string; revision: number };
type Question = { id: string; question: string; unanswerable: boolean };
type Review = { question_id: string; answer: string; cited_documents: string[]; citation_correct: boolean | null;
  refusal_correct: boolean | null; note: string; revision: number };
type Result = { passed: boolean; reasons: string[]; metrics: Record<string, number | null>; questions: number;
  citation_denominator: number; no_answer_denominator: number; reviewed_answers: number; dataset_type: string; note: string };
const names: Record<string, string> = { recall_at_10: "Recall@10", citation_correctness: "生成引用正确率",
  no_answer_retrieval: "无答案检索正确率", answer_refusal_accuracy: "生成拒答正确率" };

export function QualityGate({ id, canManage }: { id: string; canManage: boolean }) {
  const base = `/capabilities/knowledge/${id}/quality`;
  const [policy, setPolicy] = useState<Policy | null>(null), [error, setError] = useState("");
  const [experiments, setExperiments] = useState<{ id: string; status: string; dataset_snapshot: { name: string } }[]>([]);
  const [experiment, setExperiment] = useState(""), [questions, setQuestions] = useState<Question[]>([]);
  const [reviews, setReviews] = useState<Review[]>([]), [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState(""), [documents, setDocuments] = useState(""), [label, setLabel] = useState("");
  const [note, setNote] = useState(""), [result, setResult] = useState<Result | null>(null), [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    Promise.all([api<Policy>(base + "/policy"), api<{ items: typeof experiments }>(`/capabilities/knowledge/${id}/experiments`)])
      .then(([p, e]) => { if (active) { setPolicy(p); setExperiments(e.items); } }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [base, id]);
  async function run(work: () => Promise<void>) {
    setBusy(true); setError(""); setNotice("");
    try { await work(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function select(value: string) {
    setExperiment(value); setResult(null); setQuestions([]); setReviews([]); setQuestion("");
    setAnswer(""); setDocuments(""); setLabel(""); setNote("");
    if (!value) return;
    await run(async () => {
      const data = await api<{ questions: Question[]; items: Review[] }>(base + `/reviews/${value}`);
      setQuestions(data.questions); setReviews(data.items);
    });
  }
  function choose(value: string) {
    setQuestion(value); const r = reviews.find(row => row.question_id === value);
    setAnswer(r?.answer || ""); setDocuments(r?.cited_documents.join(",") || ""); setNote(r?.note || "");
    const q = questions.find(row => row.id === value), v = q?.unanswerable ? r?.refusal_correct : r?.citation_correct;
    setLabel(v == null ? "" : String(v));
  }
  function saveReview(event: FormEvent) {
    event.preventDefault(); const q = questions.find(row => row.id === question); if (!q || !label) return;
    void run(async () => {
      const row = await api<Review>(base + `/reviews/${experiment}/${encodeURIComponent(question)}`, { method: "PUT",
        body: JSON.stringify({ answer, cited_documents: q.unanswerable ? [] : documents.split(",").map(v => v.trim()).filter(Boolean),
          citation_correct: q.unanswerable ? null : label === "true", refusal_correct: q.unanswerable ? label === "true" : null,
          note, revision: reviews.find(r => r.question_id === question)?.revision || 0 }) });
      setReviews(old => [...old.filter(r => r.question_id !== question), row]); setResult(null); setNotice("标注已保存，请重新运行门禁检查。");
    });
  }
  return <section aria-label="知识质量门禁" className="knowledge-quality">
    <h3>质量门禁</h3><p>真实业务题集至少50题，且须包含无答案题。检索命中不代替生成答案的引用核对；合成题不能通过真实业务门禁。</p>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {!policy ? <p>正在读取质量规则…</p> : <form onSubmit={event => { event.preventDefault(); void run(async () => {
      const { enforce_publish, minimum_questions, recall_target, citation_target, no_answer_target, revision } = policy;
      setPolicy(await api<Policy>(base + "/policy", { method: "PUT", body: JSON.stringify({ enforce_publish,
        minimum_questions, recall_target, citation_target, no_answer_target, revision }) })); setNotice("质量规则已保存。");
    }); }}>
      <label><input type="checkbox" checked={policy.enforce_publish} disabled={!canManage || busy}
        onChange={e => setPolicy({ ...policy, enforce_publish: e.target.checked })} />发布智能体前强制检查此知识库</label>
      {(["recall_target", "citation_target", "no_answer_target"] as const).map(key => <label key={key}>
        {key === "recall_target" ? "Recall目标" : key === "citation_target" ? "引用正确率目标" : "无答案/拒答目标"}
        <input type="number" min="0" max="1" step="0.01" required disabled={!canManage || busy} value={policy[key]}
          onChange={e => setPolicy({ ...policy, [key]: e.target.value })} /></label>)}
      {canManage && <button disabled={busy}>保存质量规则</button>}
    </form>}
    <label>最近100次实验<select disabled={busy} value={experiment} onChange={e => void select(e.target.value)}>
      <option value="">请选择实验</option>{experiments.map(e => <option key={e.id} value={e.id}>{e.dataset_snapshot.name} · {e.status} · {e.id}</option>)}</select></label>
    {experiment && <button disabled={busy} onClick={() => void run(async () => setResult(await api<Result>(base + `/check/${experiment}`)))}>运行门禁检查</button>}
    {result && <div role="status"><strong>{result.passed ? "门禁通过（按本次人工标注）" : "门禁未通过或尚未验收"}</strong>
      <p>题目{result.questions}；引用分母{result.citation_denominator}；无答案分母{result.no_answer_denominator}；已标注{result.reviewed_answers}。</p>
      {Object.entries(result.metrics).map(([key, value]) => <p key={key}>{names[key] || key}：{value == null ? "未完整验收" : `${(value * 100).toFixed(1)}%`}</p>)}
      <ul>{result.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul><p>{result.note}</p></div>}
    {questions.length > 0 && <><h4>逐题生成答案与证据核对</h4><label>题目<select disabled={busy} value={question} onChange={e => choose(e.target.value)}>
      <option value="">请选择需要标注的题目</option>{questions.map(q => <option value={q.id} key={q.id}>{q.question}</option>)}</select></label>
      {question && <form onSubmit={saveReview}>
        <label>实际生成答案<textarea required maxLength={12000} disabled={!canManage || busy} value={answer} onChange={e => setAnswer(e.target.value)} /></label>
        {!questions.find(q => q.id === question)?.unanswerable && <label>实际引用文档ID（逗号分隔）<input disabled={!canManage || busy} value={documents} onChange={e => setDocuments(e.target.value)} /></label>}
        <label>{questions.find(q => q.id === question)?.unanswerable ? "人工核对拒答" : "人工核对引用"}<select required disabled={!canManage || busy} value={label} onChange={e => setLabel(e.target.value)}>
          <option value="">尚未标注</option><option value="true">正确</option><option value="false">错误</option></select></label>
        <label>核对说明<textarea maxLength={2000} disabled={!canManage || busy} value={note} onChange={e => setNote(e.target.value)} /></label>
        {canManage && <button disabled={busy}>保存人工标注</button>}
      </form>}</>}
  </section>;
}
