"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  ArrowLeft,
  Download,
  FlaskConical,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";
import { api, ApiError } from "@/lib/api/platform";
import {
  evaluationActive,
  evaluationLabel,
  type EvaluationCapabilities,
  type EvaluationBudget,
  type EvaluationComparison,
  type EvaluationDataset,
  type EvaluationRun,
  type EvaluationSample,
  type FailureCandidate,
  type Page,
} from "./agent-evaluation-types";
import "./agent-evaluation-workspace.css";

const base = "/agent-evaluations";
type View = "datasets" | "runs" | "comparisons" | "failure-candidates";
const views: [View, string][] = [
  ["datasets", "评测集"],
  ["runs", "评测批次"],
  ["comparisons", "版本对比"],
  ["failure-candidates", "改进候选"],
];
const date = (value: string) => new Date(value).toLocaleString("zh-CN");

function Status({ value }: { value: string | null | undefined }) {
  return (
    <span
      className="agent-eval-status"
      data-status={value || "UNKNOWN"}
    >
      {evaluationLabel(value)}
    </span>
  );
}

export function AgentEvaluationWorkspace() {
  const [view, setView] = useState<View>("datasets");
  const [capability, setCapability] = useState<EvaluationCapabilities | null>(
    null,
  );
  const [datasets, setDatasets] = useState<EvaluationDataset[]>([]);
  const [runs, setRuns] = useState<EvaluationRun[]>([]);
  const [comparisons, setComparisons] = useState<EvaluationComparison[]>([]);
  const [candidates, setCandidates] = useState<FailureCandidate[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [modal, setModal] = useState<View | null>(null);
  const [selection, setSelection] = useState<{ view: View; id: string } | null>(
    null,
  );
  const [revision, setRevision] = useState(0);

  const reload = useCallback(
    (signal: AbortSignal) =>
      Promise.all([
        api<EvaluationCapabilities>(`${base}/capabilities`, { signal }),
        api<
          Page<
            | EvaluationDataset
            | EvaluationRun
            | EvaluationComparison
            | FailureCandidate
          >
        >(
          `${base}/${view}?limit=20${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
          { signal },
        ),
      ]),
    [cursor, view],
  );
  const receive = useCallback(
    ([cap, page]: [
      EvaluationCapabilities,
      Page<
        | EvaluationDataset
        | EvaluationRun
        | EvaluationComparison
        | FailureCandidate
      >,
    ]) => {
      setCapability(cap);
      if (view === "datasets") setDatasets(page.items as EvaluationDataset[]);
      if (view === "runs") setRuns(page.items as EvaluationRun[]);
      if (view === "comparisons")
        setComparisons(page.items as EvaluationComparison[]);
      if (view === "failure-candidates")
        setCandidates(page.items as FailureCandidate[]);
      setNext(page.next_cursor);
      setError("");
    },
    [view],
  );

  useEffect(() => {
    const controller = new AbortController();
    reload(controller.signal)
      .then((value) => {
        if (!controller.signal.aborted) receive(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [reload, receive, revision]);

  useEffect(() => {
    if (view !== "runs" || !runs.some((run) => evaluationActive(run.status)))
      return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      reload(controller.signal)
        .then((value) => {
          if (!controller.signal.aborted) receive(value);
        })
        .catch((reason: Error) => {
          if (!controller.signal.aborted) setError(reason.message);
        });
    }, 5000);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [reload, receive, runs, view]);

  const canCreate =
    capability &&
    (view === "runs"
      ? capability.permissions.run && capability.admission.status === "READY"
      : capability.permissions.manage);
  const items =
    view === "datasets"
      ? datasets
      : view === "runs"
        ? runs
        : view === "comparisons"
          ? comparisons
          : candidates;
  const refresh = () => {
    setLoading(true);
    setRevision((value) => value + 1);
  };
  const changePage = (value: string | null) => {
    setLoading(true);
    setCursor(value);
  };
  return (
    <section
      className="agent-eval-workspace"
      aria-label="Agent评测"
    >
      <header className="resource-list-heading">
        <div>
          <h2>
            <FlaskConical size={20} /> Agent 评测
          </h2>
          <p>用任务事实验证智能体，分别查看执行结果、评分和资源回收。</p>
        </div>
        <button
          type="button"
          onClick={refresh}
          disabled={loading}
          aria-label="刷新评测数据"
        >
          <RefreshCw size={15} /> 刷新
        </button>
      </header>
      {capability?.admission.status === "BLOCKED" && (
        <aside
          className="agent-eval-admission"
          role="status"
        >
          <strong>执行准入尚未开放</strong>
          <ul>
            {capability.admission.blockers.map((item) => (
              <li key={item.code}>{item.message}</li>
            ))}
          </ul>
          <small>
            可以管理评测集；未执行的任务不会计为通过。受控工程测试不代表真实模型效果。
          </small>
        </aside>
      )}
      <nav
        className="agent-eval-tabs"
        aria-label="评测视图"
      >
        {views.map(([key, label]) => (
          <button
            key={key}
            type="button"
            aria-pressed={view === key}
            onClick={() => {
              if (view === key) return;
              setLoading(true);
              setView(key);
              setCursor(null);
              setSelection(null);
              setNotice("");
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p
          className="platform-error"
          role="alert"
        >
          {error}{" "}
          <button
            type="button"
            onClick={refresh}
          >
            重试加载
          </button>
        </p>
      )}
      {notice && <p role="status">{notice}</p>}
      {selection ? (
        <EvaluationDetail
          key={`${selection.view}:${selection.id}`}
          selection={selection}
          capability={capability}
          onBack={() => {
            setSelection(null);
            refresh();
          }}
        />
      ) : (
        <>
          <div className="agent-eval-toolbar">
            <span>{loading ? "正在加载…" : "每页最多20条"}</span>
            {canCreate && (
              <button
                className="settings-primary"
                type="button"
                onClick={() => setModal(view)}
              >
                <Plus size={15} />
                {view === "datasets"
                  ? "新增评测集"
                  : view === "runs"
                    ? "创建批次"
                    : view === "comparisons"
                      ? "新增对比"
                      : "新增改进候选"}
              </button>
            )}
          </div>
          <div
            className="agent-eval-list"
            aria-busy={loading}
          >
            {!loading && !error && items.length === 0 && (
              <div className="agent-eval-empty">
                <FlaskConical size={30} />
                <h3>暂无{views.find(([key]) => key === view)?.[1]}</h3>
                <p>
                  先导入有明确验收条件的评测集，再发布版本。执行前需通过环境和预算检查。
                </p>
              </div>
            )}
            {items.map((item) => (
              <button
                className="agent-eval-row"
                type="button"
                key={item.id}
                onClick={() => setSelection({ view, id: item.id })}
              >
                {view === "datasets" ? (
                  <>
                    <strong>{(item as EvaluationDataset).name}</strong>
                    <span>
                      {(item as EvaluationDataset).case_count}条任务定义 ·
                      草稿修订{(item as EvaluationDataset).revision} ·{" "}
                      {(item as EvaluationDataset).current_version
                        ? `发布 v${(item as EvaluationDataset).current_version}`
                        : "尚未发布"}
                    </span>
                    <small>
                      {date((item as EvaluationDataset).updated_at)}
                    </small>
                  </>
                ) : view === "runs" ? (
                  <>
                    <strong>评测批次 · {item.id.slice(0, 8)}</strong>
                    <span>
                      <Status value={(item as EvaluationRun).status} /> 判定：
                      <Status value={(item as EvaluationRun).gate_verdict} />
                    </span>
                    <small>
                      {(item as EvaluationRun).completed_samples}/
                      {(item as EvaluationRun).total_samples}次执行 ·{" "}
                      {date((item as EvaluationRun).created_at)}
                    </small>
                  </>
                ) : view === "comparisons" ? (
                  <>
                    <strong>对比 · {item.id.slice(0, 8)}</strong>
                    <Status value={(item as EvaluationComparison).status} />
                    <small>
                      {date((item as EvaluationComparison).created_at)}
                    </small>
                  </>
                ) : (
                  <>
                    <strong>{(item as FailureCandidate).name}</strong>
                    <Status value={(item as FailureCandidate).status} />
                    <small>{date((item as FailureCandidate).created_at)}</small>
                  </>
                )}
              </button>
            ))}
          </div>
          <div className="agent-eval-toolbar">
            <button
              type="button"
              disabled={!cursor || loading}
              onClick={() => changePage(null)}
            >
              回到首页
            </button>
            <button
              type="button"
              disabled={!next || loading}
              onClick={() => changePage(next)}
            >
              下一页
            </button>
          </div>
        </>
      )}
      {modal && capability && (
        <CreateEvaluation
          view={modal}
          capability={capability}
          onClose={() => setModal(null)}
          onCreated={(id) => {
            setModal(null);
            setSelection({ view: modal, id });
            setNotice("已保存；任务完成与评测通过分别判定。");
            refresh();
          }}
        />
      )}
    </section>
  );
}

function EvaluationDetail({
  selection,
  capability,
  onBack,
}: {
  selection: { view: View; id: string };
  capability: EvaluationCapabilities | null;
  onBack: () => void;
}) {
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [samples, setSamples] = useState<EvaluationSample[]>([]);
  const [nextSample, setNextSample] = useState<string | null>(null);
  const [sample, setSample] = useState<Record<string, unknown> | null>(null);
  const [editing, setEditing] = useState(false);
  const [reviewReason, setReviewReason] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [revision, setRevision] = useState(0);
  const path = `${base}/${selection.view}/${selection.id}`;
  const sampleWindow = useRef(100);
  const requestSequence = useRef(0);
  useEffect(() => {
    const controller = new AbortController();
    const sequence = ++requestSequence.current;
    Promise.all([
      api<Record<string, unknown>>(path, { signal: controller.signal }),
      selection.view === "runs"
        ? api<Page<EvaluationSample>>(`${path}/samples?limit=100`, {
            signal: controller.signal,
          })
        : Promise.resolve(null),
    ])
      .then(async ([value, page]) => {
        let items = page?.items || [];
        let cursor = page?.next_cursor || null;
        const seen = new Set<string>();
        while (
          cursor &&
          items.length < sampleWindow.current &&
          !controller.signal.aborted
        ) {
          if (seen.has(cursor)) throw new Error("样本分页未前进，请重新读取");
          seen.add(cursor);
          const more = await api<Page<EvaluationSample>>(
            `${path}/samples?limit=100&cursor=${encodeURIComponent(cursor)}`,
            { signal: controller.signal },
          );
          if (!more.items.length) throw new Error("样本分页不完整，请重新读取");
          items = [...items, ...more.items];
          cursor = more.next_cursor;
        }
        if (
          !controller.signal.aborted &&
          sequence === requestSequence.current
        ) {
          setDetail(value);
          setSamples(items);
          setNextSample(cursor);
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted && sequence === requestSequence.current)
          setError(reason.message);
      });
    return () => controller.abort();
  }, [path, revision, selection.view]);
  useEffect(() => {
    if (
      busy ||
      selection.view !== "runs" ||
      !detail ||
      !evaluationActive(String(detail.status))
    )
      return;
    const timer = setTimeout(() => setRevision((value) => value + 1), 5000);
    return () => clearTimeout(timer);
  }, [busy, detail, selection.view]);

  async function moreSamples() {
    if (!nextSample) return;
    const sequence = ++requestSequence.current;
    setBusy(true);
    setError("");
    try {
      const page = await api<Page<EvaluationSample>>(
        `${path}/samples?limit=100&cursor=${encodeURIComponent(nextSample)}`,
      );
      if (sequence !== requestSequence.current) return;
      const rows = [
        ...new Map(
          [...samples, ...page.items].map((row) => [row.id, row]),
        ).values(),
      ];
      sampleWindow.current = rows.length;
      setSamples(rows);
      setNextSample(page.next_cursor);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function mutate(suffix: string, body: object) {
    setBusy(true);
    setError("");
    try {
      await api(`${path}/${suffix}`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setRevision((value) => value + 1);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function download(format: string) {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/v1${path}/report?format=${format}`, {
        cache: "no-store",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(body?.error?.message || "报告尚未就绪或无权下载");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `evaluation-${selection.id}.${format === "junit" ? "xml" : format === "markdown" ? "md" : "json"}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="agent-eval-detail">
      <button
        type="button"
        onClick={onBack}
      >
        <ArrowLeft size={15} /> 返回列表
      </button>
      {error && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}{" "}
          <button
            type="button"
            onClick={() => setRevision((value) => value + 1)}
          >
            重新读取
          </button>
        </p>
      )}
      {!detail && !error && <p role="status">正在读取详情…</p>}
      {detail && (
        <>
          <h3>{String(detail.name || `批次 ${selection.id.slice(0, 8)}`)}</h3>
          {selection.view === "comparisons" && (
            <>
              <Status value={String(detail.status)} />
              <p>只有任务、规则、重复次数与配置条件一致时，才能据此比较。</p>
              <ul>
                {Array.isArray(detail.reasons) &&
                  detail.reasons.map((reason, index) => (
                    <li key={index}>{String(reason)}</li>
                  ))}
              </ul>
            </>
          )}
          {selection.view === "failure-candidates" && (
            <>
              <Status value={String(detail.status)} />
              <p>候选仅用于人工改进，不自动加入评测集或执行。</p>
              {capability?.permissions.manage &&
                detail.status === "PENDING_REVIEW" && (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      void mutate("review", {
                        revision: detail.revision,
                        decision: "APPROVED",
                        reason: reviewReason,
                      });
                    }}
                  >
                    <label>
                      审核理由
                      <textarea
                        required
                        maxLength={2000}
                        value={reviewReason}
                        onChange={(event) =>
                          setReviewReason(event.target.value)
                        }
                      />
                    </label>
                    <div className="agent-eval-toolbar">
                      <button
                        type="button"
                        disabled={busy || !reviewReason.trim()}
                        onClick={() =>
                          void mutate("review", {
                            revision: detail.revision,
                            decision: "REJECTED",
                            reason: reviewReason,
                          })
                        }
                      >
                        拒绝候选
                      </button>
                      <button
                        type="submit"
                        disabled={busy}
                      >
                        批准为改进候选
                      </button>
                    </div>
                  </form>
                )}
            </>
          )}
          {selection.view === "datasets" && (
            <div className="agent-eval-toolbar">
              <span>
                草稿修订{String(detail.revision)} · {String(detail.case_count)}
                条任务定义；业务目标是否独立仍需语义审核
              </span>
              {capability?.permissions.manage && (
                <>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => setEditing(true)}
                  >
                    编辑草稿
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() =>
                      void mutate("versions", { revision: detail.revision })
                    }
                  >
                    发布当前草稿版本
                  </button>
                </>
              )}
            </div>
          )}
          {selection.view === "runs" && (
            <>
              <div className="agent-eval-toolbar">
                <span>
                  执行：
                  <Status value={String(detail.status)} /> 判定：
                  <Status value={String(detail.gate_verdict)} />
                </span>
                {capability?.permissions.run &&
                  evaluationActive(String(detail.status)) &&
                  detail.status !== "CANCEL_REQUESTED" && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void mutate("cancel", { revision: detail.revision })
                      }
                    >
                      取消剩余执行
                    </button>
                  )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setRevision((value) => value + 1)}
                >
                  刷新进度
                </button>
              </div>
              <div className="agent-eval-toolbar">
                {["json", "junit", "markdown"].map((format) => (
                  <button
                    key={format}
                    type="button"
                    disabled={busy}
                    onClick={() => void download(format)}
                  >
                    <Download size={14} />{" "}
                    {format === "markdown" ? "中文报告" : format.toUpperCase()}
                  </button>
                ))}
              </div>
              <h4>样本结果</h4>
              <p>
                未留证、未评分或效果未知均不等于通过。已加载{samples.length}
                条样本{nextSample ? "，可继续加载。" : "。"}
              </p>
              {samples.map((row) => (
                <button
                  type="button"
                  className="agent-eval-row"
                  key={row.id}
                  disabled={busy}
                  onClick={async () => {
                    setBusy(true);
                    setError("");
                    try {
                      setSample(
                        await api<Record<string, unknown>>(
                          `${path}/samples/${row.id}`,
                        ),
                      );
                    } catch (reason) {
                      setError((reason as Error).message);
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  <strong>
                    {row.case_id} · 第{row.repeat_index}次
                  </strong>
                  <span>
                    任务：
                    <Status value={row.task_outcome} /> 评分：
                    <Status value={row.metric_status} /> 判定：
                    <Status value={row.gate_verdict} />
                  </span>
                  <small>回收：{evaluationLabel(row.cleanup_status)}</small>
                </button>
              ))}
              {nextSample && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void moreSamples()}
                >
                  加载更多样本
                </button>
              )}
              {!samples.length && <p>尚无已记录样本。</p>}
              {sample && (
                <>
                  <details open>
                    <summary>样本事实与证据</summary>
                    <pre>{JSON.stringify(sample, null, 2)}</pre>
                  </details>
                  {capability?.permissions.manage && (
                    <SampleReview
                      key={String(sample.id)}
                      path={`${path}/samples/${String(sample.id)}/reviews`}
                      sample={sample}
                    />
                  )}
                </>
              )}
            </>
          )}
          <details>
            <summary>
              {selection.view === "datasets"
                ? "定义、摘要与不可变版本"
                : "完整记录与限制"}
            </summary>
            <pre>{JSON.stringify(detail, null, 2)}</pre>
          </details>
        </>
      )}
      {editing && detail && capability && (
        <CreateEvaluation
          view="datasets"
          dataset={detail as unknown as EvaluationDataset}
          capability={capability}
          onClose={() => setEditing(false)}
          onCreated={() => {
            setEditing(false);
            setRevision((value) => value + 1);
          }}
        />
      )}
    </section>
  );
}

function SampleReview({
  path,
  sample,
}: {
  path: string;
  sample: Record<string, unknown>;
}) {
  const [decision, setDecision] = useState("NEEDS_MORE_EVIDENCE");
  const [reason, setReason] = useState("");
  const [rules, setRules] = useState(
    typeof sample.rules_version === "string" ? sample.rules_version : "",
  );
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await api(path, {
        method: "POST",
        body: JSON.stringify({
          revision: sample.revision,
          decision,
          reason,
          rules_version: rules,
        }),
      });
      setSaved(true);
    } catch (value) {
      setError((value as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form
      className="agent-eval-review"
      onSubmit={(event) => void submit(event)}
    >
      <h4>人工复核</h4>
      <p>复核追加审核记录，不修改原始事实、评分或门禁结果。</p>
      <fieldset disabled={busy || saved || typeof sample.revision !== "number"}>
        <label>
          复核决定
          <select
            value={decision}
            onChange={(event) => setDecision(event.target.value)}
          >
            <option value="NEEDS_MORE_EVIDENCE">需要补充证据</option>
            <option value="CONFIRMED">确认原记录</option>
            <option value="DISPUTED">对原记录有异议</option>
          </select>
        </label>
        <label>
          规则版本
          <input
            required
            maxLength={120}
            value={rules}
            onChange={(event) => setRules(event.target.value)}
          />
        </label>
        <label>
          复核理由
          <textarea
            required
            maxLength={2000}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>
        <button type="submit">
          {busy ? "正在保存…" : saved ? "已追加复核" : "保存复核记录"}
        </button>
      </fieldset>
      {error && <p role="alert">{error}</p>}
      {typeof sample.revision !== "number" && (
        <p>缺少样本修订号，暂不能提交复核。</p>
      )}
    </form>
  );
}

function CreateEvaluation({
  view,
  dataset,
  capability,
  onClose,
  onCreated,
}: {
  view: View;
  dataset?: EvaluationDataset;
  capability: EvaluationCapabilities;
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [name, setName] = useState(dataset?.name || "");
  const [description, setDescription] = useState(dataset?.description || "");
  const [definition, setDefinition] = useState(
    dataset?.definition ? JSON.stringify(dataset.definition, null, 2) : "",
  );
  const [datasetId, setDatasetId] = useState("");
  const [version, setVersion] = useState(1);
  const repeats = 1;
  const [baseline, setBaseline] = useState("");
  const [candidate, setCandidate] = useState("");
  const [input, setInput] = useState("");
  const [expected, setExpected] = useState("");
  const [authorization, setAuthorization] = useState("");
  const [redacted, setRedacted] = useState(false);
  const [preview, setPreview] = useState<unknown>(null);
  const [requestId] = useState(() => crypto.randomUUID());
  const [submitted, setSubmitted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [options, setOptions] = useState<EvaluationDataset[]>([]);
  const [budgets, setBudgets] = useState<EvaluationBudget>(
    capability.default_budgets || {
      max_seconds: 300,
      max_model_calls: 20,
      max_tokens: 20000,
      max_sandboxes: 2,
    },
  );
  const [versionInfo, setVersionInfo] = useState<{
    key: string;
    case_count: number;
  } | null>(null);
  useEffect(() => {
    if (view !== "runs") return;
    const controller = new AbortController();
    api<Page<EvaluationDataset>>(`${base}/datasets?limit=100`, {
      signal: controller.signal,
    })
      .then((page) => {
        if (!controller.signal.aborted) setOptions(page.items);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [view]);
  useEffect(() => {
    if (
      view !== "runs" ||
      !datasetId ||
      !Number.isSafeInteger(version) ||
      version < 1
    )
      return;
    const controller = new AbortController();
    const key = `${datasetId}:${version}`;
    api<{ case_count: number }>(
      `${base}/datasets/${datasetId}/versions/${version}`,
      { signal: controller.signal },
    )
      .then((value) => {
        if (
          !Number.isSafeInteger(value.case_count) ||
          value.case_count < 1 ||
          value.case_count > 100
        )
          throw new Error("发布版本的任务数量无效");
        if (!controller.signal.aborted) {
          setVersionInfo({ key, case_count: value.case_count });
          setError("");
        }
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [view, datasetId, version]);
  const dirty = Boolean(
    name ||
    description ||
    definition ||
    input ||
    expected ||
    authorization ||
    redacted ||
    datasetId ||
    baseline ||
    candidate ||
    version !== 1 ||
    JSON.stringify(budgets) !==
      JSON.stringify(
        capability.default_budgets || {
          max_seconds: 300,
          max_model_calls: 20,
          max_tokens: 20000,
          max_sandboxes: 2,
        },
      ),
  );
  const close = () => {
    if (
      !busy &&
      (!dirty ||
        window.confirm("放弃尚未完成的评测配置？已提交的批次仍保留在列表中。"))
    )
      onClose();
  };
  function payload(): object {
    if (view === "datasets")
      return {
        name,
        description,
        revision: dataset?.revision || 0,
        definition: JSON.parse(definition),
      };
    if (view === "runs")
      return {
        dataset_id: datasetId,
        version,
        repeats,
        request_id: requestId,
        execution_mode: "engineering",
        acceptance_target: "engineering",
        budgets,
      };
    if (view === "comparisons")
      return { baseline_run_id: baseline, candidate_run_id: candidate };
    return {
      name,
      material: { input, expected_behavior: expected },
      authorization_note: authorization,
      redaction_confirmed: redacted,
    };
  }
  async function submit(event: FormEvent, onlyPreview = false) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const body = JSON.stringify(payload());
      if (new TextEncoder().encode(body).length > 131072)
        throw new Error("评测定义超过128 KiB，请拆分为更小的评测集。");
      if (!onlyPreview && view === "runs") setSubmitted(true);
      const suffix = onlyPreview ? "/preview" : dataset ? `/${dataset.id}` : "";
      const value = await api<{ id: string }>(`${base}/${view}${suffix}`, {
        method: dataset && !onlyPreview ? "PUT" : "POST",
        body,
      });
      if (onlyPreview) setPreview(value);
      else onCreated(value.id);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 422)
        setSubmitted(false);
      setError(
        reason instanceof SyntaxError
          ? "JSON格式不正确，请检查定义。"
          : (reason as Error).message,
      );
    } finally {
      setBusy(false);
    }
  }
  const selectedVersion =
    versionInfo?.key === `${datasetId}:${version}` ? versionInfo : null;
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="agent-eval-overlay" />
        <Dialog.Content
          className="agent-eval-dialog"
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault();
          }}
        >
          <header>
            <div>
              <Dialog.Title>
                {view === "datasets"
                  ? dataset
                    ? "编辑评测集草稿"
                    : "新增评测集"
                  : view === "runs"
                    ? "创建评测批次"
                    : view === "comparisons"
                      ? "新增版本对比"
                      : "新增改进候选"}
              </Dialog.Title>
              <Dialog.Description>
                仅管理当前账号的数据。执行会留下独立版本、用量和验收证据。
              </Dialog.Description>
            </div>
            <button
              aria-label="关闭评测配置"
              type="button"
              disabled={busy}
              onClick={close}
            >
              <X size={18} />
            </button>
          </header>
          <form onSubmit={(event) => void submit(event)}>
            <fieldset disabled={busy || submitted}>
              {(view === "datasets" || view === "failure-candidates") && (
                <label>
                  名称
                  <input
                    required
                    maxLength={120}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
              )}
              {view === "datasets" && (
                <>
                  <label>
                    说明
                    <textarea
                      maxLength={2000}
                      value={description}
                      onChange={(event) => setDescription(event.target.value)}
                    />
                  </label>
                  <label>
                    导入声明式JSON
                    <input
                      type="file"
                      accept=".json,application/json"
                      onChange={async (event) => {
                        const file = event.target.files?.[0];
                        if (!file) return;
                        if (file.size > 131072) {
                          setError("文件最多128 KiB");
                          return;
                        }
                        try {
                          setDefinition(await file.text());
                          setPreview(null);
                        } catch {
                          setError("文件读取失败");
                        }
                      }}
                    />
                  </label>
                  <label>
                    评测定义
                    <textarea
                      required
                      rows={10}
                      value={definition}
                      onChange={(event) => {
                        setDefinition(event.target.value);
                        setPreview(null);
                      }}
                      placeholder="导入现有评测集JSON。仅接受注册的验证器和指标，不执行自定义脚本。"
                    />
                  </label>
                </>
              )}
              {view === "runs" && (
                <>
                  <label>
                    已发布评测集
                    <select
                      required
                      value={datasetId}
                      onChange={(event) => {
                        setDatasetId(event.target.value);
                        setVersion(
                          options.find((item) => item.id === event.target.value)
                            ?.current_version || 1,
                        );
                      }}
                    >
                      <option value="">请选择</option>
                      {options
                        .filter((item) => item.current_version)
                        .map((item) => (
                          <option
                            value={item.id}
                            key={item.id}
                          >
                            {item.name} · 最新发布 v{item.current_version}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    发布版本
                    <input
                      type="number"
                      required
                      min={1}
                      step={1}
                      value={version}
                      onChange={(event) =>
                        setVersion(event.target.valueAsNumber)
                      }
                    />
                  </label>
                  <p>
                    {selectedVersion
                      ? `${selectedVersion.case_count}条任务定义 × ${repeats}次，最多${selectedVersion.case_count * repeats}次执行。`
                      : "请选择有效发布版本，等待读取该版本的任务数量。"}{" "}
                    定义数量不等于已确认的独立业务目标。本入口使用单次受控工程模式，不代表真实模型质量评测。预算将由服务器按不可变定义检查。
                  </p>
                  <h3>本批次硬预算</h3>
                  {(
                    [
                      ["max_seconds", "总时限（秒）"],
                      ["max_model_calls", "最多模型调用次数"],
                      ["max_tokens", "最多Token"],
                      ["max_sandboxes", "最多沙箱创建次数"],
                    ] as const
                  ).map(([key, label]) => (
                    <label key={key}>
                      {label}
                      <input
                        required
                        type="number"
                        min={key === "max_seconds" ? 1 : 0}
                        step={1}
                        value={Number.isNaN(budgets[key]) ? "" : budgets[key]}
                        onChange={(event) =>
                          setBudgets((previous) => ({
                            ...previous,
                            [key]: event.target.valueAsNumber,
                          }))
                        }
                      />
                    </label>
                  ))}
                  <p>
                    这是执行上限，实际用量在报告中单列。预算不足时拒绝提交，不会自动扩大上限。
                  </p>
                </>
              )}
              {view === "comparisons" && (
                <>
                  <label>
                    基线批次ID
                    <input
                      required
                      value={baseline}
                      onChange={(event) => setBaseline(event.target.value)}
                    />
                  </label>
                  <label>
                    候选批次ID
                    <input
                      required
                      value={candidate}
                      onChange={(event) => setCandidate(event.target.value)}
                    />
                  </label>
                  <p>
                    服务器核对任务、规则、重复次数与实际配置。条件不一致时不输出可信提升。
                  </p>
                </>
              )}
              {view === "failure-candidates" && (
                <>
                  <label>
                    合成或已获授权的输入
                    <textarea
                      required
                      value={input}
                      onChange={(event) => {
                        setInput(event.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label>
                    期望行为
                    <textarea
                      required
                      value={expected}
                      onChange={(event) => {
                        setExpected(event.target.value);
                        setPreview(null);
                      }}
                    />
                  </label>
                  <label>
                    授权说明
                    <input
                      required
                      value={authorization}
                      onChange={(event) => setAuthorization(event.target.value)}
                    />
                  </label>
                  <label className="agent-eval-check">
                    <input
                      required
                      type="checkbox"
                      checked={redacted}
                      onChange={(event) => setRedacted(event.target.checked)}
                    />
                    我已确认材料获授权且已移除敏感信息
                  </label>
                  <p>保存为待审核候选，不自动执行或加入已发布评测集。</p>
                </>
              )}
            </fieldset>
            {submitted && error && (
              <p role="status">
                提交结果待确认。请先返回批次列表核对；本窗口保留原请求标识，重试不会创建第二个批次。
              </p>
            )}
            {error && (
              <p
                className="platform-error"
                role="alert"
              >
                {error}
              </p>
            )}
            {preview !== null && (
              <details open>
                <summary>校验预览</summary>
                <pre>{JSON.stringify(preview, null, 2)}</pre>
              </details>
            )}
            <footer>
              <button
                type="button"
                disabled={busy}
                onClick={close}
              >
                取消
              </button>
              {(view === "datasets" || view === "failure-candidates") && (
                <button
                  type="button"
                  disabled={busy}
                  onClick={(event) => void submit(event, true)}
                >
                  校验预览
                </button>
              )}
              <button
                className="settings-primary"
                type="submit"
                disabled={
                  busy ||
                  (view === "runs" &&
                    (capability.admission.status !== "READY" ||
                      !selectedVersion))
                }
              >
                {busy
                  ? "正在处理…"
                  : submitted
                    ? "以原请求标识核对提交"
                    : view === "runs"
                      ? "创建批次"
                      : "保存"}
              </button>
            </footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
