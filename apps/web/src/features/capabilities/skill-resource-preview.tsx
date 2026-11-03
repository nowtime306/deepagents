"use client";

import { MarkdownText } from "@/components/thread/markdown-text";

export function SkillResourcePreview({ path, text }: { path: string; text: string }) {
  if (!path.endsWith(".md")) return <pre>{text}</pre>;
  const header = path === "SKILL.md" ? text.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---(?:\r?\n|$)/) : null;
  return <>{header && <details><summary>技能元信息</summary><pre>{header[1]}</pre></details>}
    <MarkdownText>{header ? text.slice(header[0].length) : text}</MarkdownText></>;
}
