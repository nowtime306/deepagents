"use client";

import { useCallback, useEffect, useState } from "react";
import Image from "next/image";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "@/lib/api/platform";
import { TokenUsage, type Usage } from "@/features/ask/token-usage";

type PageDraft = { page: number; text: string };
type Job = {
  id: string;
  source_name: string;
  page_start: number;
  page_end: number;
  status: string;
  revision: number;
  draft: { pages: PageDraft[] } | null;
  error_code: string | null;
  run_id: string | null;
  usage?: Usage;
};
type SourcePage = { id: string; page: number; name: string; url: string };
const statuses: Record<string, string> = {
  PREPARING: "准备页面中",
  PENDING: "等待或正在识别",
  SUCCEEDED: "待校对",
  IMPORTED: "已确认导入",
  FAILED: "识别失败",
  UNKNOWN: "结果未知，未自动重试",
  CANCELLED: "已取消",
};

export function KnowledgeOCR({
  id,
  canManage,
  onChanged,
}: {
  id: string;
  canManage: boolean;
  onChanged: () => Promise<void>;
}) {
  const base = `/capabilities/knowledge/${id}`;
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [model, setModel] = useState("");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [start, setStart] = useState(1),
    [end, setEnd] = useState(1);
  const [retry, setRetry] = useState("");
  const [acknowledge, setAcknowledge] = useState(false);
  const [canGenerate, setCanGenerate] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [reviewing, setReviewing] = useState<Job | null>(null);
  const [pages, setPages] = useState<PageDraft[]>([]);
  const [images, setImages] = useState<SourcePage[]>([]);
  const reload = useCallback(
    async () => setJobs((await api<{ items: Job[] }>(base + "/ocr")).items),
    [base],
  );
  useEffect(() => {
    void api<{ is_admin?: boolean; permissions?: string[] }>("/auth/me")
      .then((account) =>
        setCanGenerate(
          !!account.is_admin ||
            account.permissions === undefined ||
            ["chat.run", "media.use"].every((permission) =>
              account.permissions!.includes(permission),
            ),
        ),
      )
      .catch((e: Error) => setError(e.message));
    void api<{ items: { id: string; name: string }[] }>(base + "/ocr-models")
      .then((result) => {
        setModels(result.items);
        setModel(result.items[0]?.id || "");
      })
      .catch((e: Error) => setError(e.message));
    const update = () => void reload().catch((e: Error) => setError(e.message));
    update();
    const timer = window.setInterval(update, 4000);
    return () => window.clearInterval(timer);
  }, [base, reload]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section
      className="knowledge-processing"
      aria-label="扫描文档识别"
    >
      <details>
        <summary>扫描件与图片 OCR</summary>
        <p>
          上传 PDF
          或静态图片，选择图片理解模型；本地只准备页面图片，文字识别会发送到所选模型并计入用量。每次1–4页、原文件最多4MB，草稿校对确认后才进入知识库。不保证复杂表格完全还原。
        </p>
        {error && !reviewing && (
          <p
            role="alert"
            className="platform-error"
          >
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {canManage && canGenerate && (
          <form
            className="knowledge-processing-form"
            onSubmit={(event) => {
              event.preventDefault();
              if (!file) return;
              const form = event.currentTarget;
              void act(async () => {
                const body = new FormData();
                body.set("file", file);
                body.set("model_id", model);
                body.set("page_start", String(start));
                body.set("page_end", String(end));
                if (retry) {
                  body.set("retry_of", retry);
                  body.set("acknowledge_duplicate", String(acknowledge));
                }
                const result = await api<{ duplicate: boolean }>(
                  base + "/ocr",
                  { method: "POST", body },
                );
                setNotice(
                  result.duplicate
                    ? "已显示已有记录，本次没有重复请求模型。"
                    : "已提交识别。最多一次模型调用、64000 Token 预算；草稿须校对后导入。",
                );
                setFile(null);
                setRetry("");
                setAcknowledge(false);
                form.reset();
              });
            }}
          >
            <label>
              识别模型
              <select
                aria-label="OCR识别模型"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                disabled={busy}
                required
              >
                <option value="">请选择模型</option>
                {models.map((item) => (
                  <option
                    key={item.id}
                    value={item.id}
                  >
                    {item.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              待识别文件
              <input
                aria-label="待识别文件"
                type="file"
                accept=".pdf,.png,.jpg,.jpeg,.webp"
                required
                disabled={busy}
                onChange={(e) => setFile(e.target.files?.[0] || null)}
              />
            </label>
            <label>
              起始页
              <input
                aria-label="OCR起始页"
                type="number"
                min={1}
                max={100}
                value={start}
                required
                disabled={busy}
                onChange={(e) => setStart(Number(e.target.value))}
              />
            </label>
            <label>
              结束页
              <input
                aria-label="OCR结束页"
                type="number"
                min={start}
                max={Math.min(100, start + 3)}
                value={end}
                required
                disabled={busy}
                onChange={(e) => setEnd(Number(e.target.value))}
              />
            </label>
            <label>
              本次请求
              <select
                aria-label="OCR重新识别记录"
                value={retry}
                disabled={busy}
                onChange={(e) => {
                  setRetry(e.target.value);
                  setAcknowledge(false);
                }}
              >
                <option value="">识别新文件或配置</option>
                {jobs
                  .filter((job) =>
                    ["FAILED", "UNKNOWN", "CANCELLED"].includes(job.status),
                  )
                  .map((job) => (
                    <option
                      value={job.id}
                      key={job.id}
                    >
                      重新识别：{job.source_name} · {statuses[job.status]}
                    </option>
                  ))}
              </select>
            </label>
            {retry && (
              <label className="knowledge-ocr-ack">
                <input
                  type="checkbox"
                  required
                  checked={acknowledge}
                  onChange={(e) => setAcknowledge(e.target.checked)}
                />
                我已核对上次消耗；重新选择原文件及相同页码、模型，确认再次请求可能重复计费
              </label>
            )}
            <button
              disabled={
                busy || !model || !file || (retry !== "" && !acknowledge)
              }
            >
              授权识别并生成草稿
            </button>
          </form>
        )}
        {canManage && !canGenerate && (
          <p>
            发起识别还需要对话运行与媒体使用权限；已有草稿仍可按当前知识权限查看。
          </p>
        )}
        {models.length === 0 && (
          <p>尚无可用的图片理解模型，请在模型管理中配置后再使用。</p>
        )}
        {jobs.map((job) => (
          <article
            className="knowledge-operation"
            key={job.id}
          >
            <strong>
              {job.source_name} · {statuses[job.status] || job.status}
            </strong>
            <p>
              第{job.page_start}–{job.page_end}页
              {job.error_code ? ` · ${job.error_code}` : ""}
            </p>
            <TokenUsage
              label="识别"
              usage={job.usage}
            />
            {job.draft && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(async () => {
                    setImages(
                      (
                        await api<{ items: SourcePage[] }>(
                          `${base}/ocr/${job.id}/pages`,
                        )
                      ).items,
                    );
                    setPages(job.draft!.pages.map((page) => ({ ...page })));
                    setReviewing(job);
                  })
                }
              >
                {job.status === "SUCCEEDED" && canManage
                  ? "校对识别草稿"
                  : "查看识别记录"}
              </button>
            )}
            {canManage && ["PREPARING", "PENDING"].includes(job.status) && (
              <button
                type="button"
                disabled={busy}
                onClick={() =>
                  void act(() =>
                    api(`${base}/ocr/${job.id}/cancel`, { method: "POST" }),
                  )
                }
              >
                取消识别
              </button>
            )}
          </article>
        ))}
      </details>
      <Dialog.Root
        open={!!reviewing}
        onOpenChange={(open) => {
          if (!open && !busy) setReviewing(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="workspace-overlay" />
          <Dialog.Content
            className="workspace-dialog knowledge-ocr-dialog"
            aria-describedby={undefined}
          >
            <Dialog.Title>校对识别草稿</Dialog.Title>
            <p>
              逐页对照原图，修正识别错误。确认导入后会按当前分块策略保存；绑定向量模型的库还会产生索引调用。
            </p>
            {error && (
              <p
                role="alert"
                className="platform-error"
              >
                {error}
              </p>
            )}
            <div className="knowledge-ocr-pages">
              {pages.map((draft, index) => (
                <section key={draft.page}>
                  <h3>第{draft.page}页</h3>
                  <div className="knowledge-ocr-comparison">
                    {images.find((image) => image.page === draft.page) && (
                      <Image
                        src={
                          images.find((image) => image.page === draft.page)!.url
                        }
                        alt={`第${draft.page}页原图`}
                        width={1200}
                        height={900}
                        unoptimized
                      />
                    )}
                    <label>
                      第{draft.page}页识别文字
                      <textarea
                        aria-label={`第${draft.page}页识别文字`}
                        maxLength={12000}
                        rows={10}
                        value={draft.text}
                        readOnly={
                          !canManage || reviewing?.status !== "SUCCEEDED"
                        }
                        onChange={(e) =>
                          setPages((items) =>
                            items.map((item, i) =>
                              i === index
                                ? { ...item, text: e.target.value }
                                : item,
                            ),
                          )
                        }
                      />
                    </label>
                  </div>
                </section>
              ))}
            </div>
            <footer>
              <button
                disabled={busy}
                onClick={() => setReviewing(null)}
              >
                关闭
              </button>
              {reviewing?.status === "SUCCEEDED" && canManage && (
                <button
                  disabled={busy || !pages.some((page) => page.text.trim())}
                  onClick={() =>
                    void act(async () => {
                      await api(`${base}/ocr/${reviewing.id}/import`, {
                        method: "POST",
                        body: JSON.stringify({
                          pages,
                          revision: reviewing.revision,
                        }),
                      });
                      setReviewing(null);
                      setNotice(
                        "已确认导入，请查看文档列表中的 OCR 来源和索引状态。",
                      );
                      await onChanged();
                    })
                  }
                >
                  确认校对并导入
                </button>
              )}
            </footer>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
