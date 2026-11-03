"use client";

import { useState } from "react";
import { api } from "@/lib/api/platform";
import "./workflow-approval.css";

export function WorkflowApproval({ approval, name, args }: {
  approval: string; name: string; args: Record<string, unknown>;
}) {
  const [input, setInput] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const code = typeof args.code === "string" ? args.code : "";
  return <div className="workflow-approval-body">
    {name === "workflow_approval" && <p>{String(args.prompt || "请确认是否继续")}</p>}
    {name === "workflow_code" && <>
      <p>批准后执行以下固定代码，最多{String(args.timeout)}秒。</p>
      <pre aria-label="待审批的Python代码">{code}</pre>
    </>}
    <details>
      <summary>查看输入内容 · {String(args.input_characters || 0)}字</summary>
      {input === null ? <>
        <pre>{String(args.input_preview || "")}</pre>
        {Number(args.input_characters) > 2000 && <small>以上仅为前2000字预览。</small>}
        <button type="button" disabled={loading} onClick={async () => {
          setLoading(true); setError("");
          try { setInput((await api<{ text: string }>(`/work-approvals/${approval}/input`)).text); }
          catch (reason) { setError((reason as Error).message); }
          finally { setLoading(false); }
        }}>{loading ? "正在读取…" : "读取已校验的完整输入"}</button>
      </> : <pre>{input}</pre>}
      {error && <p role="alert">{error}</p>}
    </details>
    <details><summary>校验信息</summary><small>输入摘要：{String(args.input_digest || "未提供")}</small>
      {code && <small>代码摘要：{String(args.code_digest || "未提供")}</small>}
    </details>
  </div>;
}
