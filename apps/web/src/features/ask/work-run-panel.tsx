"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";
import type { ResourcePage } from "./use-paged-resources";
import { TaskFiles, ArtifactPreview } from "./task-files";
import { AgentVersionPicker } from "./agent-versions";
import { DraftEstimate, WorkTokenUsage } from "./token-usage";
import { RunDiagnostics } from "./run-diagnostics";
import { BrowserWorkbench } from "./browser-workbench";
import { WorkflowApproval } from "./workflow-approval";
import { RepositoryPicker, type RepositoryPermissions } from "./repository-picker";
import { RepositoryReview } from "./repository-review";
import { nodeLabels } from "./workflow-editor";

type Todo = { content: string; status: string };
type Run = {
  id: string;
  status: string;
  environment?: string;
  created_at: string;
  error_code?: string;
  pause_requested?: boolean;
  model_calls?: number;
  step_limit?: number;
  token_limit?: number;
  network_domains?: string[];
  used_tokens?: number;
  input_files?: { id: string; path: string; size: number }[];
  result?: { text: string; todos?: { content: string; status: string }[] };
};
type Approval = {
  id: string;
  digest: string;
  expires_at: string;
  decision: string | null;
  payload: {
    action_requests: {
      name: string;
      args: Record<string, unknown>;
      description?: string;
    }[];
  };
};
type Recovery = {
  job: { status: string; not_before: string; error_code?: string } | null;
  sessions: {
    kind: string;
    state: string;
    retention_required: boolean;
    data_lost: boolean;
  }[];
};
type Operation = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: string;
  exit_code: number | null;
  output: string | null;
};
type Artifact = { id: string; path: string; size: number };
type Event = {
  event_id: number;
  event: string;
  data: {
    node_id?: string;
    node_name?: string;
    kind?: string;
    event?: string;
    error_code?: string;
    text?: string;
    nodes?: string[];
    name?: string;
    output?: string;
    truncated?: boolean;
    todos?: Todo[];
  };
};
const labels: Record<string, string> = {
  PENDING: "待执行",
  RUNNING: "运行中",
  WAITING_APPROVAL: "等待审批",
  PAUSED: "已暂停",
  COMPLETED: "已完成",
  FAILED: "执行失败",
  CANCELLED: "已取消",
  NEEDS_REVIEW: "需要核查",
};
const workflowErrors: Record<string, string> = {
  WORKFLOW_APPROVAL_REJECTED: "此流程操作未获批准，后续节点没有执行。",
  WORKFLOW_CODE_FAILED: "流程代码返回失败，已停止后续节点；请查看操作输出。",
  WORKFLOW_CODE_OUTPUT_LIMIT: "流程代码输出过长或被截断，已停止后续节点。",
  WORKFLOW_CODE_FILE_UNSAFE: "代码或输入路径无法安全写入，脚本没有执行。",
  WORKFLOW_BINDING_MISSING: "固定流程绑定不可用，任务不会改为普通智能体执行。",
  WORKFLOW_RESOURCE_CHANGED: "流程的模型或资源发生变化，已停止，请核对后创建新任务。",
  WORKFLOW_NODE_BUDGET_EXCEEDED: "流程节点预算已用完，已停止后续执行。",
};

export function WorkRunPanel({
  task,
  close,
  repositoryPermissions = {},
}: {
  task: { id: string; title: string; revision: number; workflow_task?: boolean };
  close: () => void;
  repositoryPermissions?: RepositoryPermissions;
}) {
  const [repository, setRepository] = useState("");
  const [revision, setRevision] = useState(task.revision);
  const [restore, setRestore] = useState(false);
  const [preview, setPreview] = useState<Artifact | null>(null);
  const [models, setModels] = useState<{ id: string; name: string }[]>([]);
  const [model, setModel] = useState("");
  const [modelRouting, setModelRouting] = useState(false);
  const [agents, setAgents] = useState<
    { id: string; name: string; model_id: string; enabled: boolean }[]
  >([]);
  const [agent, setAgent] = useState("");
  const [agentVersion, setAgentVersion] = useState("");
  const [versionModel, setVersionModel] = useState("");
  const [stepLimit, setStepLimit] = useState(40);
  const [tokenLimit, setTokenLimit] = useState(80000);
  const [networkDomains, setNetworkDomains] = useState("");
  const [environment, setEnvironment] = useState("coding");
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [runCursor, setRunCursor] = useState<string | null>(null), [historyLoading, setHistoryLoading] = useState(false);
  const olderLoaded = useRef(false);
  const [selected, setSelected] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [recovery, setRecovery] = useState<Recovery | null>(null);
  const [operations, setOperations] = useState<Operation[]>([]);
  const [events, setEvents] = useState<Event[]>([]);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [approvalCheckedAt, setApprovalCheckedAt] = useState(0);

  useEffect(() => {
    let active = true;
    Promise.all([
      api<{ items: { id: string; name: string }[] }>("/models"),
      api<{ enabled: boolean }>("/work-capabilities"),
      api<{
        items: {
          id: string;
          name: string;
          model_id: string;
          enabled: boolean;
        }[];
      }>("/agents"),
    ])
      .then(([m, c, a]) => {
        if (active) {
          setModels(m.items);
          setModel(m.items[0]?.id || "");
          setEnabled(c.enabled);
          setAgents(a.items.filter((item) => item.enabled));
        }
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      try {
        const [history, readiness] = await Promise.all([
          api<ResourcePage<Run>>(`/work-tasks/${task.id}/runs`),
          api<{ enabled: boolean }>("/work-capabilities"),
        ]);
        if (!active) return;
        setEnabled(readiness.enabled);
        setRuns((previous) => [...history.items, ...previous.filter((item) => !history.items.some((next) => next.id === item.id))]);
        if (!olderLoaded.current) setRunCursor(history.next_cursor || null);
        if (!selected && history.items.length) setSelected(history.items[0].id);
        if (selected) {
          const [r, a, f, o, recoveryInfo] = await Promise.all([
            api<Run>(`/work-runs/${selected}`),
            api<{ items: Approval[] }>(`/work-runs/${selected}/approvals`),
            api<{ items: Artifact[] }>(`/work-runs/${selected}/artifacts`),
            api<{ items: Operation[] }>(`/work-runs/${selected}/operations`),
            api<Recovery>(`/work-runs/${selected}/recovery`),
          ]);
          let after = 0;
          const collected: Event[] = [];
          while (active && collected.length < 2000) {
            const batch = await api<{ items: Event[]; next_after: number }>(
              `/work-runs/${selected}/events?after=${after}`,
            );
            collected.push(...batch.items);
            after = batch.next_after;
            if (batch.items.length < 100) break;
          }
          if (!active) return;
          setRun(r);
          setApprovals(a.items);
          setApprovalCheckedAt(Date.now());
          setArtifacts(f.items);
          setOperations(o.items);
          setRecovery(recoveryInfo);
          setEvents(collected);
        }
      } catch (reason) {
        if (active) setError((reason as Error).message);
      }
      if (active) timer = setTimeout(poll, 2000);
    }
    setRun(null);
    setApprovals([]);
    setArtifacts([]);
    setOperations([]);
    setRecovery(null);
    setEvents([]);
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [task.id, selected]);

  async function action(work: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function moreRuns() {
    if (!runCursor || historyLoading) return;
    setHistoryLoading(true);
    try {
      const next = await api<ResourcePage<Run>>(`/work-tasks/${task.id}/runs?before=${encodeURIComponent(runCursor)}`);
      olderLoaded.current = true;
      setRuns((previous) => [...previous, ...next.items.filter((item) => !previous.some((old) => old.id === item.id))]);
      setRunCursor(next.next_cursor || null);
    } catch (reason) { setError((reason as Error).message); } finally { setHistoryLoading(false); }
  }
  return (
    <section
      className="work-execution"
      aria-label="任务执行详情"
    >
      <div className="work-execution-heading">
        <h2>{task.title}</h2>
        <button
          type="button"
          onClick={close}
        >
          关闭详情
        </button>
      </div>
      <p>
        {task.workflow_task ? "固定流程按节点顺序执行，审批通过后才运行代码；关闭页面后后台继续。"
          : "命令和删除操作需要审批。默认禁止出站，可在提交前授权公网域名；关闭页面后，已提交任务由后台继续处理。"}
      </p>
      {enabled === false && (
        <p role="status">当前环境尚未开放任务执行，可以查看已有记录。</p>
      )}
      {error && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      {!task.workflow_task && <DraftEstimate request={{
        mode: "work", model_id: versionModel || agents.find((item) => item.id === agent)?.model_id || model,
        task_id: task.id, agent_id: agent || null, agent_version_id: agentVersion || null,
      }} />}
      {task.workflow_task ? <p>这是固定流程任务。请在工作流页面明确选择输入、版本和网络授权后创建新运行。</p> : <form
        className="work-run-controls"
        onSubmit={(event) => {
          event.preventDefault();
          void action(async () => {
            const result = await api<{ run_id: string }>(
              `/work-tasks/${task.id}/runs`,
              {
                method: "POST",
                body: JSON.stringify({
                  model_id:
                    agents.find((item) => item.id === agent)?.model_id || model,
                  agent_id: agent || null,
                  model_routing: modelRouting,
                  agent_version_id: agentVersion || null,
                  step_limit: stepLimit,
                  token_limit: tokenLimit,
                  network_domains: networkDomains
                    .split(/[,，\s]+/)
                    .filter(Boolean),
                  revision,
                  restore_from_run: restore ? selected : null,
                  environment,
                  repository_id: environment === "coding" ? repository || null : null,
                }),
              },
            );
            setSelected(result.run_id);
          });
        }}
      >
        <label>
          执行智能体
          <select
            aria-label="执行智能体"
            value={agent}
            onChange={(event) => {
              setAgent(event.target.value);
              setAgentVersion("");
              setVersionModel("");
            }}
          >
            <option value="">通用助手</option>
            {agents.map((item) => (
              <option
                key={item.id}
                value={item.id}
              >
                {item.name}
              </option>
            ))}
          </select>
        </label>
        <AgentVersionPicker
          agent={agent}
          value={agentVersion}
          change={setAgentVersion}
          model={setVersionModel}
        />
        <label>
          执行模型
          <select
            aria-label="执行模型"
            disabled={!!agent}
            value={
              versionModel ||
              agents.find((item) => item.id === agent)?.model_id ||
              model
            }
            onChange={(event) => setModel(event.target.value)}
          >
            {!models.length && <option value="">请先配置聊天模型</option>}
            {models.map((m) => (
              <option
                value={m.id}
                key={m.id}
              >
                {m.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          {!agent && <span><input type="checkbox" checked={modelRouting} onChange={e => setModelRouting(e.target.checked)}/>使用已确认的模型路由</span>}
          任务环境
          <select
            aria-label="任务环境"
            value={environment}
            onChange={(event) => setEnvironment(event.target.value)}
          >
            <option value="coding">编程与文件处理</option>
            <option value="browser">浏览器与文件处理</option>
          </select>
        </label>
        {repositoryPermissions.use && environment === "coding" && <RepositoryPicker value={repository} onChange={setRepository} />}
        <button
          className="settings-primary"
          disabled={!enabled || !model || busy}
        >
          开始执行
        </button>
      </form>}
      {!task.workflow_task && <><details>
        <summary>执行预算</summary>
        <label>
          模型调用次数上限
          <input
            type="number"
            aria-label="模型调用次数上限"
            min={5}
            max={100}
            value={stepLimit}
            onChange={(event) => setStepLimit(Number(event.target.value))}
          />
        </label>
        <label>
          Token 用量上限
          <input
            type="number"
            aria-label="Token 用量上限"
            min={1000}
            max={1000000}
            value={tokenLimit}
            onChange={(event) => setTokenLimit(Number(event.target.value))}
          />
        </label>
        <p>
          按模型服务返回的用量统计，到达上限后停止后续调用；单次调用可能超过剩余额度。
        </p>
      </details>
      <label>
        授权公网域名
        <input
          aria-label="授权公网域名"
          value={networkDomains}
          onChange={(event) => setNetworkDomains(event.target.value)}
          placeholder="例如 example.com，多个域名用逗号分隔；留空禁止联网"
        />
      </label>
      <p>
        最多 10
        个完整域名，内网、回环、元数据和未授权站点禁止访问。网页依赖其他域名时需一并填写；此授权会允许任务向对应站点发送内容。
      </p>
      <TaskFiles
        taskId={task.id}
        revision={revision}
        onRevision={setRevision}
      />
      {run &&
        ["COMPLETED", "CANCELLED", "FAILED", "NEEDS_REVIEW"].includes(
          run.status,
        ) && (
          <label>
            <input
              type="checkbox"
              checked={restore}
              onChange={(event) => setRestore(event.target.checked)}
            />
            从当前历史成果创建恢复副本
            <small>
              新建运行并重新制定计划，保留历史同路径文件。不会自动重放原运行；需已安全回收旧执行环境。上传的新输入只补充缺失路径。
            </small>
          </label>
        )}
      </>}
      {task.workflow_task && run && <p>模型调用上限 {run.step_limit ?? "未提供"} · Token预算 {run.token_limit?.toLocaleString("zh-CN") ?? "未提供"} ·
        代码网络 {run.network_domains?.length ? run.network_domains.join("、") : "禁止联网"}。以运行账本统计实际消耗。</p>}
      <div className="work-execution-grid">
        <aside>
          <h3>运行记录</h3>
          {!runs.length && <p>还没有运行记录。</p>}
          {runs.map((item) => (
            <button
              type="button"
              key={item.id}
              aria-pressed={selected === item.id}
              onClick={() => {
                setSelected(item.id);
                setRestore(false);
                setPreview(null);
              }}
            >
              {labels[item.status] || item.status} ·{" "}
              {new Date(item.created_at).toLocaleString("zh-CN")}
            </button>
          ))}
          {runCursor && <button type="button" disabled={historyLoading} onClick={() => void moreRuns()}>加载更早任务运行</button>}
        </aside>
        <main>
          <h3>执行进度</h3>
          {run && (
            <>
              <p role="status">{labels[run.status] || run.status}</p>
              <WorkTokenUsage run={run.id} active={["PENDING", "RUNNING"].includes(run.status)} />
              {run.environment === "browser" && <BrowserWorkbench key={run.id} run={run.id} status={run.status} />}
              <RunDiagnostics id={run.id} mode="work" active={["PENDING", "RUNNING"].includes(run.status)} />
              {run.model_calls !== undefined && (
                <p>
                  预算计数：{run.model_calls} 次模型尝试
                </p>
              )}
              {run.error_code && (
                <p>
                  {workflowErrors[run.error_code] || (run.error_code === "WORK_CAPACITY_WAIT"
                    ? "正在等待可用执行资源，后台会自动继续。"
                    : run.error_code === "WORK_USAGE_UNAVAILABLE"
                      ? "模型服务未返回用量统计，任务已停止。"
                      : run.error_code === "WORK_BUDGET_EXCEEDED"
                        ? "任务预算已耗尽，已停止后续调用。"
                        : "任务已中断，请核对已保存的进度和成果。")}
                </p>
              )}
              {["PENDING", "RUNNING", "WAITING_APPROVAL", "PAUSED"].includes(
                run.status,
              ) && (
                <button
                  disabled={busy}
                  type="button"
                  onClick={() =>
                    void action(() =>
                      api(`/work-runs/${selected}/cancel`, { method: "POST" }),
                    )
                  }
                >
                  取消任务
                </button>
              )}
              {["PENDING", "RUNNING", "WAITING_APPROVAL"].includes(
                run.status,
              ) && (
                <button
                  type="button"
                  disabled={busy || run.pause_requested}
                  onClick={() =>
                    void action(() =>
                      api(`/work-runs/${selected}/pause`, { method: "POST" }),
                    )
                  }
                >
                  {run.pause_requested ? "等待安全暂停" : "请求暂停"}
                </button>
              )}
              {run.status === "PAUSED" && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() =>
                    void action(() =>
                      api(`/work-runs/${selected}/resume`, { method: "POST" }),
                    )
                  }
                >
                  继续任务
                </button>
              )}
              {run.pause_requested && run.status === "RUNNING" && (
                <p>
                  将在下一次模型调用边界或审批检查点暂停，当前操作可能继续完成。
                </p>
              )}
              {run.status === "NEEDS_REVIEW" && (
                <p>
                  执行被中断或结果不确定，已停止自动重试。请核对已保存成果和运行记录。
                </p>
              )}
            </>
          )}
          <pre className="work-output">
            {run?.result?.text ||
              events
                .filter((e) => e.event === "message.delta")
                .map((e) => e.data.text || "")
                .join("") ||
              (task.workflow_task && run ? "已保留流程进度，等待当前节点完成或获批。" : "选择运行后查看进度。")}
          </pre>
          {!task.workflow_task && <ol>
            {events
              .filter((e) => e.event === "step.updated")
              .map((e) => (
                <li key={e.event_id}>已记录执行步骤 · {e.event_id}</li>
              ))}
          </ol>}
          {events
            .filter((event) => event.event === "tool.completed")
            .map((event) => (
              <details key={event.event_id}>
                <summary>工具执行记录 · {event.data.name || "工具"}</summary>
                <pre className="work-output">{event.data.output}</pre>
                {event.data.truncated && <small>输出较长，此处已截断。</small>}
              </details>
            ))}
          {task.workflow_task && <details><summary>流程进度 · {events.filter((event) => event.event === "workflow.node" && event.data.event === "node.completed").length}个节点已完成</summary>
          {events.filter((event) => event.event === "workflow.node").map((event) => <p key={event.event_id}>
            {event.data.node_name || nodeLabels[event.data.kind as keyof typeof nodeLabels] || event.data.node_id} · {{ "node.started": "开始执行", "node.completed": "执行完成",
              "node.failed": "执行失败", "node.handled": "已转入错误分支" }[event.data.event || ""] || event.data.event}
          </p>)}</details>}
          <h4>操作执行凭据</h4>
          {operations.map((operation) => (
            <details key={operation.id}>
              <summary>
                {operation.name === "execute"
                  ? "命令"
                  : operation.name === "delete"
                    ? "删除"
                    : operation.name === "workflow_code" ? "流程代码"
                    : operation.name === "workflow_approval" ? "流程人工确认"
                    : "MCP 写操作"}{" "}
                ·{" "}
                {operation.status === "APPROVED" && run && ["COMPLETED", "FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(run.status)
                  ? "已批准但未执行，运行已停止" : ({
                  APPROVED: "已批准，待执行",
                  STARTED: "已开始",
                  SUCCEEDED: "已确认成功",
                  FAILED: "已确认失败",
                  UNKNOWN: "结果未知，需核查",
                }[operation.status] || operation.status)}
                {operation.exit_code !== null
                  ? ` · 退出码 ${operation.exit_code}`
                  : ""}
              </summary>
              <pre>{JSON.stringify(operation.args, null, 2)}</pre>
              <pre className="work-output">{operation.output}</pre>
              <small>操作编号 {operation.id}</small>
            </details>
          ))}
          <p>
            完成状态表示本次执行已结束；请结合操作退出码、文件与测试输出核对目标。结果未知的操作不会自动重复。
          </p>
          {!task.workflow_task && <><h3>任务计划</h3>
          {(
            events.filter((item) => item.event === "plan.updated").at(-1)?.data
              .todos ||
            run?.result?.todos ||
            []
          ).map((todo, index) => (
            <p key={index}>
              {todo.status === "completed"
                ? "已完成"
                : todo.status === "in_progress"
                  ? "进行中"
                  : "待完成"}
              ：{todo.content}
            </p>
          ))}
          {!events.some((item) => item.event === "plan.updated") &&
            !run?.result?.todos?.length && <p>智能体尚未记录计划。</p>}</>}
          {events.filter((item) => item.event === "plan.updated").length >
            0 && (
            <details>
              <summary>查看计划修订记录</summary>
              {events
                .filter((item) => item.event === "plan.updated")
                .map((item) => (
                  <div key={item.event_id}>
                    <strong>修订记录 {item.event_id}</strong>
                    {item.data.todos?.map((todo, index) => (
                      <p key={index}>
                        {todo.status === "completed"
                          ? "已完成"
                          : todo.status === "in_progress"
                            ? "进行中"
                            : "待完成"}
                        ：{todo.content}
                      </p>
                    ))}
                  </div>
                ))}
            </details>
          )}
        </main>
        <aside>
          <h3>待审批操作</h3>
          {approvals
            .filter((a) => !a.decision)
            .map((approval) => (
              <article key={approval.id}>
                {approval.payload.action_requests.map((request, index) => (
                  <div key={index}>
                    <strong>
                      {request.name === "execute"
                        ? "运行命令"
                        : request.name === "delete"
                          ? "删除工作区文件"
                          : request.name === "browser_task"
                            ? "浏览器操作列表"
                            : request.name === "workflow_code" ? "执行固定流程代码"
                            : request.name === "workflow_approval" ? "流程人工确认"
                            : "外部写操作"}
                    </strong>
                    {request.description && <p>{request.description}</p>}
                    {["workflow_code", "workflow_approval"].includes(request.name)
                      ? <WorkflowApproval approval={approval.id} name={request.name} args={request.args} />
                      : <pre>{JSON.stringify(request.args, null, 2)}</pre>}
                  </div>
                ))}
                <p>
                  有效至 {new Date(approval.expires_at).toLocaleString("zh-CN")}
                </p>
                {approval.payload.action_requests.every((item) =>
                  ["execute", "delete"].includes(item.name),
                ) && (
                  <details>
                    <summary>修改参数后批准</summary>
                    <p>
                      保持操作类型不变，只修改 command 或
                      file_path。提交后按下方参数执行一次。
                    </p>
                    <textarea
                      aria-label="修改审批参数"
                      value={
                        edits[approval.id] ??
                        JSON.stringify(
                          approval.payload.action_requests.map(
                            ({ name, args }) => ({ name, args }),
                          ),
                          null,
                          2,
                        )
                      }
                      onChange={(event) =>
                        setEdits((current) => ({
                          ...current,
                          [approval.id]: event.target.value,
                        }))
                      }
                    />
                    <button
                      type="button"
                      disabled={
                        busy ||
                        run?.status !== "WAITING_APPROVAL" ||
                        new Date(approval.expires_at).getTime() <= approvalCheckedAt
                      }
                      onClick={() =>
                        void action(async () => {
                          const edited_actions = JSON.parse(
                            edits[approval.id] ??
                              JSON.stringify(
                                approval.payload.action_requests.map(
                                  ({ name, args }) => ({ name, args }),
                                ),
                              ),
                          );
                          await api(
                            `/work-approvals/${approval.id}/decisions`,
                            {
                              method: "POST",
                              body: JSON.stringify({
                                digest: approval.digest,
                                decision: "edit",
                                edited_actions,
                              }),
                            },
                          );
                        })
                      }
                    >
                      批准修改后的操作
                    </button>
                  </details>
                )}
                {(["approve", "reject"] as const).map((decision) => (
                  <button
                    type="button"
                    key={decision}
                    disabled={
                      busy ||
                      run?.status !== "WAITING_APPROVAL" ||
                      new Date(approval.expires_at).getTime() <= Date.now()
                    }
                    onClick={() =>
                      void action(() =>
                        api(`/work-approvals/${approval.id}/decisions`, {
                          method: "POST",
                          body: JSON.stringify({
                            digest: approval.digest,
                            decision,
                          }),
                        }),
                      )
                    }
                  >
                    {decision === "approve" ? "批准此操作" : "拒绝此操作"}
                  </button>
                ))}
              </article>
            ))}
          {recovery?.sessions.some((session) => session.data_lost) && (
            <p role="alert">
              保留的执行环境已过期，未成功导出的文件无法恢复。已保存成果仍可下载。
            </p>
          )}
          {recovery?.sessions.some(
            (session) =>
              session.retention_required && session.state !== "RELEASED",
          ) && (
            <div>
              <h3>工作区救援</h3>
              <p>
                停止残余进程后只读导出工作区，不继续原任务。最多 100
                个文件、单文件 1 MB、合计 20 MB；超限时保留环境。
              </p>
              <button
                type="button"
                disabled={
                  busy ||
                  ["PENDING", "RUNNING"].includes(recovery.job?.status || "")
                }
                onClick={() => {
                  if (
                    window.confirm(
                      "停止保留环境中的残余进程并救援导出文件？不会重放原任务。",
                    )
                  )
                    void action(() =>
                      api(`/work-runs/${selected}/recovery`, {
                        method: "POST",
                      }),
                    );
                }}
              >
                救援导出工作区
              </button>
            </div>
          )}
          {recovery?.job && (
            <p>
              救援状态：
              {{
                PENDING: "等待安全窗口",
                RUNNING: "正在停止残余进程并导出",
                COMPLETED: "成果已保存",
                FAILED: "未完成，请核对环境和文件限制后重试",
              }[recovery.job.status] || recovery.job.status}
            </p>
          )}
          {run && repositoryPermissions.read && <RepositoryReview key={run.id} run={run.id} status={run.status}
            canPublish={!!repositoryPermissions.publish} />}
          <h3>已保存成果</h3>
          {!artifacts.length && <p>暂未生成可下载成果。</p>}
          {artifacts.map((file) => (
            <p key={file.id}>
              <a
                href={`/api/v1/work-artifacts/${file.id}/download`}
                download={file.path.split("/").pop()}
              >
                {file.path}
              </a>{" "}
              · {file.size} 字节
              <button
                type="button"
                onClick={() => setPreview(file)}
              >
                预览或对照
              </button>
            </p>
          ))}
        </aside>
      </div>
      {preview && (
        <ArtifactPreview
          file={preview}
          original={run?.input_files?.find(
            (item) => item.path === preview.path,
          )}
          close={() => setPreview(null)}
        />
      )}
    </section>
  );
}
