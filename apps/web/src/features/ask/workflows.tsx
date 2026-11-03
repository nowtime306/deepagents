"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Plus, Play, Save, Upload, Settings2, History, Workflow as WorkflowIcon, Trash2, X, Undo2, Redo2, ListChecks, LayoutGrid } from "lucide-react";
import { api } from "@/lib/api/platform";
import { usePagedResources, type ResourcePage } from "./use-paged-resources";
import { WorkflowAccess } from "./workflow-access";
import { WorkflowSchemaEditor } from "./workflow-schema-editor";
import { WorkflowInputForm, type InputSchema } from "./workflow-input-form";
import { WorkRunPanel } from "./work-run-panel";
import { RepositoryPicker, type RepositoryPermissions } from "./repository-picker";
import { WorkflowEditor, initialDefinition, nodeLabels, type WorkflowDefinition, type WorkflowLayout } from "./workflow-editor";
import { WorkflowRunTrace } from "./workflow-run-trace";
import { executionLabels, nodeExecutions, type NodeEvent, type WorkflowRun } from "./workflow-execution";
import { arrangeWorkflow, nodeReferences, workflowTemplates } from "./workflow-design";
import { useWorkflowHistory } from "./use-workflow-history";
import { WorkflowVariables } from "./workflow-variables";
import { WorkflowVersions, type PublishedWorkflowVersion } from "./workflow-versions";
import "./workflow-studio.css";
import "./resource-list.css";

type Workflow = { id: string; name: string; instructions: string; model_id: string; revision: number;
  definition: WorkflowDefinition; layout: WorkflowLayout; tested_revision?: number | null;
  current_published_version?: number | null; release_revision?: number };
type Model = { id: string; name: string };
type Version = PublishedWorkflowVersion;
type VersionPage = { items: Version[]; current_published_version?: number | null; release_revision?: number };
type Panel = "node" | "settings" | "test" | "history" | "check" | "versions";
type ConfigurationCheck = { valid: boolean; scope: "configuration"; issues: { code: string; node_id: string | null; field: string; message: string }[] };
function blankDefinition(): WorkflowDefinition {
  return { ...initialDefinition(), nodes: [{ id: "start", kind: "start", input: "input", parameters: {} },
    { id: "end", kind: "end", input: "input", parameters: {} }], edges: [{ source: "start", target: "end" }] };
}
const terminal = (run: WorkflowRun) => ["SUCCEEDED", "FAILED", "CANCELLED", "NEEDS_REVIEW"].includes(run.status);

export function Workflows({ models, canManage = true, canExecute = true, canRunWork = true, repositoryPermissions = {} }: {
  models: Model[]; canManage?: boolean; canExecute?: boolean; canRunWork?: boolean; repositoryPermissions?: RepositoryPermissions;
}) {
  const [query, setQuery] = useState("");
  const { items, reload, more, cursor, loading, error: listError } = usePagedResources<Workflow>("/workflows", query);
  const [runCursor, setRunCursor] = useState<string | null>(null), [historyLoading, setHistoryLoading] = useState(false);
  const [opened, setOpened] = useState<Workflow | null>(null);
  const openedId = useRef<string | null>(null);
  const [creating, setCreating] = useState(false), [newName, setNewName] = useState(""), [template, setTemplate] = useState("blank");
  const [name, setName] = useState(""), [instructions, setInstructions] = useState("");
  const [model, setModel] = useState(models[0]?.id || "");
  const [definition, setDefinition] = useState<WorkflowDefinition>(blankDefinition);
  const [layout, setLayout] = useState<WorkflowLayout>({});
  const [panel, setPanel] = useState<Panel>("node");
  const [busy, setBusy] = useState(false), [testing, setTesting] = useState(false), [live, setLive] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [pollError, setPollError] = useState("");
  const [schemaInvalid, setSchemaInvalid] = useState(false), [graphInvalid, setGraphInvalid] = useState(false), [formInvalid, setFormInvalid] = useState(false);
  const [input, setInput] = useState(""), [nodeInput, setNodeInput] = useState("");
  const [runs, setRuns] = useState<WorkflowRun[]>([]), [versions, setVersions] = useState<Version[]>([]), [version, setVersion] = useState("");
  const [versionPreview, setVersionPreview] = useState<Version | null>(null), [previewLoading, setPreviewLoading] = useState(false), [previewError, setPreviewError] = useState("");
  const [linkedDraft, setLinkedDraft] = useState<{ id: string; name: string } | null>(null);
  const previewRequest = useRef<AbortController | null>(null), previewSequence = useRef(0);
  const [activeRunId, setActiveRunId] = useState<string | null>(null), [viewRun, setViewRun] = useState<WorkflowRun | null>(null);
  const [events, setEvents] = useState<NodeEvent[]>([]), [traceMode, setTraceMode] = useState(false), [traceNode, setTraceNode] = useState("");
  const [debugNode, setDebugNode] = useState<string | null>(null);
  const [nodeVariables, setNodeVariables] = useState<Record<string, string>>({});
  const nodeInputs = useRef<Record<string, { text: string; variables: Record<string, string> }>>({});
  const [repository, setRepository] = useState(""), [networkDomains, setNetworkDomains] = useState("");
  const [task, setTask] = useState<{ id: string; title: string; revision: number; workflow_task?: boolean } | null>(null);
  const [access, setAccess] = useState(false);
  const [focusRequest, setFocusRequest] = useState<{ id: string; sequence: number }>();
  const [resultTab, setResultTab] = useState("trace");
  const [restoreVersion, setRestoreVersion] = useState(0);
  const [checking, setChecking] = useState(false), [checkError, setCheckError] = useState("");
  const [validation, setValidation] = useState<(ConfigurationCheck & { key: string }) | null>(null);
  const checkSequence = useRef(0);
  const editHistory = useWorkflowHistory({ definition, layout }, snapshot => {
    setDefinition(snapshot.definition); setLayout(snapshot.layout); setRestoreVersion(value => value + 1); setGraphInvalid(false); setSchemaInvalid(false);
  });
  const changeDefinition = (next: WorkflowDefinition) => { editHistory.remember(); setDefinition(next); };
  const changeLayout = (next: WorkflowLayout) => { editHistory.remember(); setLayout(next); };
  const validationKey = `${opened?.id}:${JSON.stringify(definition)}`;
  const currentCheck = validation?.key === validationKey ? validation : null;
  const applyVersions = useCallback((id: string, page: VersionPage) => {
    if (openedId.current !== id) return;
    setVersions(page.items);
    if (page.release_revision !== undefined) setOpened(previous => previous?.id === id ? { ...previous,
      current_published_version: page.current_published_version ?? null, release_revision: page.release_revision } : previous);
  }, []);
  useEffect(() => () => { previewRequest.current?.abort(); }, [opened?.id]);

  useEffect(() => {
    if (!opened?.id) return;
    let active = true;
    const id = opened.id;
    Promise.all([api<ResourcePage<WorkflowRun>>(`/workflows/${id}/runs`), api<VersionPage>(`/workflows/${id}/versions`)])
      .then(([history, published]) => { if (active) { setRuns(history.items); setRunCursor(history.next_cursor || null); applyVersions(id, published); } })
      .catch((reason) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [opened?.id, applyVersions]);
  useEffect(() => {
    if (!opened?.id || !activeRunId) return;
    let active = true, reading = false;
    const id = opened.id, runId = activeRunId;
    async function refresh() {
      if (reading) return;
      reading = true;
      try {
        const history = await api<ResourcePage<WorkflowRun>>(`/workflows/${id}/runs?run_id=${runId}`);
        if (!active) return;
        setRuns((previous) => [...history.items.filter((item) => !previous.some((old) => old.id === item.id)), ...previous.map((item) => history.items.find((next) => next.id === item.id) || item)]);
        const current = history.items.find((run) => run.id === runId);
        if (!current) return;
        setViewRun(current); setTraceMode(true);
        const result = await api<{ items: NodeEvent[] }>(`/workflows/${id}/runs/${runId}/events`);
        if (active) {
          setEvents((previous) => previous.length === result.items.length && previous.at(-1)?.event_id === result.items.at(-1)?.event_id ? previous : result.items);
          setPollError("");
          if (terminal(current)) setLive(false);
        }
      } catch (reason) { if (active) setPollError((reason as Error).message); }
      finally { reading = false; }
    }
    void refresh();
    const timer = live || testing ? setInterval(() => void refresh(), 1000) : null;
    return () => { active = false; if (timer) clearInterval(timer); };
  }, [opened?.id, activeRunId, live, testing]);

  async function moreRuns() {
    if (!opened || !runCursor || historyLoading) return;
    const id = opened.id; setHistoryLoading(true);
    try {
      const next = await api<ResourcePage<WorkflowRun>>(`/workflows/${id}/runs?before=${encodeURIComponent(runCursor)}`);
      if (openedId.current === id) {
        setRuns((previous) => [...previous, ...next.items.filter((item) => !previous.some((old) => old.id === item.id))]);
        setRunCursor(next.next_cursor || null);
      }
    } catch (reason) { setError((reason as Error).message); } finally { setHistoryLoading(false); }
  }
  function open(item: Workflow, target: Panel = "node") {
    previewRequest.current?.abort(); previewSequence.current++; setVersionPreview(null); setPreviewLoading(false); setPreviewError(""); setLinkedDraft(null);
    editHistory.reset(); checkSequence.current++; setChecking(false); setValidation(null); setCheckError(""); setResultTab("trace"); setFocusRequest(undefined);
    openedId.current = item.id; setOpened(item); setName(item.name); setInstructions(item.instructions); setModel(item.model_id);
    setDefinition(structuredClone(item.definition || initialDefinition())); setLayout(item.layout || {});
    setRunCursor(null);
    setPanel(target); setVersion(""); setRuns([]); setVersions([]); setEvents([]); setViewRun(null); setActiveRunId(null);
    setTraceMode(false); setDebugNode(null); setTraceNode(""); setError(""); setNotice(""); setPollError("");
    setLive(false); setInput(""); setSchemaInvalid(false); setGraphInvalid(false);
    nodeInputs.current = {}; setNodeInput(""); setNodeVariables({});
  }
  const dirty = !!opened && JSON.stringify([name, instructions, model, definition, layout]) !==
    JSON.stringify([opened.name, opened.instructions, opened.model_id, opened.definition, opened.layout]);
  async function persist(): Promise<Workflow> {
    if (!opened) throw new Error("请先创建工作流");
    if (schemaInvalid || graphInvalid) throw new Error("请先修正流程设置中的JSON格式");
    if (!dirty) return opened;
    const id = opened.id;
    await api(`/workflows/${id}`, { method: "PUT", body: JSON.stringify({ name, instructions, model_id: model,
      definition, layout, revision: opened.revision, allow_incomplete: true }) });
    const saved = (await reload(id)).find((item) => item.id === id);
    if (!saved) throw new Error("工作流不存在或已删除");
    if (openedId.current === id) {
      setOpened(saved); setDefinition(saved.definition); setLayout(saved.layout || {});
    }
    return saved;
  }
  async function save() {
    setBusy(true); setError(""); setNotice("");
    try { await persist(); setNotice("草稿已保存"); } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  async function create(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      const graph = (workflowTemplates.find(item => item.id === template) || workflowTemplates[0]).definition();
      const result = await api<{ id: string }>("/workflows", { method: "POST", body: JSON.stringify({ name: newName.trim(),
        model_id: model || models[0]?.id, instructions: "", definition: graph,
        layout: arrangeWorkflow(graph) }) });
      const item = (await reload(result.id)).find((row) => row.id === result.id);
      if (!item) throw new Error("新工作流读取失败，请返回列表刷新");
      setCreating(false); setNewName(""); open(item);
      setInput(workflowTemplates.find(row => row.id === template)?.example || "");
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  async function publish() {
    setBusy(true); setError(""); setNotice("");
    try {
      const saved = await persist();
      const result = await api<{ version: number; created?: boolean; active?: boolean }>(`/workflows/${saved.id}/publish`, { method: "POST", body: JSON.stringify({ revision: saved.revision }) });
      applyVersions(saved.id, await api<VersionPage>(`/workflows/${saved.id}/versions`));
      setNotice(result.created === false ? `该修订已存在历史 v${result.version}${result.active === false ? "（已下线）" : ""}，未改变当前发布版。` : `已发布版本 ${result.version}`);
    } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  async function start(event: FormEvent) {
    event.preventDefault();
    if (!opened || (!debugNode && formInvalid)) return;
    setBusy(true); setTesting(true); setError(""); setNotice(""); setEvents([]); setViewRun(null); setActiveRunId(null); setTraceMode(false); setTraceNode("");
    let id = opened.id, identifier: string | null = null;
    try {
      const saved = version ? opened : await persist(); id = saved.id;
      if (selectedExecution === "work" && !debugNode) {
        const result = await api<{ task_id: string; workflow_run_id: string }>(`/workflows/${id}/work-runs`, { method: "POST", body: JSON.stringify({
          text: input, ...(version ? { version: Number(version) } : { revision: saved.revision }),
          network_domains: networkDomains.split(/[,，\s]+/).filter(Boolean), repository_id: repository || null }) });
        identifier = result.workflow_run_id; setActiveRunId(identifier); setLive(true);
      } else {
        identifier = crypto.randomUUID(); setActiveRunId(identifier); setLive(true);
        const path = debugNode ? `/workflows/${id}/nodes/${debugNode}/test-runs`
          : version ? `/workflows/${id}/versions/${version}/runs` : `/workflows/${id}/test-runs`;
        const result = await api<{ task_id?: string }>(path, { method: "POST", body: JSON.stringify({ text: debugNode ? nodeInput : input,
          ...(debugNode ? { variables: Object.fromEntries(variableReferences.map(reference => [reference, nodeVariables[reference] ?? ""])) } : {}),
          run_id: identifier, ...(!version ? { revision: saved.revision } : {}) }) });
        if (result.task_id) setNotice("节点测试任务已创建，请在运行结果中打开任务并审批。");
      }
    } catch (reason) { setError((reason as Error).message); }
    finally {
      try {
        const historyPage = await api<ResourcePage<WorkflowRun>>(`/workflows/${id}/runs`);
        const history = historyPage.items;
        if (openedId.current === id) {
          setRuns(history); setRunCursor(historyPage.next_cursor || null);
          const current = history.find((run) => run.id === identifier);
          if (current) {
            setViewRun(current); setTraceMode(true); setLive(!terminal(current));
            setEvents((await api<{ items: NodeEvent[] }>(`/workflows/${id}/runs/${current.id}/events`)).items);
          } else setLive(false);
          const updated = (await reload(id)).find((item) => item.id === id);
          if (updated) setOpened(updated);
        }
      } catch (reason) { setPollError((reason as Error).message); }
      setTesting(false); setBusy(false);
    }
  }
  function editMode() {
    previewRequest.current?.abort(); previewSequence.current++; setVersionPreview(null); setPreviewLoading(false); setPreviewError("");
    setTraceMode(false); setActiveRunId(null); setViewRun(null); setEvents([]); setVersion(""); setLive(false); setTraceNode("");
  }
  async function previewVersion(number: number) {
    if (!opened) return;
    const id = opened.id, sequence = ++previewSequence.current;
    previewRequest.current?.abort(); const controller = new AbortController(); previewRequest.current = controller;
    setVersionPreview(null); setPreviewLoading(true); setPreviewError(""); setVersion(""); setTraceMode(false); setActiveRunId(null); setViewRun(null); setEvents([]);
    try {
      const detail = await api<Version>(`/workflows/${id}/versions/${number}`, { signal: controller.signal });
      if (openedId.current === id && sequence === previewSequence.current) setVersionPreview(detail);
    } catch (reason) { if (!controller.signal.aborted && openedId.current === id && sequence === previewSequence.current) setPreviewError((reason as Error).message); }
    finally { if (!controller.signal.aborted && openedId.current === id && sequence === previewSequence.current) setPreviewLoading(false); }
  }
  async function versionAction(operation: (id: string) => Promise<void>): Promise<boolean> {
    if (!opened) return false;
    const id = opened.id; setBusy(true); setError(""); setNotice("");
    try { await operation(id); return true; }
    catch (reason) {
      if (openedId.current === id) {
        setError((reason as Error).message);
        await api<VersionPage>(`/workflows/${id}/versions`).then(page => applyVersions(id, page)).catch(() => {});
      }
      return false;
    } finally { setBusy(false); }
  }
  async function restorePublished(number: number): Promise<boolean> {
    return versionAction(async id => {
      const saved = await persist();
      const result = await api<{ workflow: Workflow; draft_backup_id: string; draft_backup_name: string }>(`/workflows/${id}/versions/${number}/restore`, {
        method: "POST", body: JSON.stringify({ revision: saved.revision }) });
      if (openedId.current === id) { open(result.workflow, "versions"); setVersions(versions); setRuns(runs); setRunCursor(runCursor); setLinkedDraft({ id: result.draft_backup_id, name: result.draft_backup_name }); setNotice(`已恢复 v${number} 为新草稿，请重新整体测试。恢复前草稿已另存为“${result.draft_backup_name}”。`); }
      try {
        const [published, history] = await Promise.all([api<VersionPage>(`/workflows/${id}/versions`), api<ResourcePage<WorkflowRun>>(`/workflows/${id}/runs`), reload()]);
        if (openedId.current === id) { applyVersions(id, published); setRuns(history.items); setRunCursor(history.next_cursor || null); }
      } catch (reason) { if (openedId.current === id) setError(`恢复已成功，备份“${result.draft_backup_name}”已保存，但列表刷新失败：${(reason as Error).message}`); }
    });
  }
  async function copyPublished(number: number, copyName: string): Promise<boolean> {
    return versionAction(async id => {
      const result = await api<{ id: string }>(`/workflows/${id}/versions/${number}/copy`, { method: "POST", body: JSON.stringify({ name: copyName }) });
      if (openedId.current === id) { setLinkedDraft({ id: result.id, name: copyName }); setNotice(`已创建独立副本“${copyName}”，当前草稿编辑保留。`); }
      try { await reload(); } catch (reason) { if (openedId.current === id) setError(`副本“${copyName}”已创建，但列表刷新失败：${(reason as Error).message}`); }
    });
  }
  async function setPublishedCurrent(number: number) {
    await versionAction(async id => {
      await api(`/workflows/${id}/versions/${number}/set-current`, { method: "POST", body: JSON.stringify({ release_revision: opened!.release_revision ?? 1 }) });
      applyVersions(id, await api<VersionPage>(`/workflows/${id}/versions`)); setNotice(`当前发布版已切换为 v${number}。既有固定版本调用保持原版。`);
    });
  }
  async function setPublishedState(number: number, active: boolean) {
    await versionAction(async id => {
      await api(`/workflows/${id}/versions/${number}/state`, { method: "PUT", body: JSON.stringify({ active }) });
      applyVersions(id, await api<VersionPage>(`/workflows/${id}/versions`));
      if (versionPreview?.version === number) await previewVersion(number);
      setNotice(active ? "版本已重新上线，未自动切换当前发布版。" : "版本已下线；如果它曾是当前发布版，当前指针已清空。");
    });
  }
  async function runCurrentPublished() {
    await versionAction(async id => {
      const page = await api<VersionPage>(`/workflows/${id}/versions`); applyVersions(id, page);
      const current = page.items.find(item => item.version === page.current_published_version && item.active !== false && item.schema_supported !== false);
      if (!current) throw new Error("尚无可运行的当前发布版，请先在版本历史中指定。");
      if (openedId.current === id) { editMode(); setVersion(String(current.version)); setPanel("test"); setDebugNode(null); setNotice(`已选择当前发布版 v${current.version}，填写输入后执行该固定版本。`); }
    });
  }
  async function checkConfiguration() {
    if (!opened) return;
    const id = opened.id, sequence = ++checkSequence.current, key = validationKey;
    setPanel("check"); setChecking(true); setCheckError("");
    try {
      const result = await api<ConfigurationCheck>("/workflows/validate", { method: "POST", body: JSON.stringify({ definition }) });
      if (openedId.current === id && sequence === checkSequence.current) setValidation({ ...result, key });
    } catch (reason) { if (openedId.current === id && sequence === checkSequence.current) setCheckError((reason as Error).message); }
    finally { if (openedId.current === id && sequence === checkSequence.current) setChecking(false); }
  }
  async function showTask(id: string) {
    try { setTask(await api(`/work-tasks/${id}`)); } catch (reason) { setError((reason as Error).message); }
  }
  const selectedVersion = versions.find((item) => String(item.version) === version);
  const debugDefinition = definition.nodes.find((node) => node.id === debugNode);
  const debugWork = !!debugDefinition && ["code", "approval"].includes(debugDefinition.kind);
  const variableReferences = debugDefinition ? nodeReferences(debugDefinition).filter(reference => reference !== debugDefinition.input) : [];
  function openNodeTest(id: string, example?: string) {
    if (debugNode) nodeInputs.current[debugNode] = { text: nodeInput, variables: nodeVariables };
    setNodeInput(example ?? nodeInputs.current[id]?.text ?? ""); setNodeVariables(nodeInputs.current[id]?.variables || {});
    setDebugNode(id); setPanel("test"); setTraceMode(false); setActiveRunId(null); setViewRun(null); setEvents([]);
    setTraceNode(""); setError(""); setNotice(""); setPollError("");
  }
  async function reuseNodeInput(identifier = debugNode, selectedRun?: WorkflowRun) {
    if (!opened || !identifier) return;
    if (dirty || live || busy) { setError("请先保存当前修改，再复用同修订的运行输入。"); return; }
    const id = opened.id;
    const source = selectedRun || runs.find((run) => run.snapshot?.revision === opened.revision &&
      (!run.snapshot?.debug_node_id || run.snapshot.debug_node_id === identifier));
    if (!source) { setError("此节点暂无当前修订的运行输入，请先手工填写测试值。"); return; }
    if (source.snapshot?.revision !== opened.revision || source.snapshot.debug_node_id && source.snapshot.debug_node_id !== identifier) { setError("此运行不属于当前草稿修订，不能复用为当前测试值。"); return; }
    setBusy(true); setError("");
    try {
      const history = (await api<{ items: NodeEvent[] }>(`/workflows/${opened.id}/runs/${source.id}/events`)).items;
      const value = history.findLast((event) => event.node_id === identifier && event.event === "node.started")?.data.input;
      if (value === undefined) throw new Error("最近运行未执行此节点，请手工填写测试值。");
      const values: Record<string, string> = {};
      const states = nodeExecutions(history, source.work_status || source.status);
      const selected = definition.nodes.find(node => node.id === identifier);
      const references = selected ? nodeReferences(selected).filter(reference => reference !== selected.input) : [];
      for (const reference of references) {
        const output = source.snapshot?.debug_node_id === identifier ? source.snapshot.debug_variables?.[reference]
          : reference === "input" ? source.input : states[reference] && ["SUCCEEDED", "HANDLED"].includes(states[reference].status) ? states[reference].output : undefined;
        if (output !== undefined) values[reference] = output;
      }
      if (openedId.current !== id) return;
      openNodeTest(identifier, value); setNodeVariables(values); setResultTab("trace"); setNotice(`已从运行 ${source.id} 复用输入，可修改后手动测试。原运行保持不变。`);
    } catch (reason) { if (openedId.current === id) setError((reason as Error).message); } finally { if (openedId.current === id) setBusy(false); }
  }
  const selectedExecution = version ? selectedVersion?.execution || "readonly" : definition.execution || "readonly";
  const inputSchema = (version ? selectedVersion?.input_schema : definition.input_schema || { type: "string" }) as InputSchema | undefined;
  const canvasDefinition = versionPreview ? versionPreview.canvas_definition || versionPreview.definition : traceMode && viewRun ? viewRun.snapshot?.canvas_definition || viewRun.snapshot?.definition || definition
    : version ? selectedVersion?.canvas_definition || selectedVersion?.definition || definition : definition;
  const canvasLayout = versionPreview ? versionPreview.layout || {} : version ? selectedVersion?.layout || {} : layout;
  const locked = !canManage || busy || live || traceMode || !!version || !!versionPreview || previewLoading;
  const canPublish = canManage && !dirty && opened?.tested_revision === opened?.revision && !version && !versionPreview && !previewLoading &&
    (!traceMode || viewRun?.snapshot?.revision === opened?.revision && !viewRun?.snapshot?.debug_node_id);

  if (task) return <WorkRunPanel task={task} repositoryPermissions={repositoryPermissions} close={() => { setTask(null); setLive(true); }} />;
  if (access) return <section className="run-history"><button onClick={() => setAccess(false)}>返回工作流设计</button><WorkflowAccess /></section>;
  if (!opened) return <section className="workflow-library">
    <header className="workflow-library-header"><div><span>工作空间 / 工作流</span><h1>工作流</h1><p>在画布上连接节点，把想法变成可运行的流程。</p></div>
      <div><button type="button" className="workflow-secondary" onClick={() => setAccess(true)}>API 发布管理</button>
        <button className="settings-primary" disabled={!canManage} onClick={() => { setError(""); setCreating(true); setModel(models[0]?.id || ""); }}><Plus size={15}/>新建工作流</button></div>
    </header>
    {(error || listError) && <p role="alert" className="platform-error">{error || listError}{listError && <button onClick={() => void reload().catch(() => {})}>重试加载工作流</button>}</p>}
    {loading && <p role="status">正在加载工作流…</p>}
    {!loading && !listError && !items.length && !query.trim() && <div className="workflow-library-empty"><WorkflowIcon size={36}/><h2>创建你的第一个工作流</h2><p>添加节点、连接逻辑、测试结果，都在同一个画布里完成。</p><button className="settings-primary" disabled={!canManage} onClick={() => setCreating(true)}>创建工作流</button></div>}
    {!loading && !listError && !items.length && query.trim() && <p>没有匹配的工作流。</p>}
    <label className="resource-list-search"><input aria-label="搜索工作流" placeholder="搜索工作流名称" value={query} onChange={(e) => setQuery(e.target.value)}/><span>已加载 {items.length} 个工作流</span></label>
    {!loading && items.length > 0 && !items.some((item) => `${item.name} ${item.instructions || ""}`.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的工作流。</p>}
    <div className="workflow-library-grid">{items.filter((item) => `${item.name} ${item.instructions || ""}`.toLowerCase().includes(query.toLowerCase())).map((item) => <article className="run-history-card workflow-library-card" key={item.id}>
      <div className="workflow-library-card-title"><WorkflowIcon size={20}/><strong>{item.name}</strong></div>
      <p>草稿 · 修订 {item.revision} · {item.tested_revision === item.revision ? "当前修订已测试" : "待测试"}</p>
      <small>{item.definition?.nodes.length || 0}个节点 · {item.definition?.execution === "work" ? "持久任务" : "只读流程"} · {item.current_published_version ? `当前发布版 v${item.current_published_version}` : "尚未指定发布版"}</small>
      <footer><button className="workflow-secondary" onClick={() => open(item)}>编辑</button><button className="workflow-secondary" onClick={() => open(item, "test")}>测试或运行</button>
        <button aria-label="删除" title="删除工作流" disabled={!canManage} onClick={async () => { if (!confirm(`删除工作流“${item.name}”及其测试记录？`)) return;
          try { await api(`/workflows/${item.id}`, { method: "DELETE" }); await reload(); } catch (reason) { setError((reason as Error).message); } }}><Trash2 size={15}/></button></footer>
    </article>)}</div>
    {cursor && <button type="button" disabled={loading} onClick={() => void more().catch(() => {})}>加载更多工作流</button>}
    {creating && <div className="workflow-dialog-backdrop"><section role="dialog" aria-modal="true" aria-labelledby="workflow-create-title" className="workflow-create-dialog">
      <header><h2 id="workflow-create-title">创建工作流</h2><button type="button" aria-label="关闭创建工作流" disabled={busy} onClick={() => setCreating(false)}><X size={18}/></button></header>
      <form onSubmit={create}><label>工作流名称<input autoFocus required maxLength={100} value={newName} onChange={(event) => setNewName(event.target.value)}/></label>
        <label>默认聊天模型<select aria-label="工作流聊天模型" required value={model || models[0]?.id || ""} onChange={(event) => setModel(event.target.value)}>{!models.length && <option value="">请先配置模型</option>}{models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <label>初始模板<select aria-label="工作流初始模板" value={template} onChange={(event) => setTemplate(event.target.value)}>{workflowTemplates.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
        <div className="workflow-template-grid" aria-label="场景模板">{workflowTemplates.map(item => <button type="button" key={item.id} aria-pressed={template === item.id} onClick={() => setTemplate(item.id)}>
          <strong>{item.name}</strong><span>{item.description}</span><small>{item.definition().nodes.length} 个节点 · {item.resource}</small>
        </button>)}</div>
        <div className="workflow-template-example"><strong>样例输入</strong><pre>{workflowTemplates.find(item => item.id === template)?.example}</pre><strong>预期结果</strong><pre>{workflowTemplates.find(item => item.id === template)?.expected}</pre></div>
        <p>创建后进入画布，随时添加节点、配置参数和测试。</p>{error && <p role="alert" className="platform-error">{error}</p>}
        <button className="settings-primary" disabled={busy || !models.length || !newName.trim()}>{busy ? "正在创建…" : "进入画布"}</button></form>
    </section></div>}
  </section>;

  const settings = <div className="workflow-panel-content"><h2>流程设置</h2>
    <fieldset disabled={locked}><label>工作流名称<input required maxLength={100} value={name} onChange={(event) => setName(event.target.value)}/></label>
      <label>默认聊天模型<select aria-label="工作流聊天模型" required value={model} onChange={(event) => setModel(event.target.value)}>{models.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
      <label>模型指令<textarea maxLength={12000} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="默认模型节点指令，也可在各节点单独配置。"/></label>
      <WorkflowSchemaEditor key={restoreVersion} definition={definition} onChange={changeDefinition} onInvalid={setSchemaInvalid}/>
    </fieldset></div>;
  const versionsPanel = <WorkflowVersions items={versions} current={opened.current_published_version ?? null} preview={versionPreview}
    loading={previewLoading} error={previewError} busy={busy || live} canManage={canManage} dirty={dirty} onPreview={number => void previewVersion(number)}
    onRestore={restorePublished} onCopy={copyPublished} onCurrent={number => void setPublishedCurrent(number)} onState={(number, active) => void setPublishedState(number, active)} onEdit={editMode}/>;
  const history = <div className="workflow-panel-content"><h2>运行历史</h2><p className="workflow-panel-help">点击记录，查看当时的画布快照和节点结果。</p>
    {!runs.length && <p>尚无运行记录。</p>}{runs.map((run) => <article className="workflow-history-item" data-run-id={run.id} key={run.id}>
      <button type="button" disabled={testing || live} onClick={() => { editMode(); setActiveRunId(run.id); setEvents([]); setViewRun(null); setPanel("test"); setTraceNode(""); setError(""); setPollError(""); }}>
        <strong>{executionLabels[run.work_status || run.status] || "状态未知"}</strong><small>{new Date(run.created_at).toLocaleString("zh-CN")}</small>
        <span>{run.snapshot?.debug_node_id ? "单节点测试" : run.snapshot?.published_version ? `已发布版本 ${run.snapshot.published_version}` : `草稿修订 ${run.snapshot?.revision ?? "未知"}`}</span>
      </button></article>)}
    {runCursor && <button type="button" disabled={historyLoading} onClick={() => void moreRuns()}>加载更早工作流运行</button>}
    <button type="button" className="workflow-secondary" onClick={() => setPanel("versions")}>查看版本历史</button></div>;
  const testingPanel = <div className="workflow-panel-content"><h2>{debugDefinition ? `测试节点 · ${debugDefinition.name || nodeLabels[debugDefinition.kind]}` : "测试运行"}</h2>
    <div className="workflow-test-modes"><button type="button" aria-pressed={!debugNode} disabled={busy || live} onClick={() => setDebugNode(null)}>整个流程</button>
      {debugNode && <span>单节点：{definition.nodes.find((node) => node.id === debugNode)?.name || debugNode}</span>}</div>
    <form className="workflow-run-form" onSubmit={start}>
      <h3 className="workflow-test-title">测试：{opened.name}</h3>
      {!debugNode && <label>运行版本<select aria-label="运行工作流版本" disabled={busy || live} value={version} onChange={(event) => { setVersion(event.target.value); setTraceMode(false); setActiveRunId(null); setViewRun(null); setEvents([]); }}>
        <option value="">当前草稿</option>{versions.map((item) => <option key={item.version} value={item.version} disabled={item.active === false || item.schema_supported === false}>已发布版本 {item.version}（草稿修订 {item.draft_revision}）</option>)}</select></label>}
      {debugNode ? <><p className="workflow-panel-help">只执行此节点{debugDefinition && ["subworkflow", "iteration", "loop", "parallel"].includes(debugDefinition.kind) ? "及其配置的子流程" : ""}，不会运行原图上游或下游。输入来源：{debugDefinition?.input === "input" ? "流程输入" : definition.nodes.find((node) => node.id === debugDefinition?.input)?.name || debugDefinition?.input}。</p>
        <button type="button" className="workflow-secondary" disabled={busy || live || dirty} onClick={() => void reuseNodeInput()}>复用最近运行输入</button>
        <label>节点测试输入<textarea aria-label="节点测试输入" required maxLength={16000} value={nodeInput} disabled={busy || live} onChange={(event) => setNodeInput(event.target.value)}/></label>
        {variableReferences.map((reference) => <label key={reference}>变量 {reference} 的测试值（可留空测试空文本）<textarea aria-label={`变量 ${reference} 的测试值`} maxLength={16000} disabled={busy || live}
          value={nodeVariables[reference] || ""} onChange={(event) => setNodeVariables((values) => ({ ...values, [reference]: event.target.value }))}/></label>)}
        {debugWork && <p className="workflow-panel-help">此节点创建独立测试任务；请在任务详情确认审批后执行。</p>}</>
        : inputSchema ? <WorkflowInputForm key={`${opened.id}:${version}`} schema={inputSchema} text={input} change={setInput} onInvalid={setFormInvalid} disabled={busy || live}/> : <p>正在读取版本输入结构…</p>}
      {selectedExecution === "work" && !debugNode && <>{repositoryPermissions.use && <RepositoryPicker value={repository} onChange={setRepository}/>}
        <label>代码联网域名<input aria-label="流程代码授权域名" disabled={busy || live} value={networkDomains} onChange={(event) => setNetworkDomains(event.target.value)}/></label>
        <p className="workflow-panel-help">将创建持久任务；代码和人工审批仍需在任务详情确认。</p></>}
      <button className="settings-primary" disabled={!canExecute || busy || live || debugWork && !canRunWork || !debugNode && (formInvalid || !inputSchema || !!version && selectedVersion?.active === false || selectedExecution === "work" && !canRunWork)}>
        <Play size={13}/>{testing ? "运行中…" : debugNode ? "运行单节点测试" : selectedExecution === "work" ? "创建流程任务" : version ? "运行已发布版本" : "运行测试"}</button>
      {live && !testing && <p role="status">正在等待后台运行，节点状态将自动更新。</p>}
    {pollError && <p role="status" className="workflow-panel-help">状态读取暂不可用：{pollError}。不会重新发送执行请求。</p>}
    <div className="workflow-result-tabs" role="tablist" aria-label="运行数据视图"><button type="button" role="tab" aria-selected={resultTab === "trace"} onClick={() => setResultTab("trace")}>结果与轨迹</button><button type="button" role="tab" aria-selected={resultTab === "variables"} onClick={() => setResultTab("variables")}>变量</button></div>
    {resultTab === "variables" ? <WorkflowVariables key={viewRun?.id || "empty"} run={viewRun} events={events}
      reusable={!!viewRun && canExecute && !busy && !live && !dirty && !version && viewRun.snapshot?.revision === opened.revision}
      onReuse={id => void reuseNodeInput(id, viewRun || undefined)}/>
      : <WorkflowRunTrace run={viewRun} events={events} selected={traceNode} onSelect={setTraceNode} onTask={(id) => void showTask(id)}/>}
    </form>
  </div>;
  const checklist = <section className="workflow-panel-content" aria-label="流程检查结果"><h2>流程检查</h2><p className="workflow-panel-help">仅检查当前配置，不调用模型或工具，不代表资源已就绪；执行和发布仍会检查实时权限与资源。</p>
    {checking && <p role="status">正在检查配置…</p>}{checkError && <p role="alert">{checkError}</p>}
    {!checking && !checkError && (!currentCheck ? <p>当前配置尚未检查，或检查后又有修改。</p> : currentCheck.valid ? <p role="status">配置检查通过。请测试实际结果后再发布。</p> : <div className="workflow-checklist">{currentCheck.issues.map((issue, index) => <button type="button" key={`${issue.code}:${issue.node_id}:${index}`} disabled={!issue.node_id || !definition.nodes.some(item => item.id === issue.node_id)} onClick={() => {
      if (issue.node_id) { setFocusRequest(previous => ({ id: issue.node_id!, sequence: (previous?.sequence || 0) + 1 })); setPanel("node"); }
    }}><strong>{issue.node_id ? definition.nodes.find(item => item.id === issue.node_id)?.name || issue.node_id : "整个流程"}</strong><span>{issue.message}</span><small>{issue.code} · {issue.field}</small></button>)}</div>)}
    <button type="button" className="workflow-secondary" disabled={checking || !canManage || locked} onClick={() => void checkConfiguration()}>重新检查</button>
  </section>;
  return <section className="workflow-studio" aria-label="工作流工作台" onKeyDown={event => {
    if (locked || !(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== "z" || (event.target as HTMLElement).closest("input,textarea,select,[contenteditable=true]")) return;
    event.preventDefault(); if (event.shiftKey) editHistory.redo(); else editHistory.undo();
  }}>
    <header className="workflow-studio-header"><div><button type="button" aria-label="返回工作流列表" disabled={busy || live} onClick={() => { if (dirty && !confirm("草稿尚未保存，确定返回列表？")) return; openedId.current = null; setOpened(null); void reload().catch((reason) => setError(reason.message)); }}><ArrowLeft size={18}/></button>
      <div><h1>{versionPreview?.name || name}</h1><span>{versionPreview ? `历史 v${versionPreview.version} · 只读预览` : dirty ? "有未保存修改" : `已保存 · 修订 ${opened.revision}`} · {opened.current_published_version ? `当前发布 v${opened.current_published_version}` : "尚未指定当前发布版"}</span></div></div>
      <div className="workflow-studio-actions"><button type="button" className="workflow-secondary" aria-label="撤销编辑" title="撤销（Ctrl+Z）" disabled={locked || !editHistory.canUndo} onClick={editHistory.undo}><Undo2 size={14}/></button>
        <button type="button" className="workflow-secondary" aria-label="重做编辑" title="重做（Ctrl+Shift+Z）" disabled={locked || !editHistory.canRedo} onClick={editHistory.redo}><Redo2 size={14}/></button>
        <button type="button" className="workflow-secondary" disabled={locked} onClick={() => { try { changeLayout(arrangeWorkflow(definition)); setNotice("画布已整理，保存草稿后保留布局。"); } catch (reason) { setError((reason as Error).message); } }}><LayoutGrid size={14}/>整理画布</button>
        <button type="button" className="workflow-secondary" disabled={locked || checking} onClick={() => void checkConfiguration()}><ListChecks size={14}/>检查流程</button>
        <button type="button" className="workflow-secondary" disabled={busy || live || !!versionPreview || previewLoading} onClick={() => setPanel(panel === "settings" ? "node" : "settings")}><Settings2 size={14}/>流程设置</button>
        <button type="button" className="workflow-secondary" onClick={() => setPanel(panel === "history" ? "node" : "history")}><History size={14}/>运行历史</button>
        <button type="button" className="workflow-secondary" disabled={busy || live} onClick={() => setPanel("versions")}><History size={14}/>版本历史</button>
        <button type="button" className="workflow-secondary" disabled={locked || graphInvalid || schemaInvalid} onClick={() => void save()}><Save size={14}/>保存草稿</button>
        <button type="button" className="workflow-secondary" disabled={busy || live} onClick={() => { editMode(); setPanel("test"); setDebugNode(null); }}><Play size={14}/>测试运行</button>
        <button type="button" className="workflow-secondary" disabled={!canExecute || busy || live || !opened.current_published_version} onClick={() => void runCurrentPublished()}><Play size={14}/>运行当前发布版</button>
        <button type="button" className="settings-primary" disabled={busy || live || graphInvalid || schemaInvalid || !canPublish} title={canPublish ? "发布已通过整体测试的修订" : "请先保存并通过整个流程测试"} onClick={() => void publish()}><Upload size={14}/>发布版本</button></div>
    </header>
    {(traceMode || version || versionPreview || previewLoading) && <div className="workflow-snapshot-banner"><span>{previewLoading ? "正在读取历史版本…" : versionPreview ? `预览历史 v${versionPreview.version}，当前草稿编辑保留。` : traceMode ? "正在查看运行时画布快照，节点参数只读。" : `已发布版本 ${version} · 只读画布`}</span><button disabled={busy || live} onClick={editMode}>返回编辑</button></div>}
    {(error || notice) && <div className={error ? "workflow-studio-message platform-error" : "workflow-studio-message"} role={error ? "alert" : "status"}>{error || notice}</div>}
    {linkedDraft && <div className="workflow-version-linked"><span>独立草稿：{linkedDraft.name}</span><button type="button" disabled={busy || live || dirty} title={dirty ? "请先保存当前草稿，再打开副本" : "打开独立草稿"} onClick={() => void versionAction(async () => { const fresh = await api<Workflow>(`/workflows/${linkedDraft.id}`); open(fresh); })}>打开副本</button>{dirty && <small>保存当前编辑后可打开副本。</small>}</div>}
    {versionPreview?.schema_supported === false ? <div className="workflow-version-unsupported">{versionsPanel}</div> : <WorkflowEditor key={`${opened.id}:${versionPreview ? `preview-${versionPreview.version}` : previewLoading ? "preview-loading" : version ? `published-${version}` : "draft"}`} definition={canvasDefinition} layout={canvasLayout} models={models} onChange={changeDefinition} onLayout={changeLayout} onInvalid={invalid => { if (!versionPreview && !previewLoading) setGraphInvalid(invalid); }} focusRequest={focusRequest} restoreVersion={restoreVersion}
      readOnly={locked} events={traceMode ? events : undefined} runStatus={traceMode ? viewRun?.work_status || viewRun?.status : undefined}
      sidePanel={panel === "settings" ? settings : panel === "history" ? history : panel === "test" ? testingPanel : panel === "check" ? checklist : panel === "versions" ? versionsPanel : undefined}
      onSelect={(id) => { if (traceMode) { setTraceNode(id); setPanel("test"); } else setPanel("node"); }}
      onTestNode={canExecute && !busy && !live && !version && !versionPreview && !previewLoading && (!traceMode ||
        !!viewRun?.snapshot?.debug_node_id && viewRun.snapshot.revision === opened.revision && !dirty) ? openNodeTest : undefined}
      onClosePanel={() => setPanel("node")}/>}
  </section>;
}
