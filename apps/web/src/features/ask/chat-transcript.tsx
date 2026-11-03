"use client";

import { type ComponentProps, Fragment, memo, useEffect, useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { MarkdownText } from "@/components/thread/markdown-text";
import { useRunSnapshot, useLiveSnapshot } from "./run-snapshot";
import { api } from "@/lib/api/platform";
import { ChatMessage, type Message } from "./chat-message";
import { RunDiagnostics } from "./run-diagnostics";
import { TokenUsage, type Usage } from "./token-usage";
import "./chat-transcript.css";

type MessageProps = Pick<ComponentProps<typeof ChatMessage>, "error" | "branch" | "canFeedback">;
type Turn = { key: string; user?: Message; messages: Message[]; runId?: string };
type RunInfo = { created_at?: string; claimed_at?: string; finished_at?: string; status?: string };

function turns(messages: Message[]): Turn[] {
  const result: Turn[] = [];
  for (const message of messages) {
    const last = result.at(-1);
    if (message.role === "user") result.push({ key: message.id, user: message, runId: message.run_id, messages: [] });
    else if (last && (!message.run_id || !last.runId || last.runId === message.run_id)) {
      last.messages.push(message); last.runId ||= message.run_id;
    } else result.push({ key: message.id, runId: message.run_id, messages: [message] });
  }
  return result;
}

export const ChatTranscript = memo(function ChatTranscript({ messages, activeRun, stream, usage, ...props }: MessageProps & {
  messages: Message[]; activeRun: string | null; stream: string; usage?: Usage[];
}) {
  const groups = useMemo(() => {
    const result = turns(messages);
    if (activeRun && !result.some((group) => group.runId === activeRun))
      result.push({ key: activeRun, runId: activeRun, messages: [] });
    return result;
  }, [messages, activeRun]);
  return <>{groups.map((group) => <Fragment key={group.key}>
    {group.user && <ChatMessage message={group.user} {...props}/>}
    {(group.messages.length > 0 || group.runId) && <ChatTurn group={group} active={group.runId === activeRun}
      stream={group.runId === activeRun ? stream : ""} usage={usage?.find((item) => item.run_id === group.runId)} {...props}/>}
  </Fragment>)}</>;
});

const ChatTurn = memo(function ChatTurn({ group, active, stream, usage, ...props }: MessageProps & {
  group: Turn; active: boolean; stream: string; usage?: Usage;
}) {
  const pushed = useRunSnapshot<RunInfo>(group.runId || "", "ask", "run");
  const liveSnapshot = useLiveSnapshot(group.runId || "", "ask");
  const [loaded, setInfo] = useState<RunInfo | null>(null), [now, setNow] = useState(0);
  const info = pushed ?? loaded;
  useEffect(() => {
    if (!group.runId || liveSnapshot) return;
    const controller = new AbortController();
    const load = () => void api<RunInfo>(`/runs/${group.runId}`, { signal: controller.signal }).then((data) => {
      if (!controller.signal.aborted) { setInfo(data); setNow(Date.now()); }
    }).catch(() => { /* Unavailable timing must not hide the conversation or invent a duration. */ });
    load();
    return () => controller.abort();
  }, [group.runId, liveSnapshot]);
  useEffect(() => { if (!active) return; setNow(Date.now()); const clock = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(clock); }, [active]);

  const last = group.messages.at(-1);
  const answer = last?.role === "assistant" && !last.content.data?.tool_calls?.length && !stream ? last : null;
  const process = group.messages.filter((message) => message !== answer);
  const cards = process.filter((message) => message.role === "tool" && message.content.data?.name === "create_automation_draft");
  const steps = process.filter((message) => !cards.includes(message));
  const start = Date.parse(info?.claimed_at || info?.created_at || "");
  const end = active ? now : Date.parse(info?.finished_at || "");
  const seconds = Number.isFinite(start) && Number.isFinite(end) && end >= start ? Math.floor((end - start) / 1000) : null;
  const duration = seconds === null ? "" : seconds < 60 ? `${seconds}秒` : `${Math.floor(seconds / 60)}分${seconds % 60}秒`;
  const label = active ? info?.status === "PENDING" ? "等待执行" : `正在处理${duration ? ` · ${duration}` : "…"}`
    : `${info?.status === "FAILED" ? "执行失败 · " : info?.status === "CANCELLED" ? "已停止 · " : ""}${duration ? `用时 ${duration}` : "处理过程"}`;

  return <section className="chat-turn" data-run-id={group.runId}>
    {(steps.length > 0 || group.runId || active) && <details className="chat-process" open={active}>
      <summary><span>{label}</span><ChevronRight size={14}/>{active && <span className="working-indicator"/>}</summary>
      <div className="chat-process-body">
        {steps.map((message) => <ChatMessage key={message.id} message={message} intermediate {...props}/>)}
        {!steps.length && <p className="chat-process-empty">{active ? "正在等待模型返回…" : "此轮没有额外工具步骤。"}</p>}
        {group.runId && <RunDiagnostics id={group.runId} active={active}/>}
      </div>
    </details>}
    {answer && <ChatMessage message={answer} intermediate={active} {...props}/>}
    {stream && <article className="platform-message assistant chat-stream"><MarkdownText>{stream}</MarkdownText></article>}
    {cards.map((message) => <ChatMessage key={message.id} message={message} intermediate {...props}/>)}
    <TokenUsage usage={usage}/>
  </section>;
});
