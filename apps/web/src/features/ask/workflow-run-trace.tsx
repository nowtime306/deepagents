"use client";

import { nodeLabels } from "./workflow-editor";
import { executionLabels, nodeExecutions, type NodeEvent, type WorkflowRun } from "./workflow-execution";
import { RouteSelection } from "@/features/settings/model-routes";
import { RunConfigurations } from "./run-configurations";

export function WorkflowRunTrace({ run, events, selected, onSelect, onTask }: {
  run: WorkflowRun | null; events: NodeEvent[]; selected: string;
  onSelect: (node: string) => void; onTask: (task: string) => void;
}) {
  if (!run) return <p className="workflow-panel-empty">运行后，节点状态、输入和输出将显示在这里。</p>;
  const definition = run.snapshot?.canvas_definition || run.snapshot?.definition;
  const states = nodeExecutions(events, run.work_status || run.status);
  const debug = run.snapshot?.debug_node_id;
  const debugState = debug ? states[debug] : undefined;
  const identifiers = [...new Set(events.map((event) => event.node_id))].filter((id) => !run.snapshot?.debug_node_id || id === run.snapshot.debug_node_id || id.startsWith(run.snapshot.debug_node_id + "."));
  const name = (id: string) => {
    const node = definition?.nodes.find((item) => item.id === id);
    return node ? node.name || nodeLabels[node.kind] : id;
  };
  const details = events.filter((event) => !selected || event.node_id === selected);
  return <section className="workflow-trace" aria-label="节点运行情况">
    <div className="workflow-run-summary" data-status={run.work_status || run.status}>
      <strong>{executionLabels[run.work_status || run.status] || "运行状态未知"}</strong>
      <span>{run.snapshot?.debug_node_id ? `单节点测试 · ${name(run.snapshot.debug_node_id)}` : run.snapshot?.published_version ? `已发布版本 ${run.snapshot.published_version}` : `草稿修订 ${run.snapshot?.revision ?? "未知"}`}</span>
      <small>{new Date(run.created_at).toLocaleString("zh-CN")}</small>
    </div>
    {run.task_id && <button type="button" className="workflow-secondary" onClick={() => onTask(run.task_id!)}>查看任务与审批</button>}
    {run.routed && <RouteSelection run={run.routing_run_id || run.id}/>}
    <RunConfigurations id={run.id} mode="workflow"/>
    {debug && <div className="workflow-debug-input" aria-label="节点实际输入"><strong>实际输入</strong><pre>{debugState?.input ?? run.input}</pre></div>}
    {debugState?.duration_ms !== undefined && <p className="workflow-debug-duration">节点耗时：{debugState.duration_ms} ms</p>}
    {debugState?.error_code && <p role="alert">节点测试失败：{debugState.error_code}。请检查节点配置和输入后重新测试。</p>}
    {debug && definition?.nodes.find((node) => node.id === debug)?.kind === "condition" && ["true", "false"].includes(debugState?.output || "") &&
      <p>分支结果：{debugState?.output === "true" ? "命中" : "默认"}</p>}
    <div className="workflow-run-output" aria-label={debug ? "节点实际输出" : "流程运行结果"}><strong>{debug ? "节点输出" : "运行结果"}</strong>
      {run.output !== null ? <pre>{run.output}</pre> : <p>{run.status === "FAILED" ? "本次运行没有完整结果，请查看失败节点。" : "尚未返回最终结果。"}</p>}
    </div>
    <div className="workflow-trace-heading"><h3>节点轨迹</h3><button type="button" onClick={() => onSelect("")}>查看全部</button></div>
    <div className="workflow-trace-list">{identifiers.map((id) => <button key={id} type="button" aria-pressed={selected === id}
      onClick={() => onSelect(id)} data-node={id} data-status={states[id]?.status}>
      <span className="workflow-state-dot"/><span>{name(id)}<small>{executionLabels[states[id]?.status] || "等待执行"}</small></span>
      <small>{states[id]?.duration_ms !== undefined ? `${states[id].duration_ms} ms` : ""}</small>
    </button>)}</div>
    {!identifiers.length && <p className="workflow-panel-empty">尚无节点事件，正在等待执行记录。</p>}
    {details.map((event) => <details className="workflow-event" key={event.event_id}>
      <summary>{name(event.node_id)} · {event.event === "loop.progress"
        ? `第${event.data.iteration}/${event.data.maximum}轮 · ${event.data.matched ? "已满足停止条件" : "尚未满足停止条件"}`
        : ({ "node.started": "开始执行", "node.completed": "执行完成", "node.handled": "已转入错误分支", "node.failed": "执行失败" } as Record<string, string>)[event.event] || "状态更新"}
        {event.data.duration_ms !== undefined ? ` · ${event.data.duration_ms} 毫秒` : ""}</summary>
      {event.data.input !== undefined && <><h4>输入</h4><pre>{event.data.input}</pre></>}
      {event.data.output !== undefined && <><h4>输出</h4><pre>{event.data.output}</pre></>}
      {event.data.error_code && <p role="status">{event.data.error_code === "WORKFLOW_LOOP_LIMIT" ? "已达到循环次数上限，仍未满足停止条件；未自动继续。"
        : event.data.error_code === "WORKFLOW_ITERATION_INPUT" || event.data.error_code === "WORKFLOW_ITERATION_LIMIT" ? "迭代输入必须为有效且不超过配置上限的JSON数组。"
        : "节点执行失败，请检查输入及资源配置。"}<small>{event.data.error_code}</small></p>}
    </details>)}
  </section>;
}
