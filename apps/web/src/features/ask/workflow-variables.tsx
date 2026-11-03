"use client";
import { useState } from "react";
import { nodeLabels } from "./workflow-editor";
import { executionLabels, nodeExecutions, type NodeEvent, type WorkflowRun } from "./workflow-execution";

function valueType(value: string): string {
  try { JSON.parse(value); return "JSON"; } catch { return "文本"; }
}

export function WorkflowVariables({ run, events, reusable, onReuse }: {
  run: WorkflowRun | null; events: NodeEvent[]; reusable: boolean; onReuse: (node: string) => void;
}) {
  const [notice, setNotice] = useState("");
  if (!run) return <p className="workflow-panel-empty">先运行流程，再查看真实变量。</p>;
  const definition = run.snapshot?.canvas_definition || run.snapshot?.definition;
  const states = nodeExecutions(events, run.work_status || run.status);
  const rows = definition?.nodes.filter(node => !run.snapshot?.debug_node_id || node.id === run.snapshot.debug_node_id) || [];
  async function copy(value: string) {
    try { await navigator.clipboard.writeText(value); setNotice("变量已复制"); }
    catch { setNotice("无法访问剪贴板，请展开并手动复制"); }
  }
  return <section className="workflow-variable-inspector" aria-label="运行变量检查">
    <h3>运行变量</h3><p className="workflow-panel-help">修订 {run.snapshot?.revision ?? "未知"} · 运行 {run.id}。原始记录只读，复用仅填入测试值。</p>
    {!reusable && <p className="workflow-panel-help">只有同修订且没有未保存修改的草稿，才能复用节点输入。</p>}
    {notice && <p role="status">{notice}</p>}
    <details className="workflow-variable"><summary>流程输入 · {valueType(run.input)}</summary><pre>{run.input}</pre><button type="button" onClick={() => void copy(run.input)}>复制流程输入</button></details>
    {rows.map(node => {
      const state = states[node.id];
      const input = state?.input;
      const output = state && ["SUCCEEDED", "HANDLED"].includes(state.status) ? state.output : undefined;
      return <details className="workflow-variable" key={node.id} data-node-id={node.id}>
        <summary><strong>{node.name || nodeLabels[node.kind]}</strong><small>{node.id} · {executionLabels[state?.status] || "未运行"}</small></summary>
        <h4>实际输入{input !== undefined ? ` · ${valueType(input)}` : ""}</h4>{input !== undefined ? <pre>{input}</pre> : <p>尚无实际输入</p>}
        <h4>实际输出{output !== undefined ? ` · ${valueType(output)}` : ""}</h4>{output !== undefined ? <pre>{output}</pre> : <p>没有可用输出</p>}
        {output !== undefined && <button type="button" onClick={() => void copy(output)}>复制输出</button>}
        <button type="button" disabled={!reusable || input === undefined || ["start", "end"].includes(node.kind)} onClick={() => onReuse(node.id)}>将输入用于节点测试</button>
      </details>;
    })}
  </section>;
}
