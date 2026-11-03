"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import { statuses } from "@/locales/zh-CN/platform";
import { RunDiagnostics } from "./run-diagnostics";

type Run = {
  id: string;
  conversation_id: string;
  conversation_title: string;
  model_id: string;
  status: string;
  created_at: string;
  finished_at: string | null;
};

export function RunHistory({ onOpen }: { onOpen: (id: string) => void }) {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ items: Run[] }>("/runs")
      .then((data) => setRuns(data.items))
      .catch((reason) => setError(reason.message));
  }, []);
  return (
    <section className="run-history">
      <p>工作空间 / 运行记录</p>
      <h1>运行记录</h1>
      <p>查看当前账号最近的 100 次运行，打开对应对话查看消息和工具结果。</p>
      {error && (
        <div
          role="alert"
          className="platform-error"
        >
          {error}
        </div>
      )}
      {!runs && !error && <p>正在读取运行记录…</p>}
      {runs?.length === 0 && (
        <p>还没有运行记录，开始一次智能对话后将在这里显示。</p>
      )}
      {runs?.map((run) => (
        <div key={run.id}>
        <article className="run-history-card">
          <div>
            <strong>{run.conversation_title}</strong>
            <p>
              {statuses[run.status] || run.status} ·{" "}
              {new Date(run.created_at).toLocaleString("zh-CN")}
            </p>
          </div>
          <button onClick={() => onOpen(run.conversation_id)}>查看对话</button>
        </article>
        <RunDiagnostics id={run.id} active={["PENDING", "RUNNING"].includes(run.status)} />
        </div>
      ))}
    </section>
  );
}
