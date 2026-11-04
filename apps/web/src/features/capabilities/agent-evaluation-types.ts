export type Page<T> = { items: T[]; next_cursor: string | null };
export type EvaluationCapabilities = {
  default_budgets?: EvaluationBudget;
  permissions: { read: boolean; manage: boolean; run: boolean };
  admission: {
    status: "BLOCKED" | "READY";
    stage_a_accepted: boolean;
    runner_ready: boolean;
    blockers: { code: string; message: string }[];
  };
};
export type EvaluationBudget = {
  max_seconds: number;
  max_model_calls: number;
  max_tokens: number;
  max_sandboxes: number;
};
export type EvaluationDataset = {
  id: string;
  name: string;
  description: string;
  revision: number;
  case_count: number;
  current_version: number | null;
  updated_at: string;
  definition?: unknown;
  versions?: { version: number; notes: string; created_at: string }[];
};
export type EvaluationRun = {
  id: string;
  dataset_id: string;
  version: number;
  status: string;
  gate_verdict: string;
  total_samples: number;
  completed_samples: number;
  created_at: string;
  revision: number;
};
export type EvaluationSample = {
  id: string;
  case_id: string;
  repeat_index: number;
  status: string;
  task_outcome: string | null;
  metric_status: string | null;
  gate_verdict: string;
  cleanup_status: string | null;
};
export type EvaluationComparison = {
  id: string;
  baseline_run_id: string;
  candidate_run_id: string;
  comparable: boolean;
  status: string;
  reasons: string[];
  summary: unknown;
  created_at: string;
};
export type FailureCandidate = {
  id: string;
  name: string;
  status: string;
  revision: number;
  created_at: string;
};

const labels: Record<string, string> = {
  PENDING: "等待执行",
  RUNNING: "执行中",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
  INTERRUPTED: "已中断",
  SUCCEEDED: "任务成功",
  FAILED: "任务失败",
  PASS: "通过",
  FAIL: "不通过",
  NEEDS_REVIEW: "待核验",
  UNKNOWN: "效果未知",
  CLEAN: "已回收",
  ERROR: "评分错误",
  NOT_SCORED: "未评分",
  NOT_APPLICABLE: "不适用",
  SKIPPED: "未执行",
  ENV_UNAVAILABLE: "环境不可用",
  COMPARABLE: "可比较",
  INCOMPARABLE: "不可比较",
  PENDING_REVIEW: "待审核",
  APPROVED: "已批准",
  REJECTED: "已拒绝",
  NOT_RUN: "尚未评分",
  NOT_REQUIRED: "未要求评分",
  COMPLETE: "评分已完成",
  QUEUED: "排队中",
  CANCEL_REQUESTED: "正在取消",
};

export function evaluationLabel(value: string | null | undefined): string {
  return value ? labels[value] || `未识别状态（${value}）` : "未留证";
}

export function evaluationActive(value: string): boolean {
  return ["PENDING", "QUEUED", "RUNNING", "CANCEL_REQUESTED"].includes(value);
}
