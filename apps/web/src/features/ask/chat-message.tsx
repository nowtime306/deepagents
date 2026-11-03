"use client";

import { Copy, ChevronDown, Globe } from "lucide-react";
import { memo } from "react";
import { MarkdownText } from "@/components/thread/markdown-text";
import { MessageFeedback } from "./message-feedback";
import { MessageBranches } from "./message-branches";
import { AutomationDraftCard } from "./automation-draft-card";

export type Message = {
  id: string;
  role: string;
  run_id?: string;
  content: {
    context_import?: boolean;
    text?: string;
    attachments?: { id: string; name: string; media_type?: string }[];
    data?: {
      content: unknown;
      name?: string;
      tool_calls?: { name?: string }[];
    };
  };
};
function contentText(message: Message): string {
  const content = message.content.text ?? message.content.data?.content ?? "";
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .map((block) => {
      if (!block || block.type !== "text" || typeof block.text !== "string")
        return "";
      const chars = Array.from(block.text as string);
      const annotations = Array.isArray(block.annotations)
        ? block.annotations
        : [];
      const links: { position: number; url: string }[] = [];
      for (const item of annotations) {
        const raw = item?.url || item?.url_citation?.url;
        const position = item?.end_index ?? item?.url_citation?.end_index;
        try {
          const url = new URL(raw);
          if (
            ["http:", "https:"].includes(url.protocol) &&
            Number.isInteger(position) &&
            position >= 0 &&
            position <= chars.length
          )
            links.push({
              position,
              url: url.href.replaceAll("(", "%28").replaceAll(")", "%29"),
            });
        } catch {
          /* Ignore invalid source URLs. */
        }
      }
      for (const link of links.sort((a, b) => b.position - a.position))
        chars.splice(link.position, 0, ` [来源](${link.url})`);
      return chars.join("");
    })
    .filter(Boolean)
    .join("\n\n");
}

export const ChatMessage = memo(function ChatMessage({
  message,
  error,
  canFeedback = true,
  intermediate = false,
  branch,
}: {
  message: Message;
  error: (text: string) => void;
  canFeedback?: boolean;
  intermediate?: boolean;
  branch?: {
    conversation: string;
    onCreated: (id: string, draft: string) => Promise<void>;
  };
}) {
  const text = contentText(message);
  const calls = message.content.data?.tool_calls || [];
  if (message.role === "tool" || (!text && calls.length)) {
    const search =
      message.content.data?.name?.includes("search") ||
      calls.some((item) => item.name?.includes("search"));
    if (message.role === "tool" && message.content.data?.name === "create_automation_draft") {
      let identifier = "";
      try {
        const draft = JSON.parse(text);
        if (draft.type === "automation_draft" && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(draft.id))
          identifier = draft.id;
      } catch { /* Incomplete streamed tool content is shown below until persisted. */ }
      if (identifier) return <AutomationDraftCard id={identifier}/>;
    }
    return (
      <details className="chat-tool-result">
        <summary>
          {search && <Globe size={14} />} {search ? "联网搜索" : "工具调用"}
          <ChevronDown size={14} />
        </summary>
        <pre>
          {text.trim() === "[]"
            ? "没有找到匹配结果。"
            : text || calls.map((item) => item.name).join("、")}
        </pre>
      </details>
    );
  }
  return (
    <article className={`platform-message ${message.role}`}>
      {message.content.context_import && <small>来自分支历史 · 仅作上下文</small>}
      {message.content.attachments?.length ? (
        <div className="message-attachments">
          {message.content.attachments.map((file) => (
            <a
              key={file.id}
              href={`/api/v1/attachments/${file.id}/download${file.media_type?.startsWith("image/") ? "?inline=true" : ""}`}
              target="_blank"
              rel="noopener noreferrer"
            >
              {file.media_type?.startsWith("image/") && (
                <img
                  src={`/api/v1/attachments/${file.id}/download?inline=true`}
                  alt={file.name}
                  loading="lazy"
                />
              )}
              <span>{file.name}</span>
            </a>
          ))}
        </div>
      ) : null}
      <MarkdownText>{text}</MarkdownText>
      {text && !intermediate && (message.role === "assistant" || branch) && (
        <div className="answer-actions">
          <button
            type="button"
            aria-label={message.role === "user" ? "复制提问" : "复制回答"}
            title={message.role === "user" ? "复制提问" : "复制回答"}
            onClick={() =>
              void navigator.clipboard
                .writeText(text)
                .catch(() => error("复制失败，请手动选择内容复制"))
            }
          >
            <Copy size={15} />
          </button>
          {message.role === "assistant" &&
            canFeedback &&
            /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(message.id) && (
              <MessageFeedback id={message.id} />
            )}
          {branch && /^[0-9a-f]{8}-[0-9a-f-]{27}$/i.test(message.id) && (
            <MessageBranches
              conversation={branch.conversation}
              message={message.id}
              text={text}
              userMessage={message.role === "user"}
              onCreated={branch.onCreated}
              onError={error}
            />
          )}
        </div>
      )}
    </article>
  );
});
