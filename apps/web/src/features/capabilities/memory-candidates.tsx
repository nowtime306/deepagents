"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";
import { RunDiagnostics } from "@/features/ask/run-diagnostics";
import { TokenUsage, type Usage } from "@/features/ask/token-usage";
import { MemoryScopeEditor } from "./memory-scope";
import {
  emptyScope,
  scopePayload,
  type ScopeOptions,
} from "./memory-scope-values";
import "./memory-governance.css";

type Candidate = {
  id: string;
  title: string;
  content: string;
  status: string;
  revision: number;
  source_title: string;
  source_message_ids: string[];
  source_messages?: { id: string; text: string }[];
};
type Job = {
  id: string;
  source_title: string;
  run_id: string | null;
  status: string;
  error_code: string | null;
  usage?: Usage;
};
type Message = { id: string; role: string; content: { text?: string } };
const statuses: Record<string, string> = {
  PENDING: "待处理",
  SUCCEEDED: "提取完成",
  FAILED: "提取失败",
  UNKNOWN: "结果未知，不自动重试",
  CANCELLED: "已取消",
  ACCEPTED: "已确认",
  REJECTED: "已拒绝",
};

export function MemoryCandidates({
  canManage,
  canGenerate,
  canReadChat,
  options,
  onAccepted,
}: {
  canManage: boolean;
  canGenerate: boolean;
  canReadChat: boolean;
  options: ScopeOptions;
  onAccepted: () => Promise<void>;
}) {
  const [extractOpen, setExtractOpen] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [conversations, setConversations] = useState<
    { id: string; title: string }[]
  >([]);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [conversation, setConversation] = useState("");
  const [model, setModel] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [editing, setEditing] = useState<Candidate | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [scope, setScope] = useState(emptyScope);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);
  const reload = useCallback(async () => {
    const generated = await api<{ items: Job[] }>("/memories/jobs");
    setJobs(generated.items);
    setCandidates(
      (await api<{ items: Candidate[] }>("/memories/candidates")).items,
    );
  }, []);
  useEffect(() => {
    const refresh = () =>
      void reload().catch((e: Error) => setError(e.message));
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => window.clearInterval(timer);
  }, [reload]);
  useEffect(() => {
    if (!canGenerate) return;
    void Promise.all([
      api<{ items: { id: string; title: string }[] }>("/conversations"),
      api<{ items: { id: string; name: string }[] }>("/models"),
    ])
      .then(([c, m]) => {
        setConversations(c.items);
        setModels(m.items);
        setModel(m.items[0]?.id || "");
      })
      .catch((e: Error) => setError(e.message));
  }, [canGenerate]);
  useEffect(() => {
    if (!conversation) return;
    let current = true;
    void api<{ items: Message[] }>(`/conversations/${conversation}/messages`)
      .then((result) => {
        if (current) {
          const eligible = result.items
            .filter((m) => m.role === "user" && m.content.text)
            .slice(-20);
          setMessages(eligible);
          setSelected(eligible.map((m) => m.id));
        }
      })
      .catch((e: Error) => setError(e.message));
    return () => {
      current = false;
    };
  }, [conversation]);
  async function act(operation: () => Promise<unknown>) {
    setPending(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  }
  function extract(e: FormEvent) {
    e.preventDefault();
    void act(async () => {
      const result = await api<{ duplicate: boolean }>("/memories/jobs", {
        method: "POST",
        body: JSON.stringify({
          conversation_id: conversation,
          model_id: model,
          source_message_ids: selected,
        }),
      });
      setExtractOpen(false);
      setNotice(
        result.duplicate
          ? "此来源和模型配置已提取过，已显示原记录，没有重复请求模型。"
          : "已提交提取。候选须经你确认才会生效。最多两次模型调用，24000 Token 预算。",
      );
    });
  }
  function review(e: FormEvent) {
    e.preventDefault();
    if (!editing) return;
    void act(async () => {
      const result = await api<{ duplicate: boolean }>(
        `/memories/candidates/${editing.id}/review`,
        {
          method: "POST",
          body: JSON.stringify({
            decision: "accept",
            revision: editing.revision,
            title,
            content,
            ...scopePayload(scope),
          }),
        },
      );
      setEditing(null);
      setNotice(
        result.duplicate
          ? "已关联原有相同记忆，没有重复保存，也未改变原有启用状态或期限。"
          : "已确认，记忆现在可以按范围参与召回。",
      );
      await onAccepted();
    });
  }
  return (
    <section
      className="memory-governance"
      aria-label="记忆候选管理"
    >
      <header className="resource-list-heading"><h2>记忆候选</h2>{canGenerate && <button className="settings-primary" onClick={() => { setError(""); setExtractOpen(true); }}>从会话生成记忆候选</button>}</header>
      <p>
        只提取你授权的用户文字。候选仅作为背景资料，确认前不参与召回；密码、密钥和临时问题不应保存。
      </p>
      {error && !extractOpen && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {canGenerate && extractOpen && (
        <ResourceDialog title="从会话生成记忆候选" busy={pending} close={() => setExtractOpen(false)}>{error && <p role="alert">{error}</p>}<form
          className="memory-extraction-form"
          onSubmit={extract}
        >
          <label>
            来源会话
            <select
              aria-label="来源会话"
              required
              value={conversation}
              onChange={(e) => {
                setConversation(e.target.value);
                setMessages([]);
                setSelected([]);
              }}
            >
              <option value="">请选择最近的会话</option>
              {conversations.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                >
                  {item.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            提取模型
            <select
              aria-label="提取模型"
              required
              value={model}
              onChange={(e) => setModel(e.target.value)}
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
          {messages.length > 0 && (
            <fieldset>
              <legend>授权来源消息（最多最近 20 条）</legend>
              {messages.map((message, index) => (
                <label
                  className="memory-message-choice"
                  key={message.id}
                >
                  <input
                    type="checkbox"
                    checked={selected.includes(message.id)}
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [...selected, message.id]
                          : selected.filter((id) => id !== message.id),
                      )
                    }
                  />
                  <span>
                    消息 {index + 1}：{message.content.text?.slice(0, 180)}
                  </span>
                </label>
              ))}
            </fieldset>
          )}
          <p>
            生成候选会调用所选模型并计入用量；不会自动重试未知请求或启用记忆。
          </p>
          <button
            disabled={pending || !model || !conversation || !selected.length}
            className="settings-primary"
          >
            授权并生成候选
          </button>
        </form></ResourceDialog>
      )}
      <div className="memory-candidate-list">
        {candidates.map((candidate) => (
          <article key={candidate.id}>
            <header>
              <h3>{candidate.title}</h3>
              <small>{statuses[candidate.status] || candidate.status}</small>
            </header>
            <p className="memory-preserve">{candidate.content}</p>
            <details>
              <summary>来源：{candidate.source_title}</summary>
              {candidate.source_messages?.map((message) => (
                <blockquote
                  className="memory-preserve"
                  key={message.id}
                >
                  {message.text}
                  <small>（来源摘录，最多2000字符）</small>
                </blockquote>
              ))}
            </details>
            {canManage && candidate.status === "PENDING" && (
              <footer>
                <button
                  disabled={pending}
                  onClick={() => {
                    setEditing(candidate);
                    setTitle(candidate.title);
                    setContent(candidate.content);
                    setScope(emptyScope);
                  }}
                >
                  审阅并确认
                </button>
                <button
                  disabled={pending}
                  onClick={() =>
                    void act(() =>
                      api(`/memories/candidates/${candidate.id}/review`, {
                        method: "POST",
                        body: JSON.stringify({
                          decision: "reject",
                          revision: candidate.revision,
                        }),
                      }),
                    )
                  }
                >
                  拒绝候选
                </button>
              </footer>
            )}
          </article>
        ))}
      </div>
      {jobs.length > 0 && (
        <details className="memory-job-history">
          <summary>提取记录与消耗（{jobs.length}）</summary>
          {jobs.map((job) => (
            <article key={job.id}>
              <h3>{job.source_title}</h3>
              <p>
                {statuses[job.status] || job.status}
                {job.error_code ? ` · ${job.error_code}` : ""}
              </p>
              <TokenUsage
                label="提取"
                usage={job.usage}
              />
              {canManage && job.status === "PENDING" && (
                <button
                  disabled={pending}
                  onClick={() =>
                    void act(() =>
                      api(`/memories/jobs/${job.id}/cancel`, {
                        method: "POST",
                      }),
                    )
                  }
                >
                  取消提取
                </button>
              )}
              {canReadChat && job.run_id && (
                <RunDiagnostics
                  id={job.run_id}
                  active={job.status === "PENDING"}
                />
              )}
            </article>
          ))}
        </details>
      )}
      <Dialog.Root
        open={!!editing}
        onOpenChange={(open) => {
          if (!open && !pending) setEditing(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="workspace-overlay" />
          <Dialog.Content
            className="workspace-dialog memory-review-dialog"
            aria-describedby={undefined}
          >
            <Dialog.Title>审阅记忆候选</Dialog.Title>
            <p>确认后按指定范围生效；冲突时须改名或明确编辑旧记忆。</p>
            <form onSubmit={review}>
              <label>
                候选标题
                <input
                  required
                  maxLength={120}
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                />
              </label>
              <label>
                候选内容
                <textarea
                  required
                  rows={5}
                  maxLength={4000}
                  value={content}
                  onChange={(e) => setContent(e.target.value)}
                />
              </label>
              <MemoryScopeEditor
                value={scope}
                options={options}
                onChange={setScope}
              />
              {error && (
                <p
                  role="alert"
                  className="platform-error"
                >
                  {error}
                </p>
              )}
              <footer>
                <button
                  type="button"
                  disabled={pending}
                  onClick={() => setEditing(null)}
                >
                  取消
                </button>
                <button
                  disabled={pending}
                  className="settings-primary"
                >
                  确认保存记忆
                </button>
              </footer>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
