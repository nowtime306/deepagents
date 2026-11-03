"use client";

import { ChangeEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type FileInfo = { id: string; path: string; size: number };
export function TaskFiles({
  taskId,
  revision,
  onRevision,
}: {
  taskId: string;
  revision: number;
  onRevision: (revision: number) => void;
}) {
  const [files, setFiles] = useState<FileInfo[]>([]),
    [path, setPath] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function reload() {
    setFiles(
      (await api<{ items: FileInfo[] }>(`/work-tasks/${taskId}/files`)).items,
    );
  }
  useEffect(() => {
    reload().catch((reason) => setError(reason.message));
  }, [taskId]);
  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const selected = Array.from(event.target.files || []);
    event.target.value = "";
    setBusy(true);
    setError("");
    let current = revision;
    try {
      for (const file of selected) {
        if (file.size > 1024 * 1024) throw new Error("单个任务文件最多 1 MB");
        const target = selected.length === 1 && path ? path : `/${file.name}`;
        const result = await fetch(
          `/api/v1/work-tasks/${taskId}/files?path=${encodeURIComponent(target)}&revision=${current}`,
          {
            method: "POST",
            headers: { "Content-Type": "application/octet-stream" },
            body: file,
          },
        );
        const data = await result.json();
        if (!result.ok) throw new Error(data.error?.message || "上传失败");
        current = data.revision;
        onRevision(current);
      }
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>任务输入文件 · {files.length} 个</summary>
      <p>
        单文件最多 1 MB，合计最多 20
        MB。输入文件在提交运行时固定版本；移除或替换只影响新运行，历史输入保留以便追溯。
      </p>
      {error && (
        <p
          className="platform-error"
          role="alert"
        >
          {error}
        </p>
      )}
      <label>
        工作区目标路径
        <input
          aria-label="工作区目标路径"
          maxLength={240}
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="单文件可指定 /src/main.py；留空使用文件名"
        />
      </label>
      <label>
        上传任务文件
        <input
          aria-label="上传任务文件"
          type="file"
          multiple
          disabled={busy}
          onChange={(event) => void upload(event)}
        />
      </label>
      {files.map((file) => (
        <p key={file.id}>
          <a href={`/api/v1/work-input-files/${file.id}/download`}>
            {file.path}
          </a>{" "}
          · {file.size} 字节{" "}
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                const result = await api<{ revision: number }>(
                  `/work-tasks/${taskId}/files/${file.id}?revision=${revision}`,
                  { method: "DELETE" },
                );
                onRevision(result.revision);
                await reload();
              } catch (reason) {
                setError((reason as Error).message);
              } finally {
                setBusy(false);
              }
            }}
          >
            移除输入文件
          </button>
        </p>
      ))}
    </details>
  );
}

export function ArtifactPreview({
  file,
  original,
  close,
}: {
  file: FileInfo;
  original?: FileInfo;
  close: () => void;
}) {
  const [diff, setDiff] = useState("");
  const [text, setText] = useState(""),
    [before, setBefore] = useState(""),
    [image, setImage] = useState(""),
    [error, setError] = useState("");
  useEffect(() => {
    let active = true,
      objectUrl = "";
    setText("");
    setDiff("");
    setBefore("");
    setImage("");
    setError("");
    async function read(url: string) {
      const response = await fetch(url);
      if (!response.ok) throw new Error("文件暂不可用");
      return new Uint8Array(await response.arrayBuffer());
    }
    void (async () => {
      try {
        const data = await read(`/api/v1/work-artifacts/${file.id}/download`);
        if (!active) return;
        const png =
          data[0] === 137 && data[1] === 80 && data[2] === 78 && data[3] === 71;
        const jpeg = data[0] === 255 && data[1] === 216 && data[2] === 255;
        if (png || jpeg) {
          objectUrl = URL.createObjectURL(
            new Blob([data], { type: png ? "image/png" : "image/jpeg" }),
          );
          setImage(objectUrl);
        } else {
          setText(
            new TextDecoder("utf-8", { fatal: true })
              .decode(data)
              .slice(0, 40000),
          );
          const changes = await api<{ diff: string; truncated: boolean }>(
            `/work-artifacts/${file.id}/diff`,
          ).catch(() => null);
          if (active && changes)
            setDiff(
              changes.diff +
                (changes.truncated ? "\n（差异较长，已截断）" : ""),
            );
          if (original) {
            const input = await read(
              `/api/v1/work-input-files/${original.id}/download`,
            );
            if (active)
              setBefore(
                new TextDecoder("utf-8", { fatal: true })
                  .decode(input)
                  .slice(0, 40000),
              );
          }
        }
      } catch {
        if (active) setError("此文件无法作为文本或图片预览，请下载查看。");
      }
    })();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [file.id, original?.id]);
  return (
    <section aria-label="成果预览">
      <h3>{file.path}</h3>
      <button
        type="button"
        onClick={close}
      >
        关闭预览
      </button>
      {error && <p>{error}</p>}
      {image ? (
        <img
          src={image}
          alt="任务浏览器截图或图片成果"
          style={{ maxWidth: "100%" }}
        />
      ) : (
        <div className="artifact-comparison">
          {original && (
            <div>
              <h4>本次初始输入</h4>
              <pre className="work-output">{before}</pre>
            </div>
          )}
          <div>
            <h4>已保存成果</h4>
            <pre className="work-output">{text}</pre>
          </div>
        </div>
      )}
      {diff && (
        <details>
          <summary>查看逐行差异</summary>
          <pre className="work-output">{diff}</pre>
        </details>
      )}
      <small>
        文本预览最多显示 4
        万字符。历史输入与成果分别保留，文件内容不会作为网页执行。
      </small>
    </section>
  );
}
