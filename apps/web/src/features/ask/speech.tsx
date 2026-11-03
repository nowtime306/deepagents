"use client";

import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Model = { id: string; name: string };
type SpeechOutput = {
  id: string;
  model_name: string;
  voice: string;
  input: string;
  created_at: string;
};

function AudioUnderstanding() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ items: Model[] }>("/speech/understanding-models")
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
        (
          await api<{ text: string }>("/speech/understanding", {
            method: "POST",
            body,
          })
        ).text,
      );
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>音频理解</h2>
      <p>
        音频和问题将发送给服务商，可能产生费用；本页面不保存音频及回答。支持
        WAV、MP3，最多 10 MiB。
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
        <p>暂无可用音频理解模型，请先在模型管理中添加支持音频聊天的模型。</p>
      )}
      <form onSubmit={submit}>
        <p>
          <label>
            模型{" "}
            <select
              aria-label="音频理解模型"
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
            音频{" "}
            <input
              aria-label="理解音频文件"
              type="file"
              accept=".wav,.mp3"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              required
            />
          </label>
        </p>
        <p>
          <label>
            问题{" "}
            <textarea
              aria-label="音频问题"
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
          {busy ? "分析中…" : "分析音频"}
        </button>
      </form>
      {answer && <p aria-label="音频分析结果">{answer}</p>}
    </section>
  );
}

function Transcription() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [text, setText] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<{ items: Model[] }>("/speech/transcription-models")
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
    setText("");
    try {
      const body = new FormData();
      body.append("model_id", model);
      body.append("file", file);
      const result = await api<{ text: string }>("/speech/transcriptions", {
        method: "POST",
        body,
      });
      setText(result.text);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section>
      <h2>语音识别</h2>
      <p>
        音频将发送给所选服务商，可能产生费用；文件最大 10
        MiB，识别结果不会保存。
      </p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!models.length && <p>暂无可用语音识别模型，请先在模型管理中添加。</p>}
      <form onSubmit={submit}>
        <p>
          <label>
            识别模型{" "}
            <select
              aria-label="语音识别模型"
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
            音频文件{" "}
            <input
              aria-label="音频文件"
              type="file"
              accept=".mp3,.wav,.m4a,.mp4,.webm,.ogg,.flac"
              onChange={(event) => setFile(event.target.files?.[0] || null)}
              required
            />
          </label>
        </p>
        <button
          type="submit"
          disabled={busy || !models.length}
        >
          {busy ? "识别中…" : "开始识别"}
        </button>
      </form>
      {text && <p aria-label="识别结果">{text}</p>}
    </section>
  );
}

export function Speech() {
  const [models, setModels] = useState<Model[]>([]);
  const [model, setModel] = useState("");
  const [text, setText] = useState("");
  const [voice, setVoice] = useState("");
  const [items, setItems] = useState<SpeechOutput[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function reload() {
    setItems((await api<{ items: SpeechOutput[] }>("/speech")).items);
  }
  useEffect(() => {
    Promise.all([
      api<{ items: Model[] }>("/speech/models"),
      api<{ items: SpeechOutput[] }>("/speech"),
    ])
      .then(([available, outputs]) => {
        setModels(available.items);
        setModel(available.items[0]?.id || "");
        setItems(outputs.items);
      })
      .catch((reason) => setError(reason.message));
  }, []);
  async function create(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api("/speech", {
        method: "POST",
        body: JSON.stringify({ model_id: model, text, voice }),
      });
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function remove(id: string) {
    if (!window.confirm("确定删除这条音频吗？")) return;
    try {
      await api(`/speech/${id}`, { method: "DELETE" });
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    }
  }
  return (
    <section className="run-history">
      <p>工作空间 / 语音合成</p>
      <h1>语音合成</h1>
      <p>
        文本会发送给所选模型服务商，可能产生费用。请填写该服务商和模型支持的音色。
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
          暂无可用语音合成模型，请先由管理员在模型管理中添加支持 /audio/speech
          的模型。
        </p>
      )}
      <form onSubmit={create}>
        <p>
          <label>
            模型{" "}
            <select
              aria-label="语音合成模型"
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
            音色{" "}
            <input
              aria-label="音色"
              value={voice}
              maxLength={200}
              onChange={(event) => setVoice(event.target.value)}
              placeholder="例如 alloy 或服务商指定的音色"
              required
            />
          </label>
        </p>
        <p>
          <label>
            文本{" "}
            <textarea
              aria-label="合成文本"
              value={text}
              maxLength={4000}
              onChange={(event) => setText(event.target.value)}
              required
            />
          </label>
        </p>
        <button
          type="submit"
          disabled={busy || !models.length}
        >
          {busy ? "生成中…" : "生成语音"}
        </button>
      </form>
      <h2>最近生成</h2>
      {!items.length && <p>还没有生成的音频。</p>}
      {items.map((item) => (
        <article key={item.id}>
          <p>
            {item.model_name} · {item.voice} ·{" "}
            {new Date(item.created_at).toLocaleString("zh-CN")}
          </p>
          <p>{item.input}</p>
          <audio
            controls
            preload="none"
            src={`/api/v1/speech/${item.id}/audio`}
          >
            浏览器不支持播放音频。
          </audio>{" "}
          <a
            href={`/api/v1/speech/${item.id}/audio`}
            download={`speech-${item.id}.mp3`}
          >
            下载
          </a>{" "}
          <button
            type="button"
            onClick={() => void remove(item.id)}
          >
            删除
          </button>
        </article>
      ))}
      <Transcription />
      <AudioUnderstanding />
    </section>
  );
}
