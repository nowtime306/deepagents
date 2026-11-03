"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Model = { id: string; name: string };
type Picture = { id: string; name: string; score?: number };
type Index = {
  model_id: string;
  model_name: string;
  dimensions: number | null;
};
export function MultimodalKnowledge({ id, canManage = true, canSearch = true }: { id: string; canManage?: boolean; canSearch?: boolean }) {
  const [models, setModels] = useState<Model[]>([]);
  const [index, setIndex] = useState<Index | null>(null);
  const [model, setModel] = useState("");
  const [images, setImages] = useState<Picture[]>([]);
  const [results, setResults] = useState<Picture[] | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const prefix = `/capabilities/knowledge/${id}`;
  async function reload() {
    const [config, items] = await Promise.all([
      api<{ index: Index | null }>(`${prefix}/image-index`),
      api<{ items: Picture[] }>(`${prefix}/images`),
    ]);
    setIndex(config.index);
    setImages(items.items);
  }
  useEffect(() => {
    reload().catch((reason) => setError(reason.message));
    if (canManage) api<{ items: Model[] }>("/capabilities/multimodal-models")
      .then((result) => {
        setModels(result.items);
        setModel(result.items[0]?.id || "");
      })
      .catch((reason) => setError(reason.message));
  }, [id, canManage]);
  async function run(action: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function search(event: FormEvent) {
    event.preventDefault();
    if (!canSearch || !query.trim() || busy) return;
    setBusy(true);
    setError("");
    setResults(null);
    try {
      setResults(
        (
          await api<{ items: Picture[] }>(`${prefix}/image-search`, {
            method: "POST",
            body: JSON.stringify({ query }),
          })
        ).items,
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function show(item: Picture) {
    return (
      <article key={item.id}>
        <img
          src={`/api/v1${prefix}/images/${item.id}`}
          alt={item.name}
          style={{ maxWidth: 180, maxHeight: 140 }}
        />
        <p>
          {item.name}
          {item.score !== undefined && ` · 相似度 ${item.score.toFixed(3)}`}
        </p>
        {canManage && <button
          type="button"
          disabled={busy}
          onClick={() => {
            if (window.confirm(`删除图片“${item.name}”？`))
              void run(async () => {
                await api(`${prefix}/images/${item.id}`, { method: "DELETE" });
                setResults(null);
              });
          }}
        >
          删除图片
        </button>}
      </article>
    );
  }
  return (
    <section>
      <h3>多模态图片检索</h3>
      <p>
        使用百炼多模态向量模型，以文字检索图片。图片和查询将发送给服务商，可能产生费用；模型绑定后不能更换。
      </p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!index ? (
        canManage ? <>
          {!models.length && (
            <p>请先在模型管理中添加使用“百炼多模态向量”协议的模型。</p>
          )}
          <label>
            多模态模型{" "}
            <select
              aria-label="知识库多模态模型"
              value={model}
              onChange={(event) => setModel(event.target.value)}
            >
              {models.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                >
                  {item.name}
                </option>
              ))}
            </select>
          </label>{" "}
          <button
            type="button"
            disabled={!model || busy}
            onClick={() =>
              void run(() =>
                api(`${prefix}/image-index`, {
                  method: "POST",
                  body: JSON.stringify({ model_id: model }),
                }),
              )
            }
          >
            绑定模型
          </button>
        </> : <p>当前知识库尚未绑定图片向量模型。</p>
      ) : (
        <>
          <p>
            已绑定：{index.model_name}
            {index.dimensions ? ` · ${index.dimensions} 维` : ""}
          </p>
          {canManage && <><label>
            上传图片{" "}
            <input
              aria-label="上传知识图片"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              disabled={busy}
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) {
                  const body = new FormData();
                  body.append("file", file);
                  void run(() =>
                    api(`${prefix}/images`, { method: "POST", body }),
                  );
                }
                event.target.value = "";
              }}
            />
          </label>
          <p>支持 PNG、JPEG、WebP，每张最多 5 MiB。</p></>}
          <form onSubmit={search}>
            <input
              aria-label="图片检索问题"
              disabled={!canSearch || busy}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              maxLength={2000}
              required
              placeholder="输入要查找的图片内容"
            />{" "}
            <button disabled={busy || !canSearch || !query.trim()}>检索图片</button>
          </form>
          {results && (
            <div>
              <h4>检索结果</h4>
              {results.length ? results.map(show) : <p>没有结果。</p>}
            </div>
          )}
          <h4>已上传图片</h4>
          {images.length ? images.map(show) : <p>暂无图片。</p>}
        </>
      )}
    </section>
  );
}
