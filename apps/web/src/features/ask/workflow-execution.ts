import type { WorkflowDefinition } from "./workflow-editor";

export type WorkflowRun = {
  id: string; status: string; input: string; output: string | null; created_at: string;
  error_node?: string | null; task_id?: string | null; work_status?: string | null;
  routed?: boolean; routing_run_id?: string;
  snapshot?: { revision?: number; published_version?: number; definition?: WorkflowDefinition;
    canvas_definition?: WorkflowDefinition; debug_node_id?: string; debug_variables?: Record<string, string> };
};
export type NodeEvent = {
  event_id: number; node_id: string; event: string; created_at?: string;
  data: { input?: string; output?: string; duration_ms?: number; error_code?: string;
    iteration?: number; maximum?: number; matched?: boolean };
};
export type NodeExecution = {
  status: string; input?: string; output?: string; duration_ms?: number; error_code?: string;
};
export const executionLabels: Record<string, string> = {
  PENDING: "等待执行", RUNNING: "运行中", SUCCEEDED: "已完成", COMPLETED: "已完成", FAILED: "执行失败",
  HANDLED: "已处理错误", WAITING_APPROVAL: "等待审批", PAUSED: "已暂停", NEEDS_REVIEW: "需要核查",
  CANCELLED: "已取消", INTERRUPTED: "已中断，需核查",
};
export function nodeExecutions(events: NodeEvent[], runStatus?: string): Record<string, NodeExecution> {
  const result: Record<string, NodeExecution> = {};
  for (const event of events) {
    const previous = result[event.node_id] || { status: "PENDING" };
    const status = ({ "node.started": "RUNNING", "node.completed": "SUCCEEDED", "node.failed": "FAILED", "node.handled": "HANDLED" } as Record<string, string>)[event.event];
    result[event.node_id] = { ...previous, ...event.data, status: status || previous.status };
  }
  for (const value of Object.values(result)) {
    if (value.status !== "RUNNING") continue;
    if (["WAITING_APPROVAL", "PAUSED", "NEEDS_REVIEW"].includes(runStatus || "")) value.status = runStatus!;
    else if (["FAILED", "CANCELLED", "SUCCEEDED", "COMPLETED"].includes(runStatus || "")) value.status = "INTERRUPTED";
  }
  return result;
}
