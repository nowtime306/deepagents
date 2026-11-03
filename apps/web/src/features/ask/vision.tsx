"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Model = { id: string; name: string };
type GeneratedImage = { id: string; model_name: string; prompt: string };

function VideoUnderstanding() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ items: Model[] }>("/vision/video-models")
      .then((result) => {
        setModels(result.items);
        setModel(result.items[0]?.id || "");
      })
      .catch((reason) => setError(reason.message));
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setAnswer("");
    try {
      const body = new FormData();
      body.append("model_id", model);
      body.append("question", question);
      body.append("file", file);
      setAnswer(
        (await api<{ text: string }>("/vision/video", { method: "POST", body }))
          .text,
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>视频理解</h2>
      <p>
        当前支持百炼视频模型，MP4 最大 10
        MiB。视频和问题会发送给服务商，可能产生费用；不会保存视频或回答。
      </p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!models.length && (
        <p>
          暂无可用视频理解模型，请在模型管理中选择百炼服务商并添加支持视频的模型。
        </p>
      )}
      <form onSubmit={submit}>
        <p>
          <label>
            模型{" "}
            <select
              aria-label="视频理解模型"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              required
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
          </label>
        </p>
        <p>
          <label>
            视频{" "}
            <input
              aria-label="视频文件"
              type="file"
              accept="video/mp4"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              required
            />
          </label>
        </p>
        <p>
          <label>
            问题{" "}
            <textarea
              aria-label="视频问题"
              value={question}
              maxLength={4000}
              onChange={(event) => setQuestion(event.target.value)}
              required
            />
          </label>
        </p>
        <button
          type="submit"
          disabled={busy || !models.length}
        >
          {busy ? "分析中…" : "分析视频"}
        </button>
      </form>
      {answer && <p aria-label="视频分析结果">{answer}</p>}
    </section>
  );
}

function ImageGeneration() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("");
  const [images, setImages] = useState<GeneratedImage[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function reload() {
    setImages(
      (await api<{ items: GeneratedImage[] }>("/vision/generated")).items,
    );
  }
  useEffect(() => {
    Promise.all([
      api<{ items: Model[] }>("/vision/generation-models"),
      api<{ items: GeneratedImage[] }>("/vision/generated"),
    ])
      .then(([available, saved]) => {
        setModels(available.items);
        setModel(available.items[0]?.id || "");
        setImages(saved.items);
      })
      .catch((reason) => setError(reason.message));
  }, []);
  async function generate(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/vision/generated", {
        method: "POST",
        body: JSON.stringify({ model_id: model, prompt }),
      });
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(id: string) {
    if (!window.confirm("确定删除这张图片吗？")) return;
    try {
      await api(`/vision/generated/${id}`, { method: "DELETE" });
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <section>
      <h2>图片生成</h2>
      <p>
        提示词将发送给服务商，可能产生费用。支持返回 Base64 PNG
        的接口，以及使用指定官方存储地址返回 PNG 的硅基流动接口。
      </p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!models.length && <p>暂无可用图片生成模型，请先在模型管理中添加。</p>}
      <form onSubmit={generate}>
        <p>
          <label>
            模型{" "}
            <select
              aria-label="图片生成模型"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              required
            >
              {models.map((item) => (
                <option
                  value={item.id}
                  key={item.id}
                >
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </p>
        <p>
          <label>
            提示词{" "}
            <textarea
              aria-label="图片提示词"
              value={prompt}
              maxLength={4000}
              onChange={(event) => setPrompt(event.target.value)}
              required
            />
          </label>
        </p>
        <button
          type="submit"
          disabled={busy || !models.length}
        >
          {busy ? "生成中…" : "生成图片"}
        </button>
      </form>
      <h3>最近生成</h3>
      {!images.length && <p>还没有生成的图片。</p>}
      {images.map((item) => (
        <article key={item.id}>
          <p>
            {item.model_name} · {item.prompt}
          </p>
          {/* The source is an authenticated local API endpoint, not an external URL. */}
          <img
            src={`/api/v1/vision/generated/${item.id}/image`}
            alt={item.prompt}
            style={{ maxWidth: "100%", maxHeight: 400 }}
          />
          <p>
            <a
              href={`/api/v1/vision/generated/${item.id}/image`}
              download={`image-${item.id}.png`}
            >
              下载
            </a>{" "}
            <button
              type="button"
              onClick={() => void remove(item.id)}
            >
              删除
            </button>
          </p>
        </article>
      ))}
    </section>
  );
}
export function Vision() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ items: Model[] }>("/vision/models")
      .then((result) => {
        setModels(result.items);
        setModel(result.items[0]?.id || "");
      })
      .catch((reason) => setError(reason.message));
  }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!file) return;
    setBusy(true);
    setError("");
    setAnswer("");
    try {
      const body = new FormData();
      body.append("model_id", model);
      body.append("question", question);
      body.append("file", file);
      setAnswer(
        (await api<{ text: string }>("/vision", { method: "POST", body })).text,
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="run-history">
      <p>工作空间 / 图片理解</p>
      <h1>图片理解</h1>
      <p>
        图片和问题将发送给所选模型服务商，可能产生费用；本页面不保存图片及回答。
      </p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!models.length && (
        <p>暂无可用图片理解模型，请先在模型管理中添加支持图文聊天的模型。</p>
      )}
      <form onSubmit={submit}>
        <p>
          <label>
            模型{" "}
            <select
              aria-label="图片理解模型"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              required
            >
              {models.map((item) => (
                <option
                  value={item.id}
                  key={item.id}
                >
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        </p>
        <p>
          <label>
            图片{" "}
            <input
              aria-label="图片文件"
              type="file"
              accept="image/png,image/jpeg,image/webp"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              required
            />
          </label>
          （PNG、JPEG、WebP，最多 5 MiB）
        </p>
        <p>
          <label>
            问题{" "}
            <textarea
              aria-label="图片问题"
              value={question}
              maxLength={4000}
              onChange={(event) => setQuestion(event.target.value)}
              required
            />
          </label>
        </p>
        <button
          type="submit"
          disabled={busy || !models.length}
        >
          {busy ? "分析中…" : "分析图片"}
        </button>
      </form>
      {answer && (
        <div>
          <h2>分析结果</h2>
          <p aria-label="分析结果">{answer}</p>
        </div>
      )}
      <ImageGeneration />
      <VideoUnderstanding />
    </section>
  );
}
