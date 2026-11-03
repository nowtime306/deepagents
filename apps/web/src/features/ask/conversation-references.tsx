"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./conversation-references.css";

type References = {
  revision: number;
  items: { source_id: string; title: string; message_count: number; characters: number; available: boolean }[];
};
type Sources = { items: { id: string; title: string }[]; next_cursor: string | null };

export function ConversationReferences({ conversation, editable, onChange }: {
  conversation: string;
  editable: boolean;
  onChange: (revision: number) => void;
}) {
  const [data, setData] = useState<References | null>(null);
  const [sources, setSources] = useState<Sources | null>(null);
  const [query, setQuery] = useState("");
  const [selection, setSelection] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api<References>(`/conversations/${conversation}/references`, { signal: controller.signal })
      .then(setData).catch((reason) => { if (!controller.signal.aborted) setError(reason.message); });
    return () => controller.abort();
  }, [conversation]);

  async function search(cursor?: string) {
    setError("");
    try {
      const params = new URLSearchParams({ q: query });
      if (cursor) params.set("before", cursor);
      setSources(await api<Sources>(`/conversations?${params}`));
      setSelection("");
    } catch (reason) { setError((reason as Error).message); }
  }

  async function save(identifiers: string[], refreshSource?: string) {
    if (!data || saving || !editable) return;
    setSaving(true);
    setError("");
    try {
      const updated = await api<References>(`/conversations/${conversation}/references`, {
        method: "PUT", body: JSON.stringify({ revision: data.revision, source_ids: identifiers, refresh_source: refreshSource }),
      });
      setData(updated);
      setSelection("");
      onChange(updated.revision);
    } catch (reason) {
      setError((reason as Error).message);
      // Refresh a conflicting version, but never repeat the user's write automatically.
      try { setData(await api<References>(`/conversations/${conversation}/references`)); } catch { /* Show original error. */ }
    } finally { setSaving(false); }
  }

  return <details className="conversation-references">
    <summary>引用其他会话{data?.items.length ? ` · ${data.items.length}/3` : ""}</summary>
    <p>只引用本人已完成的正文，最多3个来源、32000字。新消息需点击更新；图片、工具及权限不会继承。修改引用会停止正在使用旧引用的后续调用。</p>
    {error && <p role="alert">{error}</p>}
    {!data && !error && <p role="status">正在读取引用…</p>}
    {data?.items.map((item) => <div className="reference-source" key={item.source_id}>
      <span><strong>{item.title}</strong><small>{item.message_count}条正文 · {item.characters}字{!item.available && " · 来源变化，请更新"}</small></span>
      <button type="button" disabled={!editable || saving} aria-label={`更新引用 ${item.title}`}
        onClick={() => void save(data.items.map((row) => row.source_id), item.source_id)}>更新</button>
      <button type="button" disabled={!editable || saving} aria-label={`移除引用 ${item.title}`}
        onClick={() => void save(data.items.filter((row) => row.source_id !== item.source_id).map((row) => row.source_id))}>移除</button>
    </div>)}
    {data && !data.items.length && <p>尚未引用其他会话。</p>}
    {editable && <div className="reference-search">
      <label>搜索本人会话<input value={query} maxLength={200} onChange={(event) => setQuery(event.target.value)} /></label>
      <button type="button" disabled={saving} onClick={() => void search()}>查找来源</button>
      {sources && <>
        <label>选择引用来源<select value={selection} onChange={(event) => setSelection(event.target.value)}>
          <option value="">请选择已完成的会话</option>
          {sources.items.filter((item) => item.id !== conversation && !data?.items.some((row) => row.source_id === item.id))
            .map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
        </select></label>
        <button type="button" disabled={!data || !selection || saving || data.items.length >= 3}
          onClick={() => void save([...(data?.items.map((item) => item.source_id) || []), selection])}>添加引用</button>
        {sources.next_cursor && <button type="button" disabled={saving} onClick={() => void search(sources.next_cursor!)}>下一页来源</button>}
      </>}
    </div>}
  </details>;
}
