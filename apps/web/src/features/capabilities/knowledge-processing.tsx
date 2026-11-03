"use client";

import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "@/lib/api/platform";
import { TokenUsage, type Usage } from "@/features/ask/token-usage";
import "./knowledge-processing.css";

type Policy = { chunk_size: number; chunk_overlap: number; revision: number };
type Revision = {
  revision: number;
  name: string;
  chunks: number;
  chunk_size: number;
  chunk_overlap: number;
  parse_method: string;
  created_at: string;
};
type Chunk = {
  id: string;
  position: number;
  location: string;
  content: string;
};
type Operation = {
  id: string;
  action: string;
  source_revision: number | null;
  status: string;
  created_at: string;
  usage?: Usage;
};
const statuses: Record<string, string> = {
  RUNNING: "处理中",
  SUCCEEDED: "已完成",
  FAILED: "处理失败",
  UNKNOWN: "结果未知，未自动重试",
};

export function KnowledgeProcessing({
  id,
  canManage,
  view,
}: {
  id: string;
  canManage: boolean;
  view?: "settings" | "operations";
}) {
  const base = `/capabilities/knowledge/${id}`;
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const refresh = useCallback(
    async () =>
      setOperations(
        (await api<{ items: Operation[] }>(base + "/operations")).items,
      ),
    [base],
  );
  useEffect(() => {
    if (view !== "operations") void api<Policy>(base + "/processing")
      .then(setPolicy)
      .catch((e: Error) => setError(e.message));
    if (view === "settings") return;
    const update = () =>
      void refresh().catch((e: Error) => setError(e.message));
    update();
    const timer = window.setInterval(update, 5000);
    return () => window.clearInterval(timer);
  }, [base, refresh, view]);
  return (
    <section
      className="knowledge-processing"
      aria-label="文档处理设置"
    >
      <details open={view === "operations" ? true : undefined}>
        <summary>{view === "operations" ? "处理记录与用量" : "分块策略与处理用量"}</summary>
        {view !== "operations" && <p>
          按字符分块，保留来源位置；修改只影响后续导入或显式更新，不自动重切已有文档。此处字符数不是
          Token 数。
        </p>}
        {error && (
          <p
            role="alert"
            className="platform-error"
          >
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {view !== "operations" && policy && (
          <form
            className="knowledge-processing-form"
            onSubmit={async (event) => {
              event.preventDefault();
              setBusy(true);
              setError("");
              setNotice("");
              try {
                await api(base + "/processing", {
                  method: "PUT",
                  body: JSON.stringify(policy),
                });
                setPolicy(await api<Policy>(base + "/processing"));
                setNotice("分块策略已保存，原文档保持原修订。");
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            <label>
              分块字符数
              <input
                aria-label="分块字符数"
                type="number"
                required
                min={200}
                max={4000}
                disabled={busy || !canManage}
                value={policy.chunk_size}
                onChange={(e) =>
                  setPolicy({ ...policy, chunk_size: Number(e.target.value) })
                }
              />
            </label>
            <label>
              重叠字符数
              <input
                aria-label="重叠字符数"
                type="number"
                required
                min={0}
                max={Math.floor(policy.chunk_size / 2)}
                disabled={busy || !canManage}
                value={policy.chunk_overlap}
                onChange={(e) =>
                  setPolicy({
                    ...policy,
                    chunk_overlap: Number(e.target.value),
                  })
                }
              />
            </label>
            {canManage && <button disabled={busy}>保存分块策略</button>}
          </form>
        )}
        {view !== "operations" && <p>
          重叠不超过分块长度的一半。重复文件在同库、同策略下不会重复导入或索引；旧文档摘要未计算时显示未知。
        </p>}
        {view !== "settings" && <><button
          type="button"
          onClick={() =>
            void refresh().catch((e: Error) => setError(e.message))
          }
        >
          刷新处理记录
        </button>
        {operations.length === 0 && (
          <p>暂无模型处理记录；纯文字解析和关键词检索不请求模型。</p>
        )}
        {operations.map((operation) => (
          <article
            className="knowledge-operation"
            key={operation.id}
          >
            <strong>
              {operation.action === "index" ? "文档索引" : "检索预览"} ·{" "}
              {statuses[operation.status] || operation.status}
            </strong>
            <p>
              {new Date(operation.created_at).toLocaleString("zh-CN")}
              {operation.source_revision
                ? ` · 文档修订 ${operation.source_revision}`
                : ""}
            </p>
            <TokenUsage
              label="知识处理"
              usage={operation.usage}
            />
            {!operation.usage && <p>未发生模型调用或尚在等待用量。</p>}
          </article>
        ))}</>}
      </details>
    </section>
  );
}

export function DocumentRevisionActions({
  knowledge,
  document,
  canManage,
  onChanged,
}: {
  knowledge: string;
  document: { id: string; revision: number };
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const base = `/capabilities/knowledge/${knowledge}/documents/${document.id}`;
  const [open, setOpen] = useState(false);
  const [revisions, setRevisions] = useState<Revision[]>([]);
  const [revision, setRevision] = useState(document.revision);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [offset, setOffset] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  async function preview(value: number, next = 0) {
    setError("");
    try {
      setChunks(
        (
          await api<{ items: Chunk[] }>(
            `${base}/chunks?revision=${value}&offset=${next}`,
          )
        ).items,
      );
      setRevision(value);
      setOffset(next);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <div className="document-revision-actions">
      {canManage && (
        <label className="upload-label knowledge-upload">
          更新文档
          <input
            aria-label="更新此文档"
            type="file"
            accept=".txt,.md,.pdf,.docx,.xlsx"
            disabled={busy}
            onChange={async (event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (!file) return;
              setBusy(true);
              setError("");
              setNotice("");
              try {
                const body = new FormData();
                body.set("file", file);
                body.set("revision", String(document.revision));
                const result = await api<{
                  duplicate: boolean;
                  revision: number;
                  message?: string;
                }>(base, { method: "PUT", body });
                setNotice(
                  result.duplicate
                    ? result.message || "没有重复导入。"
                    : `已更新为修订 ${result.revision}，旧分块保留供追溯。`,
                );
                await onChanged();
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          />
        </label>
      )}
      <button
        type="button"
        disabled={busy}
        onClick={async () => {
          setError("");
          setOpen(true);
          try {
            setRevisions(
              (await api<{ items: Revision[] }>(base + "/revisions")).items,
            );
            await preview(document.revision);
          } catch (e) {
            setError((e as Error).message);
          }
        }}
      >
        分块与修订历史
      </button>
      {notice && <p role="status">{notice}</p>}
      {!open && error && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      <Dialog.Root
        open={open}
        onOpenChange={setOpen}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="workspace-overlay" />
          <Dialog.Content
            className="workspace-dialog knowledge-revision-dialog"
            aria-describedby={undefined}
          >
            <Dialog.Title>文档分块与修订历史</Dialog.Title>
            <p>
              检索只使用当前修订。历史分块保留原来源；查看历史不会恢复旧内容或再次请求模型。
            </p>
            {error && (
              <p
                role="alert"
                className="platform-error"
              >
                {error}
              </p>
            )}
            <label>
              文档修订
              <select
                aria-label="文档修订"
                value={revision}
                onChange={(e) => void preview(Number(e.target.value))}
              >
                {revisions.map((item) => (
                  <option
                    key={item.revision}
                    value={item.revision}
                  >
                    修订 {item.revision}
                    {item.revision === document.revision
                      ? "（当前）"
                      : ""} · {item.name} · {item.chunks} 块 · {item.chunk_size}
                    /{item.chunk_overlap} 字符 ·{" "}
                    {item.parse_method === "ocr" ? "OCR" : "文字解析"}
                  </option>
                ))}
              </select>
            </label>
            <div className="knowledge-chunk-list">
              {chunks.map((chunk) => (
                <article key={chunk.id}>
                  <strong>
                    片段 {chunk.position + 1} · {chunk.location}
                  </strong>
                  <pre>{chunk.content}</pre>
                </article>
              ))}
            </div>
            <footer>
              <button
                disabled={offset === 0}
                onClick={() => void preview(revision, Math.max(0, offset - 20))}
              >
                上一页分块
              </button>
              <button
                disabled={
                  offset + chunks.length >=
                  (revisions.find((item) => item.revision === revision)
                    ?.chunks || 0)
                }
                onClick={() => void preview(revision, offset + 20)}
              >
                下一页分块
              </button>
              <Dialog.Close asChild>
                <button>关闭</button>
              </Dialog.Close>
            </footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
