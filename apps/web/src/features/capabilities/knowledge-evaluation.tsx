"use client";

import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api } from "@/lib/api/platform";
import { TokenUsage, type Usage } from "@/features/ask/token-usage";

type Evidence = { document_id: string; revision: number; chunk_ids: string[] };
type Question = {
  id: string;
  question: string;
  category: string;
  unanswerable: boolean;
  expected: Evidence[];
};
type Dataset = {
  id: string;
  name: string;
  dataset_type: string;
  revision: number;
  questions: Question[];
};
type Measures = {
  questions: number;
  recall_at_10: number | null;
  evidence_precision: number | null;
  no_answer_correct: number | null;
  duration_ms: number | null;
};
type Metrics = Measures & {
  total_questions: number;
  complete: boolean;
  categories: Record<string, Measures>;
  limitations: string;
};
type Experiment = {
  id: string;
  dataset_snapshot: { name: string; revision: number; dataset_type: string };
  total_questions: number;
  completed_questions: number;
  status: string;
  error_code: string | null;
  metrics: Metrics | null;
  comparison_key: string;
  created_at: string;
  usage?: Usage;
};
type Result = {
  id: string;
  question: string;
  category: string;
  recall_at_10: number | null;
  no_answer_correct: boolean | null;
  duration_ms: number;
  mode: string;
  warning: string | null;
  items: {
    name: string;
    location: string;
    text: string;
    document_revision: number;
  }[];
};
const categories: Record<string, string> = {
  fact: "事实",
  no_answer: "无答案",
  outdated: "过期",
  conflict: "冲突",
  scanned: "扫描件",
  table: "表格",
};
const statuses: Record<string, string> = {
  PENDING: "排队中",
  RUNNING: "评测中",
  SUCCEEDED: "已完成",
  FAILED: "评测失败",
  UNKNOWN: "结果未知，未自动重试",
  CANCELLED: "已取消",
};
const question = (id: string): Question => ({
  id,
  question: "",
  category: "fact",
  unanswerable: false,
  expected: [],
});
const percent = (value: number | null | undefined) =>
  value == null ? "—" : `${(value * 100).toFixed(1)}%`;

function MetricSummary({ value }: { value: Measures | null }) {
  return (
    <div className="evaluation-metrics">
      <p>
        证据召回率 Recall@10 <strong>{percent(value?.recall_at_10)}</strong>
      </p>
      <p>
        检索证据精度 <strong>{percent(value?.evidence_precision)}</strong>
      </p>
      <p>
        无答案检索正确率 <strong>{percent(value?.no_answer_correct)}</strong>
      </p>
      <p>
        平均每题{" "}
        <strong>
          {value?.duration_ms == null
            ? "—"
            : `${value.duration_ms.toFixed(1)} ms`}
        </strong>
      </p>
    </div>
  );
}

export function KnowledgeEvaluation({
  id,
  documents,
  canManage,
  paid,
}: {
  id: string;
  documents: { id: string; name: string; revision?: number }[];
  canManage: boolean;
  paid: boolean;
}) {
  const base = `/capabilities/knowledge/${id}`;
  const [datasets, setDatasets] = useState<Dataset[]>([]),
    [experiments, setExperiments] = useState<Experiment[]>([]);
  const [worker, setWorker] = useState(false),
    [canSearch, setCanSearch] = useState(false);
  const [editing, setEditing] = useState<Dataset | null>(null);
  const [name, setName] = useState(""),
    [type, setType] = useState("user");
  const [questions, setQuestions] = useState<Question[]>([question("q1")]);
  const [selected, setSelected] = useState("");
  const [ack, setAck] = useState(false),
    [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(""),
    [right, setRight] = useState("");
  const [details, setDetails] = useState<{
    experiment: Experiment;
    results: Result[];
  } | null>(null);
  const [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const reload = useCallback(async () => {
    const [sources, runs] = await Promise.all([
      api<{ items: Dataset[] }>(base + "/datasets"),
      api<{ items: Experiment[]; worker_enabled: boolean }>(
        base + "/experiments",
      ),
    ]);
    setDatasets(sources.items);
    setExperiments(runs.items);
    setWorker(runs.worker_enabled);
    setSelected((current) =>
      sources.items.some((item) => item.id === current)
        ? current
        : sources.items[0]?.id || "",
    );
  }, [base]);
  useEffect(() => {
    const update = () => void reload().catch((e: Error) => setError(e.message));
    update();
    const timer = window.setInterval(update, 4000);
    void api<{ is_admin?: boolean; permissions?: string[] }>("/auth/me")
      .then((account) =>
        setCanSearch(
          !!account.is_admin ||
            account.permissions === undefined ||
            account.permissions.includes("knowledge.search"),
        ),
      )
      .catch((e: Error) => setError(e.message));
    return () => window.clearInterval(timer);
  }, [reload]);
  async function act(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function reset() {
    setEditing(null);
    setName("");
    setType("user");
    setQuestions([question("q1")]);
  }
  function change(index: number, update: Partial<Question>) {
    setQuestions((items) =>
      items.map((item, position) =>
        position === index ? { ...item, ...update } : item,
      ),
    );
  }
  const a = experiments.find((item) => item.id === left),
    b = experiments.find((item) => item.id === right);
  return (
    <section
      className="knowledge-processing"
      aria-label="知识评测与对照"
    >
      <details>
        <summary>评测题集与检索实验</summary>
        <p>
          使用本人题集标注预期证据，比较检索结果。实验固定题集、文档和模型修订；来源变化、取消或未知请求会停止。它不代替生成答案的引用正确率或拒答验收。
        </p>
        {error && !details && (
          <p
            role="alert"
            className="platform-error"
          >
            {error}
          </p>
        )}
        {notice && <p role="status">{notice}</p>}
        {canManage && (
          <details className="evaluation-editor">
            <summary>{editing ? "编辑评测题集" : "新建评测题集"}</summary>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void act(async () => {
                  const result = await api<{ id?: string }>(
                    editing
                      ? `${base}/datasets/${editing.id}`
                      : base + "/datasets",
                    {
                      method: editing ? "PUT" : "POST",
                      body: JSON.stringify({
                        name,
                        dataset_type: type,
                        questions,
                        revision: editing?.revision || 0,
                      }),
                    },
                  );
                  setNotice("题集已保存，已有实验的快照保持不变。");
                  if (result.id) setSelected(result.id);
                  reset();
                });
              }}
            >
              <div className="knowledge-processing-form">
                <label>
                  题集名称
                  <input
                    aria-label="评测题集名称"
                    maxLength={120}
                    required
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
                <label>
                  题集来源
                  <select
                    aria-label="评测题集来源"
                    value={type}
                    onChange={(event) => setType(event.target.value)}
                  >
                    <option value="user">用户提供题集</option>
                    <option value="synthetic">合成回归题集</option>
                  </select>
                </label>
              </div>
              {questions.map((item, index) => (
                <fieldset
                  className="evaluation-question"
                  key={item.id}
                >
                  <legend>问题 {index + 1}</legend>
                  <label>
                    问题内容
                    <textarea
                      aria-label={`评测问题${index + 1}`}
                      rows={2}
                      required
                      maxLength={1000}
                      value={item.question}
                      onChange={(event) =>
                        change(index, { question: event.target.value })
                      }
                    />
                  </label>
                  <label>
                    题型
                    <select
                      aria-label={`评测题型${index + 1}`}
                      value={item.category}
                      onChange={(event) =>
                        change(index, {
                          category: event.target.value,
                          ...(event.target.value === "no_answer"
                            ? { unanswerable: true, expected: [] }
                            : {}),
                        })
                      }
                    >
                      {Object.entries(categories).map(([value, label]) => (
                        <option
                          key={value}
                          value={value}
                        >
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="evaluation-checkbox">
                    <input
                      type="checkbox"
                      checked={item.unanswerable}
                      disabled={item.category === "no_answer"}
                      onChange={(event) =>
                        change(index, {
                          unanswerable: event.target.checked,
                          ...(event.target.checked ? { expected: [] } : {}),
                        })
                      }
                    />
                    此题没有可用证据
                  </label>
                  {!item.unanswerable && (
                    <div className="evaluation-evidence">
                      <p>
                        预期证据（最多10份文档，可在“分块与修订历史”核对正文）：
                      </p>
                      {documents.map((document) => {
                        const source = item.expected.find(
                          (value) => value.document_id === document.id,
                        );
                        return (
                          <label
                            className="evaluation-checkbox"
                            key={document.id}
                          >
                            <input
                              type="checkbox"
                              checked={!!source}
                              disabled={!source && item.expected.length >= 10}
                              onChange={(event) =>
                                change(index, {
                                  expected: event.target.checked
                                    ? [
                                        ...item.expected,
                                        {
                                          document_id: document.id,
                                          revision: document.revision || 1,
                                          chunk_ids: [],
                                        },
                                      ]
                                    : item.expected.filter(
                                        (value) =>
                                          value.document_id !== document.id,
                                      ),
                                })
                              }
                            />
                            {document.name} · 当前修订 {document.revision || 1}
                            {source &&
                            source.revision !== (document.revision || 1)
                              ? `（已标注旧修订 ${source.revision}，请重新核对后勾选）`
                              : ""}
                            {source?.chunk_ids.length
                              ? ` · 另标注 ${source.chunk_ids.length} 个分块`
                              : ""}
                          </label>
                        );
                      })}
                      {!documents.length && (
                        <p>请先导入文档，再标注有答案的问题。</p>
                      )}
                    </div>
                  )}
                  <button
                    type="button"
                    disabled={questions.length <= 1 || busy}
                    onClick={() =>
                      setQuestions((items) =>
                        items.filter((_, position) => position !== index),
                      )
                    }
                  >
                    删除问题 {index + 1}
                  </button>
                </fieldset>
              ))}
              <div className="evaluation-actions">
                <button
                  type="button"
                  disabled={busy || questions.length >= 50}
                  onClick={() =>
                    setQuestions((items) => [
                      ...items,
                      question(crypto.randomUUID()),
                    ])
                  }
                >
                  添加评测问题
                </button>
                <button
                  disabled={
                    busy ||
                    questions.some(
                      (item) =>
                        !item.unanswerable && item.expected.length === 0,
                    )
                  }
                >
                  保存评测题集
                </button>
                {editing && (
                  <button
                    type="button"
                    onClick={reset}
                    disabled={busy}
                  >
                    取消编辑题集
                  </button>
                )}
              </div>
            </form>
            <label className="knowledge-upload upload-label">
              导入题集 JSON
              <input
                type="file"
                accept=".json"
                aria-label="导入评测题集"
                disabled={busy}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  event.target.value = "";
                  if (!file) return;
                  void act(async () => {
                    if (file.size > 1024 * 1024)
                      throw new Error("题集文件最多1MB");
                    const result = await api<Omit<Dataset, "id">>(
                      base + "/datasets/preview",
                      { method: "POST", body: await file.text() },
                    );
                    setEditing(null);
                    setName(result.name);
                    setType(result.dataset_type);
                    setQuestions(result.questions);
                    setNotice("格式和证据归属已校验，请检查后保存为新题集。");
                  });
                }}
              />
            </label>
          </details>
        )}
        <div className="evaluation-datasets">
          {datasets.map((dataset) => (
            <article
              className="knowledge-operation"
              key={dataset.id}
            >
              <strong>{dataset.name}</strong>
              <p>
                {dataset.dataset_type === "synthetic" ? "合成回归" : "用户题集"}{" "}
                · 修订 {dataset.revision} · {dataset.questions.length} 题
              </p>
              <div className="evaluation-actions">
                {canManage && (
                  <button
                    disabled={busy}
                    onClick={() => {
                      setEditing(dataset);
                      setName(dataset.name);
                      setType(dataset.dataset_type);
                      setQuestions(
                        dataset.questions.map((item) => ({
                          ...item,
                          expected: item.expected.map((source) => ({
                            ...source,
                            chunk_ids: [...source.chunk_ids],
                          })),
                        })),
                      );
                    }}
                  >
                    编辑题集
                  </button>
                )}
                <button
                  onClick={() => {
                    const url = URL.createObjectURL(
                      new Blob(
                        [
                          JSON.stringify(
                            {
                              name: dataset.name,
                              dataset_type: dataset.dataset_type,
                              questions: dataset.questions,
                              revision: 0,
                            },
                            null,
                            2,
                          ),
                        ],
                        { type: "application/json" },
                      ),
                    );
                    const link = document.createElement("a");
                    link.href = url;
                    link.download = dataset.name + ".json";
                    link.click();
                    URL.revokeObjectURL(url);
                  }}
                >
                  导出题集
                </button>
                {canManage && (
                  <button
                    disabled={busy}
                    onClick={() => {
                      if (
                        window.confirm("删除题集？历史实验保留当时的题集快照。")
                      )
                        void act(() =>
                          api(`${base}/datasets/${dataset.id}`, {
                            method: "DELETE",
                          }),
                        );
                    }}
                  >
                    删除题集
                  </button>
                )}
              </div>
            </article>
          ))}
        </div>
        {canManage && (
          <form
            className="knowledge-processing-form"
            onSubmit={(event) => {
              event.preventDefault();
              const dataset = datasets.find((item) => item.id === selected);
              if (!dataset) return;
              void act(async () => {
                await api(base + "/experiments", {
                  method: "POST",
                  body: JSON.stringify({
                    dataset_id: selected,
                    dataset_revision: dataset.revision,
                    request_id: crypto.randomUUID(),
                    acknowledge_model_costs: ack,
                  }),
                });
                setAck(false);
                setNotice("实验已排队；已开始的未知调用不会自动重放。");
              });
            }}
          >
            <label>
              运行题集
              <select
                aria-label="运行评测题集"
                value={selected}
                onChange={(event) => setSelected(event.target.value)}
                required
                disabled={busy}
              >
                <option value="">请选择</option>
                {datasets.map((dataset) => (
                  <option
                    value={dataset.id}
                    key={dataset.id}
                  >
                    {dataset.name} · 修订 {dataset.revision}
                  </option>
                ))}
              </select>
            </label>
            <p>
              每个实验最多100次资源模型调用、512000
              Token、15分钟。关键词不请求模型；向量和重排计入本实验用量。
            </p>
            {paid && (
              <label className="evaluation-checkbox">
                <input
                  type="checkbox"
                  checked={ack}
                  required
                  onChange={(event) => setAck(event.target.checked)}
                />
                我确认本次检索实验可能产生模型费用，并按此预算执行
              </label>
            )}
            <button
              disabled={
                busy || !selected || !worker || !canSearch || (paid && !ack)
              }
            >
              开始检索实验
            </button>
            {!worker && <p>评测后台尚未启动。</p>}
            {!canSearch && <p>发起实验还需要知识检索权限。</p>}
          </form>
        )}
        <p>
          “—”表示无可计算样本或未测。所有题都完成且实验状态为“已完成”，才是完整的一次检索实验；合成题不能替代真实业务验收。
        </p>
        <div className="evaluation-experiments">
          {experiments.map((experiment) => (
            <article
              className="knowledge-operation"
              key={experiment.id}
            >
              <strong>
                {experiment.dataset_snapshot.name} ·{" "}
                {statuses[experiment.status] || experiment.status}
              </strong>
              <p>
                {new Date(experiment.created_at).toLocaleString("zh-CN")} ·
                题集修订 {experiment.dataset_snapshot.revision} ·{" "}
                {experiment.completed_questions}/{experiment.total_questions}题
              </p>
              {experiment.error_code && (
                <p>
                  本次未完整完成，请查看逐题记录并核对来源、权限和用量；没有自动重试。
                </p>
              )}
              <MetricSummary value={experiment.metrics} />
              {experiment.metrics && (
                <details>
                  <summary>按题型查看指标</summary>
                  {Object.entries(experiment.metrics.categories).map(
                    ([category, metrics]) => (
                      <section key={category}>
                        <h3>
                          {categories[category]} · {metrics.questions}题
                        </h3>
                        <MetricSummary value={metrics} />
                      </section>
                    ),
                  )}
                </details>
              )}
              <TokenUsage
                label="实验"
                usage={experiment.usage}
              />
              <div className="evaluation-actions">
                <button
                  disabled={busy}
                  onClick={() =>
                    void act(async () => {
                      const result = await api<{ results: Result[] }>(
                        `${base}/experiments/${experiment.id}`,
                      );
                      setDetails({ experiment, results: result.results });
                    })
                  }
                >
                  查看逐题结果
                </button>
                {canManage &&
                  ["PENDING", "RUNNING"].includes(experiment.status) && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void act(() =>
                          api(`${base}/experiments/${experiment.id}/cancel`, {
                            method: "POST",
                          }),
                        )
                      }
                    >
                      取消实验
                    </button>
                  )}
              </div>
            </article>
          ))}
        </div>
        {experiments.length >= 2 && (
          <details>
            <summary>对比两次实验</summary>
            <div className="knowledge-processing-form">
              <label>
                实验一
                <select
                  aria-label="对照实验一"
                  value={left}
                  onChange={(event) => setLeft(event.target.value)}
                >
                  <option value="">请选择</option>
                  {experiments.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.dataset_snapshot.name} ·{" "}
                      {new Date(item.created_at).toLocaleString("zh-CN")} ·{" "}
                      {statuses[item.status]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                实验二
                <select
                  aria-label="对照实验二"
                  value={right}
                  onChange={(event) => setRight(event.target.value)}
                >
                  <option value="">请选择</option>
                  {experiments.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.dataset_snapshot.name} ·{" "}
                      {new Date(item.created_at).toLocaleString("zh-CN")} ·{" "}
                      {statuses[item.status]}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {a && b && (
              <>
                <p>
                  {a.comparison_key === b.comparison_key
                    ? "题集、来源和检索设置一致，可比较本次结果。"
                    : "题集、来源或检索设置有差异，请结合各自快照解释差异。"}
                  {a.status !== "SUCCEEDED" || b.status !== "SUCCEEDED"
                    ? " 有实验未完整完成，不能据此宣称质量通过。"
                    : ""}
                </p>
                <div className="evaluation-comparison">
                  <div>
                    <h3>
                      实验一 · {a.completed_questions}/{a.total_questions}题
                    </h3>
                    <MetricSummary value={a.metrics} />
                  </div>
                  <div>
                    <h3>
                      实验二 · {b.completed_questions}/{b.total_questions}题
                    </h3>
                    <MetricSummary value={b.metrics} />
                  </div>
                </div>
              </>
            )}
          </details>
        )}
      </details>
      <Dialog.Root
        open={!!details}
        onOpenChange={(open) => {
          if (!open) setDetails(null);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="workspace-overlay" />
          <Dialog.Content
            className="workspace-dialog knowledge-revision-dialog"
            aria-describedby={undefined}
          >
            <Dialog.Title>逐题检索结果</Dialog.Title>
            <p>显示本次实际返回的证据，不代表生成答案的引用已人工验收。</p>
            {error && (
              <p
                role="alert"
                className="platform-error"
              >
                {error}
              </p>
            )}
            <div className="knowledge-chunk-list">
              {details?.results.length === 0 && (
                <p>尚无已完成的题目。失败或未知调用未自动重试。</p>
              )}
              {details?.results.map((result) => (
                <article key={result.id}>
                  <h3>{result.question}</h3>
                  <p>
                    {categories[result.category]} ·{" "}
                    {result.mode === "hybrid" ? "混合检索" : "关键词"} ·
                    Recall@10 {percent(result.recall_at_10)} ·{" "}
                    {result.duration_ms} ms
                  </p>
                  {result.warning && <p>{result.warning}</p>}
                  {!result.items.length && <p>没有返回证据。</p>}
                  {result.items.map((item, index) => (
                    <details key={index}>
                      <summary>
                        {item.name} · 修订 {item.document_revision} ·{" "}
                        {item.location}
                      </summary>
                      <pre>{item.text}</pre>
                    </details>
                  ))}
                </article>
              ))}
            </div>
            <Dialog.Close asChild>
              <button>关闭逐题结果</button>
            </Dialog.Close>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </section>
  );
}
