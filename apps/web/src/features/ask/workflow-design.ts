import type { WorkflowDefinition, WorkflowLayout, WorkflowNode } from "./workflow-editor";

const node = (id: string, kind: WorkflowNode["kind"], input = "input", parameters: Record<string, unknown> = {}): WorkflowNode => ({ id, kind, input, parameters });
const edge = (source: string, target: string) => ({ source, target });
const graph = (nodes: WorkflowNode[], edges: WorkflowDefinition["edges"]): WorkflowDefinition => ({
  schema_version: 1, execution: "readonly", input_schema: { type: "string" }, output_schema: { type: "string" }, node_budget: 30, nodes, edges,
});

export const workflowTemplates = [
  { id: "blank", name: "空白流程", description: "从输入到输出，逐步添加自己的逻辑。", resource: "不调用模型", example: "你好", expected: "你好",
    definition: () => graph([node("start", "start"), node("end", "end")], [edge("start", "end")]) },
  { id: "llm", name: "AI问答", description: "接收问题，交给默认模型生成回答。", resource: "需要已配置的聊天模型", example: "用一句话解释工作流", expected: "由所选模型生成，计入用量",
    definition: () => graph([node("start", "start"), node("llm", "llm"), node("end", "end", "llm")], [edge("start", "llm"), edge("llm", "end")]) },
  { id: "json", name: "JSON 字段提取", description: "从 JSON 文本中取出指定字段。", resource: "不调用模型", example: '{"customer":{"name":"小明"}}', expected: "小明",
    definition: () => graph([node("start", "start"), node("extract", "transform", "input", { operation: "json_get", path: "customer.name" }), node("end", "end", "extract")], [edge("start", "extract"), edge("extract", "end")]) },
  { id: "condition", name: "关键词分流", description: "命中 VIP 时转大写，其余转小写。", resource: "不调用模型", example: "VIP customer", expected: "VIP CUSTOMER",
    definition: () => graph([node("start", "start"), node("rule", "condition", "input", { operator: "contains", value: "VIP" }), node("matched", "transform", "input", { operation: "upper" }), node("fallback", "transform", "input", { operation: "lower" }), node("yes", "end", "matched"), node("no", "end", "fallback")],
      [edge("start", "rule"), { source: "rule", target: "matched", case: "true" }, { source: "rule", target: "fallback", case: "false" }, edge("matched", "yes"), edge("fallback", "no")]) },
  { id: "report", name: "模板报告", description: "把原文与两个上游处理结果组合成报告。", resource: "不调用模型", example: " hi ", expected: "原文： hi \n清理：hi\n大写：HI",
    definition: () => graph([node("start", "start"), node("clean", "transform", "input", { operation: "trim" }), node("uppercase", "transform", "clean", { operation: "upper" }), node("report", "transform", "uppercase", { operation: "template", template: "原文：{{input}}\n清理：{{clean}}\n大写：{{uppercase}}" }), node("end", "end", "report")],
      [edge("start", "clean"), edge("clean", "uppercase"), edge("uppercase", "report"), edge("report", "end")]) },
];

/** Match the deliberately small server template grammar; never evaluate expressions. */
export function templateReferences(text: string): string[] {
  return [...new Set([...text.matchAll(/\{\{([A-Za-z][A-Za-z0-9_-]{0,39})\}\}/g)]
    .filter(match => text[match.index! - 1] !== "{" && text[match.index! + match[0].length] !== "}").map(match => match[1]))];
}

export function nodeReferences(current: WorkflowNode): string[] {
  const tools = current.kind === "mcp" ? Object.values(current.parameters.arguments || {}).flatMap(value =>
    value && typeof value === "object" && !Array.isArray(value) && Object.keys(value).length === 1 && "ref" in value && typeof value.ref === "string" ? [value.ref] : []) : [];
  const template = current.kind === "transform" && current.parameters.operation === "template" ? templateReferences(String(current.parameters.template || "")) : [];
  return [...new Set([current.input, ...tools, ...template])];
}

/** A value is selectable only when its node executes on every path to the target. */
export function upstreamSources(definition: WorkflowDefinition, identifier: string): WorkflowNode[] {
  const ids = new Set(definition.nodes.map(item => item.id));
  const start = definition.nodes.find(item => item.kind === "start");
  if (!start || !ids.has(identifier)) return [];
  const incoming = new Map(definition.nodes.map(item => [item.id, new Set<string>()]));
  for (const item of definition.edges) if (ids.has(item.source) && ids.has(item.target)) incoming.get(item.target)!.add(item.source);
  const resolved = new Map<string, Set<string>>([[start.id, new Set([start.id])]]);
  for (let pass = 0; pass < definition.nodes.length; pass++) for (const item of definition.nodes) {
    const parents = [...incoming.get(item.id)!];
    if (resolved.has(item.id) || !parents.length || parents.some(parent => !resolved.has(parent))) continue;
    resolved.set(item.id, new Set([item.id, ...[...resolved.get(parents[0])!].filter(id => parents.every(parent => resolved.get(parent)!.has(id)))]));
  }
  const available = resolved.get(identifier);
  return definition.nodes.filter(item => item.id !== identifier && item.kind !== "start" && available?.has(item.id));
}

/** Bounded DAG layout. Invalid cyclic drafts fail without changing any coordinates. */
export function arrangeWorkflow(definition: WorkflowDefinition): WorkflowLayout {
  const pending = new Set(definition.nodes.map(item => item.id)), levels = new Map<string, number>();
  if (definition.edges.some(item => !pending.has(item.source) || !pending.has(item.target))) throw new Error("请先修复不存在节点的连线，再整理画布");
  for (let pass = 0; pass < definition.nodes.length && pending.size; pass++) for (const id of pending) {
    const parents = definition.edges.filter(item => item.target === id).map(item => item.source);
    if (parents.some(parent => !levels.has(parent))) continue;
    levels.set(id, parents.length ? Math.max(...parents.map(parent => levels.get(parent)!)) + 1 : 0); pending.delete(id);
  }
  if (pending.size) throw new Error("流程包含环，请先检查并修复连线");
  const rows = new Map<number, number>();
  return Object.fromEntries(definition.nodes.map(item => {
    const level = levels.get(item.id)!, row = rows.get(level) || 0; rows.set(level, row + 1);
    return [item.id, { x: 100 + level * 290, y: 100 + row * 170 }];
  }));
}
