"use client";

import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Plus, Copy, Trash2, Play, X, Settings2, Workflow, CheckCircle2, LoaderCircle } from "lucide-react";
import {
  ReactFlow,
  Background,
  Controls,
  Panel,
  Handle,
  Position,
  applyNodeChanges,
  applyEdgeChanges,
  type Connection,
  type Node,
  type NodeProps,
  type Edge,
  type ReactFlowInstance,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api } from "@/lib/api/platform";
import { executionLabels, nodeExecutions, type NodeEvent } from "./workflow-execution";
import { upstreamSources } from "./workflow-design";
import "./workflow-studio.css";

export const nodeLabels = {
  start: "开始",
  end: "结束",
  llm: "大语言模型",
  agent: "智能体",
  rag: "知识检索",
  mcp: "MCP 工具",
  http: "网页读取",
  condition: "条件分支",
  transform: "文本转换",
  subworkflow: "子工作流",
  iteration: "数组迭代",
  parallel: "并行子流程",
  loop: "条件循环",
  approval: "人工审批",
  code: "Python代码",
};
type Kind = keyof typeof nodeLabels;
export type WorkflowNode = {
  id: string;
  kind: Kind;
  name?: string | null;
  input: string;
  parameters: Record<string, unknown>;
};
export type WorkflowEdge = {
  source: string;
  target: string;
  case?: "true" | "false" | "error" | null;
};
export type WorkflowDefinition = {
  execution?: "readonly" | "work";
  schema_version?: number;
  input_schema?: { type: string; [key: string]: unknown };
  output_schema?: { type: string; [key: string]: unknown };
  node_budget?: number;
  model_calls?: number;
  token_limit?: number;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};
export type WorkflowLayout = Record<string, { x: number; y: number }>;
export function initialDefinition(): WorkflowDefinition {
  return {
    schema_version: 1,
    input_schema: { type: "string" },
    output_schema: { type: "string" },
    node_budget: 30,
    nodes: [
      { id: "start", kind: "start", input: "input", parameters: {} },
      { id: "llm", kind: "llm", input: "input", parameters: {} },
      { id: "end", kind: "end", input: "llm", parameters: {} },
    ],
    edges: [
      { source: "start", target: "llm" },
      { source: "llm", target: "end" },
    ],
  };
}
type CanvasNode = Node<{ label: string; kind: Kind; status?: string; duration?: number; onTest?: (id: string) => void }, "workflow">;
const edgeId = (edge: WorkflowEdge) =>
  `${edge.source}:${edge.case || "out"}:${edge.target}`;
function NodeView({ id, data, selected }: NodeProps<CanvasNode>) {
  return (
    <div className={`workflow-node${selected ? " selected" : ""}`} data-kind={data.kind} data-status={data.status || "idle"}>
      {data.kind !== "start" && (
        <Handle
          type="target"
          position={Position.Left}
          title="输入"
        />
      )}
      <div className="workflow-node-title"><span className="workflow-node-icon"><Workflow size={16} /></span><strong>{data.label}</strong></div>
      <small>{nodeLabels[data.kind]}</small>
      <button type="button" className="workflow-node-test nodrag nopan" aria-label={`测试${data.label}节点`} disabled={!data.onTest}
        onClick={(event) => { event.stopPropagation(); data.onTest?.(id); }}><Play size={12}/>测试</button>
      {data.status && <div className="workflow-node-status">
        {data.status === "RUNNING" ? <LoaderCircle size={12} /> : data.status === "SUCCEEDED" ? <CheckCircle2 size={12} /> : null}
        {executionLabels[data.status] || "未运行"}{data.duration !== undefined ? ` · ${data.duration}ms` : ""}
      </div>}
      {["condition", "transform", "iteration", "loop"].includes(data.kind) ? (
        <>
          <span className="workflow-branch first">
            {data.kind === "condition" ? "命中" : "正常"}
          </span>
          <span className="workflow-branch second">
            {data.kind === "condition" ? "默认" : "错误"}
          </span>
          <Handle
            type="source"
            id={data.kind === "condition" ? "true" : "out"}
            position={Position.Right}
            style={{ top: "35%" }}
            title={data.kind === "condition" ? "命中出口" : "正常出口"}
          />
          <Handle
            type="source"
            id={data.kind === "condition" ? "false" : "error"}
            position={Position.Right}
            style={{ top: "70%" }}
            title={data.kind === "condition" ? "默认出口" : "错误出口（可选）"}
          />
        </>
      ) : (
        data.kind !== "end" && (
          <Handle
            type="source"
            id="out"
            position={Position.Right}
            title="输出"
          />
        )
      )}
    </div>
  );
}
const nodeTypes = { workflow: NodeView };
type Resource = {
  id: string;
  name: string;
  enabled?: boolean;
  tools?: { name: string; enabled: boolean; read_only: boolean }[];
};
const noEvents: NodeEvent[] = [];
const nodeGroups = [
  { name: "AI 与知识", kinds: ["llm", "agent", "rag"] },
  { name: "数据与逻辑", kinds: ["condition", "transform", "template", "subworkflow", "iteration", "parallel", "loop", "end"] },
  { name: "工具与人工", kinds: ["mcp", "http", "approval", "code"] },
];

export function WorkflowEditor({
  definition,
  layout,
  models,
  onChange,
  onLayout,
  onInvalid,
  readOnly = false,
  events = noEvents,
  runStatus,
  sidePanel,
  onSelect,
  onTestNode,
  onClosePanel,
  focusRequest,
  restoreVersion = 0,
}: {
  definition: WorkflowDefinition;
  layout: WorkflowLayout;
  models: Resource[];
  onChange: (definition: WorkflowDefinition) => void;
  onLayout: (layout: WorkflowLayout) => void;
  onInvalid: (invalid: boolean) => void;
  readOnly?: boolean;
  events?: NodeEvent[];
  runStatus?: string;
  sidePanel?: ReactNode;
  onSelect?: (id: string) => void;
  onTestNode?: (id: string, example?: string) => void;
  onClosePanel?: () => void;
  focusRequest?: { id: string; sequence: number };
  restoreVersion?: number;
}) {
  const [nodes, setNodes] = useState<CanvasNode[]>([]);
  const flow = useRef<ReactFlowInstance<CanvasNode> | null>(null);
  const [edges, setEdges] = useState<Edge[]>([]);
  const [selected, setSelected] = useState(() => definition.nodes.find((node) => !["start", "end"].includes(node.kind))?.id || definition.nodes[0]?.id || "start");
  const [palette, setPalette] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [addKind, setAddKind] = useState<Kind>("rag");
  const [knowledge, setKnowledge] = useState<Resource[]>([]);
  const [agents, setAgents] = useState<Resource[]>([]);
  const [servers, setServers] = useState<Resource[]>([]);
  const [workflowVersions, setWorkflowVersions] = useState<Resource[]>([]);
  const [error, setError] = useState("");
  const [argumentsText, setArgumentsText] = useState("{}");
  const [argumentError, setArgumentError] = useState("");
  const [nodeQuery, setNodeQuery] = useState("");
  const [variable, setVariable] = useState("input"), [argumentName, setArgumentName] = useState("");
  const [jsonSample, setJsonSample] = useState("");
  const appliedRestore = useRef(restoreVersion);
  const current = definition.nodes.find((node) => node.id === selected);
  const available = useMemo(() => upstreamSources(definition, selected), [definition, selected]);
  const legacyStart = current?.input !== "input" && definition.nodes.find(node => node.id === current?.input && node.kind === "start");
  useEffect(() => {
    if (!focusRequest) return;
    setSelected(focusRequest.id); setInspectorOpen(true);
    void flow.current?.fitView({ nodes: [{ id: focusRequest.id }], padding: .5, maxZoom: 1 });
  }, [focusRequest]);
  useEffect(() => {
    let cancelled = false;
    requestAnimationFrame(() => requestAnimationFrame(() => {
      if (!cancelled) void flow.current?.fitView({ padding: .25, maxZoom: 1 });
    }));
    return () => { cancelled = true; };
  }, [definition.nodes.length]);

  useEffect(() => {
    const execution = nodeExecutions(events, runStatus);
    const restored = appliedRestore.current !== restoreVersion;
    appliedRestore.current = restoreVersion;
    setNodes((previous) =>
      definition.nodes.map((node, index) => ({
        id: node.id,
        ariaLabel: node.name || nodeLabels[node.kind],
        ariaRoleDescription: "流程节点",
        type: "workflow",
        deletable: !readOnly && node.kind !== "start" && (node.kind !== "end" || definition.nodes.filter((item) => item.kind === "end").length > 1),
        position: layout[node.id] ||
          (!restored && previous.find((item) => item.id === node.id)?.position) || {
            x: 40 + index * 300,
            y: 90,
          },
        data: { label: node.name || nodeLabels[node.kind], kind: node.kind, status: execution[node.id]?.status, duration: execution[node.id]?.duration_ms,
          onTest: onTestNode ? (id: string) => { setSelected(id); setInspectorOpen(true); onTestNode(id); } : undefined },
        selected: node.id === selected,
      })),
    );
    setEdges(
      definition.edges.map((edge) => ({
        id: edgeId(edge),
        source: edge.source,
        target: edge.target,
        ariaLabel: `从 ${edge.source} 到 ${edge.target} 的连线`,
        sourceHandle: edge.case || "out",
        deletable: !readOnly,
        animated: execution[edge.source]?.status === "RUNNING",
        className: execution[edge.source]?.status === "SUCCEEDED" ? "workflow-edge-completed" : "",
        label:
          edge.case === "true"
            ? "命中"
            : edge.case === "false"
              ? "默认"
              : edge.case === "error"
                ? "错误"
                : undefined,
      })),
    );
  }, [definition, layout, selected, events, runStatus, readOnly, onTestNode, restoreVersion]);
  useEffect(() => {
    let active = true;
    Promise.all([
      api<{ items: Resource[] }>("/capabilities/knowledge"),
      api<{ items: Resource[] }>("/agents"),
      api<{ items: Resource[] }>("/capabilities/mcp"),
      api<{ items: Resource[] }>("/workflows/options"),
    ])
      .then(([k, a, m, w]) => {
        if (active) {
          setKnowledge(k.items);
          setAgents(a.items.filter((item) => item.enabled));
          setServers(m.items.filter((item) => item.enabled));
          setWorkflowVersions(w.items);
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
    setArgumentsText(
      JSON.stringify(current?.parameters.arguments || {}, null, 2),
    );
    setArgumentError("");
    setVariable("input"); setArgumentName(""); setJsonSample("");
    onInvalid(false);
  }, [selected, restoreVersion]);

  function change(node: WorkflowNode) {
    onChange({
      ...definition,
      nodes: definition.nodes.map((item) =>
        item.id === node.id ? node : item,
      ),
    });
  }
  function parameter(key: string, value: unknown) {
    if (current)
      change({
        ...current,
        parameters: { ...current.parameters, [key]: value },
      });
  }
  const text = (key: string) =>
    typeof current?.parameters[key] === "string"
      ? (current.parameters[key] as string)
      : "";
  function connect(connection: Connection) {
    if (readOnly) return;
    if (!connection.source || !connection.target) return;
    const source = definition.nodes.find(
      (node) => node.id === connection.source,
    );
    const edge: WorkflowEdge = {
      source: connection.source,
      target: connection.target,
      case:
        ["transform", "iteration", "loop"].includes(source?.kind || "") &&
        connection.sourceHandle === "error"
          ? "error"
          : source?.kind === "condition"
            ? (connection.sourceHandle as "true" | "false")
            : null,
    };
    if (!definition.edges.some((item) => edgeId(item) === edgeId(edge)))
      onChange({ ...definition, edges: [...definition.edges, edge] });
  }
  function remove(ids: Set<string>, removedEdges = new Set<string>()) {
    if (readOnly) return;
    ids = new Set([...ids].filter((id) => definition.nodes.some((node) => node.id === id && node.kind !== "start" &&
      (node.kind !== "end" || definition.nodes.filter((item) => item.kind === "end").length > 1))));
    let edges = definition.edges.filter((edge) => !ids.has(edge.source) && !ids.has(edge.target) && !removedEdges.has(edgeId(edge)));
    let remaining = definition.nodes.filter((node) => !ids.has(node.id));
    if (ids.size === 1) {
      const id = [...ids][0], incoming = definition.edges.filter((edge) => edge.target === id), outgoing = definition.edges.filter((edge) => edge.source === id);
      if (incoming.length === 1 && outgoing.length === 1 && !incoming[0].case && !outgoing[0].case) {
        edges = [...edges, { source: incoming[0].source, target: outgoing[0].target }];
        remaining = remaining.map((node) => node.input === id ? { ...node, input: definition.nodes.find((source) => source.id === incoming[0].source)?.kind === "start" ? "input" : incoming[0].source } : node);
      }
    }
    onChange({
      ...definition,
      nodes: remaining, edges,
    });
    onLayout(Object.fromEntries(Object.entries(layout).filter(([id]) => !ids.has(id))));
    if (ids.has(selected)) setSelected(remaining[0]?.id || "");
  }
  function addNode(kind: Kind, copy = false, detached = false, operation?: string) {
    if (readOnly) return;
    if (definition.nodes.length >= 30) { setError("一个流程最多30个节点"); return; }
    const id = `${kind}_${crypto.randomUUID().slice(0, 8)}`;
    const incoming = current?.kind === "end" ? definition.edges.filter((edge) => edge.target === current.id && !edge.case) : [];
    const previous = current?.kind === "end" ? definition.nodes.find((node) => node.id === incoming[0]?.source) : current;
    const outgoing = definition.edges.filter((edge) => edge.source === previous?.id && !edge.case);
    const insert = !copy && !detached && previous?.kind !== "condition" && outgoing.length === 1 ? outgoing[0] : undefined;
    const parameters = kind === "transform" ? { operation: operation || "text", ...(operation === "template" ? { template: "{{input}}" } : {}) } : kind === "condition" ? { operator: "contains", value: "" }
      : kind === "loop" ? { operator: "not_empty", max_iterations: 5 } : kind === "approval" ? { prompt: "请确认是否继续" }
      : kind === "code" ? { code: "import sys\nprint(sys.stdin.read())", timeout: 30 } : {};
    const node: WorkflowNode = { id, kind, input: previous && previous.kind !== "start" ? previous.id : "input",
      parameters: copy && current ? structuredClone(current.parameters) : parameters,
      ...(copy && current ? { name: `${current.name || nodeLabels[kind]} 副本`, input: current.input } : {}) };
    const position = nodes.find((item) => item.id === previous?.id)?.position || { x: 80, y: 90 };
    const positions = Object.fromEntries(nodes.map((item) => [item.id, item.position]));
    positions[id] = { x: Math.min(8500, position.x + 260), y: position.y + (insert ? 0 : 140) };
    if (insert) for (const [key, value] of Object.entries(positions)) {
      if (key !== id && value.x > position.x) positions[key] = { ...value, x: Math.min(9000, value.x + 260) };
    }
    onChange({ ...definition, execution: ["approval", "code"].includes(kind) ? "work" : definition.execution,
      nodes: [...definition.nodes.map((item) => insert && item.id === insert.target && (item.input === previous?.id || previous?.kind === "start" && item.input === "input") ? { ...item, input: id } : item), node],
      edges: insert ? [...definition.edges.filter((edge) => edge !== insert), { source: insert.source, target: id }, { source: id, target: insert.target }] : definition.edges });
    onLayout(positions); setSelected(id); setPalette(false); setInspectorOpen(true); onSelect?.(id);
  }
  const resourceSelect = (
    key: string,
    label: string,
    resources: Resource[],
  ) => (
    <label>
      {label}
      <select
        aria-label={label}
        value={text(key)}
        onChange={(event) => parameter(key, event.target.value)}
      >
        <option value="">请选择</option>
        {resources.map((item) => (
          <option
            key={item.id}
            value={item.id}
          >
            {item.name}
          </option>
        ))}
      </select>
    </label>
  );

  return (
    <section
      className={`workflow-editor${inspectorOpen || sidePanel ? " inspector-open" : ""}`}
      aria-label="工作流画布编辑器"
    >
      <details className="workflow-canvas-options"><summary><Settings2 size={13} />画布选项</summary><fieldset disabled={readOnly}><div className="workflow-toolbar">
        <label>执行方式<select aria-label="工作流执行方式" value={definition.execution || "readonly"}
          onChange={(event) => onChange({ ...definition, execution: event.target.value as "readonly" | "work" })}>
          <option value="readonly" disabled={definition.nodes.some((node) => ["approval", "code"].includes(node.kind))}>只读流程</option>
          <option value="work">持久任务（支持审批与代码）</option>
        </select></label>
        <label>
          新增节点类型
          <select
            aria-label="新增节点类型"
            value={addKind}
            onChange={(event) => setAddKind(event.target.value as Kind)}
          >
            {Object.entries(nodeLabels)
              .filter(([kind]) => kind !== "start")
              .filter(([kind]) => definition.execution === "work" || !["approval", "code"].includes(kind))
              .map(([kind, label]) => (
                <option
                  value={kind}
                  key={kind}
                >
                  {label}
                </option>
              ))}
          </select>
        </label>
        <button
          type="button"
          onClick={() => addNode(addKind, false, true)}
        >
          添加节点
        </button>
        <label>
          节点执行预算
          <input
            type="number"
            aria-label="节点执行预算"
            min={2}
            max={100}
            value={definition.node_budget || 30}
            onChange={(event) =>
              onChange({
                ...definition,
                node_budget: Number(event.target.value),
              })
            }
          />
        </label>
      </div></fieldset></details>
      {error && <p role="alert">{error}</p>}
      <div className="workflow-editor-grid">
        <div className="workflow-canvas">
          <ReactFlow<CanvasNode>
            onInit={(instance) => { flow.current = instance; }}
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={(changes) =>
              setNodes((items) => applyNodeChanges(changes, items))
            }
            onEdgesChange={(changes) =>
              setEdges((items) => applyEdgeChanges(changes, items))
            }
            onNodeClick={(_, node) => { setSelected(node.id); setInspectorOpen(true); onSelect?.(node.id); }}
            onNodeDragStop={(_, node) =>
              !readOnly && onLayout({ ...layout, [node.id]: node.position })
            }
            onConnect={connect}
            nodesDraggable={!readOnly}
            nodesConnectable={!readOnly}
            edgesReconnectable={!readOnly}
            onDelete={({ nodes: deletedNodes, edges: deletedEdges }) =>
              remove(
                new Set(deletedNodes.map((node) => node.id)),
                new Set(deletedEdges.map((edge) => edge.id)),
              )
            }
            nodeExtent={[
              [-9000, -9000],
              [9000, 9000],
            ]}
            fitView
            fitViewOptions={{ padding: .25, maxZoom: 1 }}
            ariaLabelConfig={{
              "node.a11yDescription.default":
                "按回车选择节点，方向键移动，删除键删除。",
              "node.a11yDescription.keyboardDisabled": "按回车选择节点。",
              "node.a11yDescription.ariaLiveMessage": ({ x, y }) =>
                `节点已移动至 ${x}，${y}`,
              "edge.a11yDescription.default": "选择连线后按删除键移除。",
              "controls.ariaLabel": "画布控制",
              "controls.zoomIn.ariaLabel": "放大",
              "controls.zoomOut.ariaLabel": "缩小",
              "controls.fitView.ariaLabel": "适应画布",
              "controls.interactive.ariaLabel": "切换交互",
              "minimap.ariaLabel": "流程缩略图",
              "handle.ariaLabel": "节点连接点",
            }}
          >
            <Background />
            <Controls showInteractive={false} />
            <Panel position="top-left"><button type="button" className="workflow-add-trigger" disabled={readOnly} onClick={() => setPalette(!palette)}><Plus size={15} />添加节点</button>
              {palette && <div className="workflow-palette" role="region" aria-label="节点库"><header><strong>添加节点</strong><button type="button" aria-label="关闭节点库" onClick={() => setPalette(false)}><X size={15}/></button></header>
                <input aria-label="搜索节点" value={nodeQuery} onChange={event => setNodeQuery(event.target.value)} placeholder="搜索名称或用途"/>
                {nodeGroups.map(group => {
                  const kinds = group.kinds.filter(kind => `${kind} ${kind === "template" ? "模板组合 报告" : nodeLabels[kind as Kind]} ${group.name}`.toLowerCase().includes(nodeQuery.trim().toLowerCase()));
                  return kinds.length > 0 && <section key={group.name}><h4>{group.name}</h4>{kinds.map(kind => <button type="button" key={kind}
                    onClick={() => addNode(kind === "template" ? "transform" : kind as Kind, false, false, kind === "template" ? "template" : undefined)}>
                    <Workflow size={14}/>{kind === "template" ? "模板组合" : nodeLabels[kind as Kind]}<Plus size={12}/></button>)}</section>;
                })}
                {!nodeGroups.some(group => group.kinds.some(kind => `${kind} ${kind === "template" ? "模板组合 报告" : nodeLabels[kind as Kind]} ${group.name}`.toLowerCase().includes(nodeQuery.trim().toLowerCase()))) && <p>没有匹配的节点。<button type="button" onClick={() => setNodeQuery("")}>清除搜索</button></p>}
              </div>}
            </Panel>
            {current && <Panel position="top-right"><div className="workflow-node-actions"><span>{current.name || nodeLabels[current.kind]}</span>
              <button type="button" title="单节点测试" aria-label="单节点测试" disabled={!onTestNode} onClick={() => { setInspectorOpen(true); onTestNode?.(current.id); }}><Play size={14}/>测试节点</button>
              <button type="button" title="复制节点" aria-label="复制节点" disabled={readOnly || ["start", "end"].includes(current.kind)} onClick={() => addNode(current.kind, true)}><Copy size={14}/></button>
              <button type="button" title="删除节点" aria-label="删除节点" disabled={readOnly || current.kind === "start" || current.kind === "end" && definition.nodes.filter((node) => node.kind === "end").length === 1} onClick={() => remove(new Set([current.id]))}><Trash2 size={14}/></button>
            </div></Panel>}
          </ReactFlow>
        </div>
        <aside className="workflow-node-settings"><header className="workflow-inspector-mobile"><span>配置与运行</span><button type="button" aria-label="关闭配置面板" onClick={() => { setInspectorOpen(false); onClosePanel?.(); }}><X size={16}/></button></header>{sidePanel || <fieldset disabled={readOnly}>
          {current ? (
            <>
              <div className="workflow-node-config-heading"><h3>节点配置</h3><button type="button" disabled={!onTestNode} onClick={() => onTestNode?.(current.id)}><Play size={13}/>测试此节点</button></div>
              <p>节点标识：{current.id}</p>
              <label>
                节点名称
                <input
                  aria-label="节点名称"
                  maxLength={80}
                  value={current.name || nodeLabels[current.kind]}
                  onChange={(event) =>
                    change({ ...current, name: event.target.value })
                  }
                />
              </label>
              <label>
                节点类型
                <select
                  aria-label="节点类型"
                  value={current.kind}
                  onChange={(event) => {
                    const kind = event.target.value as Kind;
                    change({
                      ...current,
                      kind,
                      parameters:
                        kind === "transform"
                          ? { operation: "text" }
                          : kind === "condition"
                            ? { operator: "contains", value: "" }
                            : kind === "loop"
                              ? { operator: "not_empty", max_iterations: 5 }
                              : kind === "approval" ? { prompt: "请确认是否继续" }
                              : kind === "code" ? { code: "import sys\nprint(sys.stdin.read())", timeout: 30 }
                              : {},
                    });
                  }}
                >
                  {Object.entries(nodeLabels).filter(([kind]) => definition.execution === "work" || !["approval", "code"].includes(kind)).map(([kind, label]) => (
                    <option
                      value={kind}
                      key={kind}
                    >
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              {current.kind === "approval" && <label>审批说明<textarea aria-label="流程审批说明" rows={4}
                maxLength={2000} value={text("prompt")} onChange={(event) => parameter("prompt", event.target.value)} /></label>}
              {current.kind === "code" && <>
                <label>Python代码<textarea aria-label="流程Python代码" rows={10} maxLength={8000}
                  value={text("code")} onChange={(event) => parameter("code", event.target.value)} /></label>
                <label>执行时间上限（秒）<input aria-label="代码执行时间上限" type="number" min={1} max={60}
                  value={Number(current.parameters.timeout || 30)} onChange={(event) => parameter("timeout", Number(event.target.value))} /></label>
                <small>从标准输入读取节点文本，标准输出和错误输出合并为结果。每次执行前需本人批准；脚本只在任务环境运行。</small>
              </>}
              {current.kind === "end" && (
                <label className="workflow-output-mode">
                  <input
                    type="checkbox"
                    checked={current.parameters.use_previous === true}
                    onChange={(event) =>
                      parameter("use_previous", event.target.checked)
                    }
                  />
                  输出当前分支结果
                </label>
              )}
              {current.kind !== "start" &&
                !(
                  current.kind === "end" && current.parameters.use_previous
                ) && (
                  <label>
                    输入来源
                    <select
                      aria-label="节点输入来源"
                      value={current.input}
                      onChange={(event) =>
                        change({ ...current, input: event.target.value })
                      }
                    >
                      <option value="input">流程输入</option>
                      {legacyStart && <option value={legacyStart.id}>开始输出（兼容，建议流程输入）</option>}
                      {current.input !== "input" && !legacyStart && !available.some(item => item.id === current.input) && <option value={current.input}>不可用来源：{current.input}（请修复）</option>}
                      {available
                        .map((item) => (
                          <option
                            value={item.id}
                            key={item.id}
                          >
                            {item.name || nodeLabels[item.kind]}（{item.id}）
                          </option>
                        ))}
                    </select>
                    <small>只显示所有执行路径必经的上游输出。JSON 字段可添加“文本转换 → JSON 字段提取”。</small>
                  </label>
                )}
              {current.kind === "llm" && (
                <>
                  {resourceSelect("model_id", "节点模型（可选）", models)}
                  <label><input type="checkbox" checked={current.parameters.model_routing === true}
                    onChange={event => parameter("model_routing", event.target.checked)}/>使用已确认的模型路由</label>
                  <label>
                    节点指令
                    <textarea
                      aria-label="节点指令"
                      maxLength={12000}
                      value={text("instructions")}
                      onChange={(event) =>
                        parameter("instructions", event.target.value)
                      }
                    />
                  </label>
                </>
              )}
              {current.kind === "agent" && (
                <>
                  {resourceSelect("agent_id", "选择节点智能体", agents)}
                  <small>使用所选智能体的模型、指令和已授权只读能力。</small>
                </>
              )}
              {current.kind === "rag" &&
                resourceSelect("knowledge_id", "选择节点知识库", knowledge)}
              {(current.kind === "subworkflow" ||
                current.kind === "iteration" ||
                current.kind === "loop") && (
                <>
                  {resourceSelect(
                    "version_id",
                    "选择已发布子流程",
                    workflowVersions,
                  )}
                  {current.kind === "iteration" && (
                    <label>
                      最多迭代项数
                      <input
                        aria-label="最多迭代项数"
                        type="number"
                        min={1}
                        max={10}
                        value={Number(current.parameters.max_items || 10)}
                        onChange={(e) =>
                          parameter("max_items", Number(e.target.value))
                        }
                      />
                    </label>
                  )}
                  {current.kind === "loop" && (
                    <label>
                      最多循环轮数
                      <input
                        aria-label="最多循环轮数"
                        type="number"
                        min={1}
                        max={10}
                        value={Number(current.parameters.max_iterations || 5)}
                        onChange={(event) =>
                          parameter(
                            "max_iterations",
                            Number(event.target.value),
                          )
                        }
                      />
                    </label>
                  )}
                  <small>
                    只引用本人已发布的只读版本。
                    {current.kind === "loop"
                      ? "每轮传递上轮输出，执行后检查停止条件。达到上限仍未满足时报告未达成，不自动继续。"
                      : current.kind === "iteration"
                        ? "迭代输入是JSON数组，逐项执行；结果保持输入顺序。"
                        : "共享父流程预算和授权。"}
                  </small>
                </>
              )}
              {current.kind === "parallel" && (
                <label>
                  并行子流程（选择 2–4 个）
                  <select
                    aria-label="并行子流程"
                    multiple
                    value={
                      Array.isArray(current.parameters.version_ids)
                        ? (current.parameters.version_ids as string[])
                        : []
                    }
                    onChange={(e) =>
                      parameter(
                        "version_ids",
                        Array.from(
                          e.target.selectedOptions,
                          (item) => item.value,
                        ),
                      )
                    }
                  >
                    {workflowVersions.map((item) => (
                      <option
                        key={item.id}
                        value={item.id}
                      >
                        {item.name}
                      </option>
                    ))}
                  </select>
                  <small>
                    最多两路同时执行；嵌套并行在已有分支中依次执行，结果按上方列表中所选项目的顺序汇总。
                  </small>
                </label>
              )}
              {current.kind === "http" && (
                <label>
                  读取地址
                  <input
                    aria-label="网页读取地址"
                    type="url"
                    value={text("url")}
                    placeholder="https://example.com/data"
                    onChange={(event) => parameter("url", event.target.value)}
                  />
                  <small>仅公网 HTTPS GET，不发送凭证或跟随跳转。</small>
                </label>
              )}
              {(current.kind === "condition" || current.kind === "loop") && (
                <>
                  <label>
                    {current.kind === "loop" ? "停止条件" : "判断方式"}
                    <select
                      aria-label="条件判断方式"
                      value={text("operator")}
                      onChange={(event) =>
                        parameter("operator", event.target.value)
                      }
                    >
                      {Object.entries({
                        equals: "等于",
                        not_equals: "不等于",
                        contains: "包含",
                        not_empty: "非空",
                        starts_with: "开头是",
                      }).map(([value, label]) => (
                        <option
                          value={value}
                          key={value}
                        >
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {text("operator") !== "not_empty" && (
                    <label>
                      比较内容
                      <input
                        aria-label="条件比较内容"
                        value={text("value")}
                        onChange={(event) =>
                          parameter("value", event.target.value)
                        }
                      />
                    </label>
                  )}
                </>
              )}
              {current.kind === "transform" && (
                <>
                  <label>
                    转换方式
                    <select
                      aria-label="文本转换方式"
                      value={text("operation")}
                      onChange={(event) =>
                        parameter("operation", event.target.value)
                      }
                    >
                      {Object.entries({
                        text: "保持文本",
                        upper: "转为大写",
                        lower: "转为小写",
                        trim: "移除首尾空白",
                        replace: "替换文本",
                        json_get: "读取 JSON 字段",
                        template: "模板组合",
                      }).map(([value, label]) => (
                        <option
                          value={value}
                          key={value}
                        >
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {text("operation") === "replace" && (
                    <>
                      <label>
                        原文本
                        <input
                          value={text("old")}
                          onChange={(event) =>
                            parameter("old", event.target.value)
                          }
                        />
                      </label>
                      <label>
                        新文本
                        <input
                          value={text("new")}
                          onChange={(event) =>
                            parameter("new", event.target.value)
                          }
                        />
                      </label>
                    </>
                  )}
                  {text("operation") === "template" && <>
                    <label>模板内容<textarea aria-label="节点模板内容" maxLength={12000} value={text("template")} onChange={event => parameter("template", event.target.value)}/></label>
                    <div className="workflow-variable-picker"><label>插入上游变量<select aria-label="模板变量来源" value={variable} onChange={event => setVariable(event.target.value)}>
                      <option value="input">流程原始输入</option>{available.map(item => <option key={item.id} value={item.id}>{item.name || nodeLabels[item.kind]}（{item.id}）</option>)}
                    </select></label><button type="button" disabled={text("template").length + variable.length + 4 > 12000 || variable !== "input" && !available.some(item => item.id === variable)} onClick={() => parameter("template", text("template") + `{{${variable}}}`)}>插入变量</button></div>
                    <small>{"{{input}} 始终是流程原始输入。仅支持 {{节点ID}}，不支持表达式或字段路径；JSON 字段请先提取。"}</small>
                  </>}
                  {text("operation") === "json_get" && <>
                    <label>
                      字段路径
                      <input
                        aria-label="JSON 字段路径"
                        placeholder="customer.name 或 items.0.title"
                        value={text("path")}
                        onChange={(event) =>
                          parameter("path", event.target.value)
                        }
                      />
                    </label><small>用点分隔对象字段，数组使用从 0 开始的索引。缺字段或越界会明确失败。</small>
                    <label>JSON 测试样例<textarea aria-label="JSON 测试样例" maxLength={16000} value={jsonSample} onChange={event => setJsonSample(event.target.value)} placeholder={'{"customer":{"name":"小明"}}'}/></label>
                    <button type="button" disabled={!onTestNode || !jsonSample} onClick={() => onTestNode?.(current.id, jsonSample)}>用此 JSON 测试节点</button>
                    <small>填入单节点测试区后，点击运行查看真实提取结果。</small>
                  </>}
                </>
              )}
              {current.kind === "mcp" && (
                <>
                  {resourceSelect("server_id", "选择 MCP 服务", servers)}
                  <label>
                    选择工具
                    <select
                      aria-label="选择节点 MCP 工具"
                      value={text("tool")}
                      onChange={(event) =>
                        parameter("tool", event.target.value)
                      }
                    >
                      <option value="">请选择已启用的只读工具</option>
                      {servers
                        .find((server) => server.id === text("server_id"))
                        ?.tools?.filter(
                          (tool) => tool.enabled && tool.read_only,
                        )
                        .map((tool) => (
                          <option
                            key={tool.name}
                            value={tool.name}
                          >
                            {tool.name}
                          </option>
                        ))}
                    </select>
                  </label>
                  <label>
                    工具参数（JSON）
                    <textarea
                      aria-label="节点工具参数"
                      value={argumentsText}
                      maxLength={16000}
                      onChange={(event) => {
                        setArgumentsText(event.target.value);
                        try {
                          const value = JSON.parse(event.target.value);
                          if (
                            !value ||
                            Array.isArray(value) ||
                            typeof value !== "object"
                          )
                            throw new Error();
                          parameter("arguments", value);
                          setArgumentError("");
                          onInvalid(false);
                        } catch {
                          setArgumentError("请输入有效的参数对象");
                          onInvalid(true);
                        }
                      }}
                    />
                  </label>
                  <small>
                    例如 {`{"query":{"ref":"input"}}`}{" "}
                    引用流程输入。需先在能力中心绑定服务。
                  </small>
                  <div className="workflow-variable-picker"><label>参数名称<input aria-label="工具引用参数名" maxLength={100} value={argumentName} onChange={event => setArgumentName(event.target.value)}/></label>
                    <label>参数变量来源<select aria-label="工具参数变量来源" value={variable} onChange={event => setVariable(event.target.value)}><option value="input">流程输入</option>{available.map(item => <option key={item.id} value={item.id}>{item.name || nodeLabels[item.kind]}（{item.id}）</option>)}</select></label>
                    <button type="button" disabled={!argumentName.trim() || variable !== "input" && !available.some(item => item.id === variable)} onClick={() => {
                      const argumentsValue = { ...(current.parameters.arguments as Record<string, unknown> || {}), [argumentName.trim()]: { ref: variable } };
                      parameter("arguments", argumentsValue); setArgumentsText(JSON.stringify(argumentsValue, null, 2)); setArgumentError(""); onInvalid(false);
                    }}>添加参数引用</button>
                  </div>
                  {argumentError && <p role="alert">{argumentError}</p>}
                </>
              )}
              <button
                type="button"
                onClick={() => remove(new Set([current.id]))}
              >
                删除此节点
              </button>
            </>
          ) : (
            <p>请选择一个节点进行配置。</p>
          )}</fieldset>}
        </aside>
      </div>
    </section>
  );
}
