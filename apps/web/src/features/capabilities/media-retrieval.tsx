"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";

type Hit = { id: string; name: string; score: number; sample_seconds?: string | null; video_job_id?: string | null };
export function MediaRetrieval({ id }: { id: string }) {
  return <ImageQuery key={id} id={id} />;
}
function ImageQuery({ id }: { id: string }) {
  const [file, setFile] = useState<File | null>(null), [preview, setPreview] = useState("");
  const previewRef = useRef({ url: "" });
  const [items, setItems] = useState<Hit[] | null>(null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const generation = useRef(0);
  useEffect(() => {
    const state = previewRef.current;
    const current = generation;
    return () => { ++current.current; URL.revokeObjectURL(state.url); };
  }, []);
  function choose(next: File | null) {
    URL.revokeObjectURL(previewRef.current.url);
    previewRef.current.url = next ? URL.createObjectURL(next) : "";
    setFile(next); setPreview(previewRef.current.url); setItems(null); setError("");
  }
  async function search(event: FormEvent) {
    event.preventDefault(); if (!file || busy) return;
    const version = ++generation.current;
    setBusy(true); setError(""); setItems(null);
    try {
      if (file.size > 5 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type))
        throw new Error("请选择不超过5MiB的PNG、JPEG或WebP图片");
      const data = new FormData(); data.append("file", file);
      const result = await api<{ items: Hit[] }>(`/capabilities/knowledge/${id}/image-query/search`, {
        method: "POST", body: data,
      });
      if (version === generation.current) setItems(result.items);
    } catch (reason) { if (version === generation.current) setError((reason as Error).message); }
    finally { if (version === generation.current) setBusy(false); }
  }
  return <section aria-label="图片相似检索">
    <h3>以图搜图</h3><p>需要绑定图片向量模型并已有图片资料。查询图片不保存，相似度不代表答案置信度。</p>
    <form onSubmit={search}>
      <label>查询图片<input type="file" accept="image/png,image/jpeg,image/webp" disabled={busy}
        onChange={event => choose(event.target.files?.[0] || null)} /></label>
      {preview && <figure><img alt="查询图片预览" src={preview} style={{ maxWidth: "100%", maxHeight: 160 }} />
        <button type="button" disabled={busy} onClick={() => choose(null)}>移除图片</button></figure>}
      <button disabled={!file || busy}>{busy ? "正在计算图片相似度…" : "检索相似图片"}</button>
    </form>
    {error && <p role="alert">{error}</p>}
    {items !== null && !items.length && <p role="status">没有找到可显示的图片证据，请检查索引和资料。</p>}
    {items?.map(item => <article key={item.id}>
      <a href={`/api/v1/capabilities/knowledge/${id}/images/${item.id}`} target="_blank" rel="noreferrer">{item.name}</a>
      <span> · 相似度 {item.score.toFixed(3)}</span>
      {item.video_job_id && <a href={`/api/v1/capabilities/knowledge/${id}/videos/${item.video_job_id}/file#t=${Number(item.sample_seconds || 0)}`} target="_blank" rel="noreferrer"> · 查看采样{item.sample_seconds}秒附近视频</a>}
    </article>)}
  </section>;
}

type VideoJob = { id: string; name: string; status: string; indexed_frames: number; error_code: string | null; cancel_requested: boolean };
const videoStates: Record<string, string> = { PENDING: "排队中", RUNNING: "处理中", UNKNOWN: "需核查", COMPLETED: "已完成", FAILED: "失败", CANCELLED: "已取消" };
export function VideoRetrieval({ id, canManage = true }: { id: string; canManage?: boolean }) {
  return <VideoFiles key={id} id={id} canManage={canManage} />;
}
function VideoFiles({ id, canManage }: { id: string; canManage: boolean }) {
  const [items, setItems] = useState<VideoJob[]>([]), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [file, setFile] = useState<File | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const loadedMore = useRef(false);
  const [interval, setIntervalValue] = useState("5"), [maximum, setMaximum] = useState("12");
  const prefix = `/capabilities/knowledge/${id}/videos`;
  const reload = async () => { const result = await api<{ items: VideoJob[]; next_cursor: string | null }>(prefix); loadedMore.current = false; setItems(result.items); setCursor(result.next_cursor); };
  useEffect(() => {
    let closed = false;
    const read = () => { void api<{ items: VideoJob[]; next_cursor: string | null }>(`/capabilities/knowledge/${id}/videos`).then(result => {
      if (!closed) { setItems(old => loadedMore.current ? [...result.items, ...old.filter(row => !result.items.some(next => next.id === row.id))] : result.items);
        if (!loadedMore.current) setCursor(result.next_cursor); setError(""); }
    }).catch(e => { if (!closed) setError(e.message); }); };
    read(); const timer = setInterval(read, 5000);
    return () => { closed = true; clearInterval(timer); };
  }, [id]);
  async function action(operation: () => Promise<void>) {
    setBusy(true); setError(""); try { await operation(); await reload(); } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <section aria-label="视频帧资料"><header><h3>视频帧资料</h3></header>
    <p>最多20MiB、处理前60秒、最多12帧。结果的采样时间是帧检索定位，不包含音轨或完整时序理解。</p>
    {canManage && <form onSubmit={event => { event.preventDefault(); if (!file) return;
      void action(async () => { if (file.size > 20 * 1024 * 1024) throw new Error("视频不能超过20MiB");
        const data = new FormData(); data.append("file", file); data.append("interval_seconds", interval); data.append("max_frames", maximum);
        await api(prefix, { method: "POST", body: data }); setFile(null); });
    }}><label>MP4或WebM视频<input type="file" accept="video/mp4,video/webm" disabled={busy} onChange={e => setFile(e.target.files?.[0] || null)} /></label>
      <label>采样间隔（秒）<input type="number" min="1" max="30" required value={interval} onChange={e => setIntervalValue(e.target.value)} /></label>
      <label>最多帧数<input type="number" min="1" max="12" required value={maximum} onChange={e => setMaximum(e.target.value)} /></label>
      <button disabled={!file || busy}>上传并排队处理</button></form>}
    {error && <p role="alert">{error}</p>}
    {!items.length && <p>暂无视频资料。</p>}
    {cursor && <button disabled={busy} onClick={() => {
      setBusy(true); setError("");
      void api<{ items: VideoJob[]; next_cursor: string | null }>(`${prefix}?before=${encodeURIComponent(cursor)}`).then(result => {
        loadedMore.current = true; setItems(old => [...old, ...result.items.filter(row => !old.some(previous => previous.id === row.id))]);
        setCursor(result.next_cursor);
      }).catch(e => setError(e.message)).finally(() => setBusy(false));
    }}>加载更早视频资料</button>}
    {items.map(job => <article key={job.id}><strong>{job.name}</strong> · {videoStates[job.status] || job.status} · 已索引{job.indexed_frames}帧
      {job.error_code && <p>处理提示：{job.error_code}</p>}
      <a href={`/api/v1${prefix}/${job.id}/file`} target="_blank" rel="noreferrer">查看原视频</a>
      {canManage && <button disabled={busy || job.cancel_requested && ["PENDING", "RUNNING", "UNKNOWN"].includes(job.status)} onClick={() => void action(async () => {
        if (["PENDING", "RUNNING", "UNKNOWN"].includes(job.status)) await api(`${prefix}/${job.id}/cancel`, { method: "POST" });
        else await api(`${prefix}/${job.id}`, { method: "DELETE" });
      })}>{["PENDING", "RUNNING", "UNKNOWN"].includes(job.status) ? job.cancel_requested ? "等待确认停止" : "取消处理" : "删除视频及帧"}</button>}
    </article>)}
  </section>;
}
