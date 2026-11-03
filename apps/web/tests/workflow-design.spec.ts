import { expect, test } from "@playwright/test";
import { arrangeWorkflow, nodeReferences, templateReferences, upstreamSources, workflowTemplates } from "../src/features/ask/workflow-design";

test("branch-local outputs are unavailable after a merge; root and common predecessors remain", () => {
  const definition = workflowTemplates.find(item => item.id === "condition")!.definition();
  definition.nodes.push({ id: "merge", kind: "end", input: "input", parameters: {} });
  definition.edges = definition.edges.filter(item => !["yes", "no"].includes(item.target));
  definition.edges.push({ source: "matched", target: "merge" }, { source: "fallback", target: "merge" });
  expect(upstreamSources(definition, "merge").map(item => item.id)).toEqual(["rule"]);
  expect(upstreamSources(definition, "matched").map(item => item.id)).toEqual(["rule"]);
  expect(upstreamSources(definition, "rule")).toEqual([]);
});

test("linear sources do not include self or future nodes, irrespective of array order", () => {
  const definition = workflowTemplates.find(item => item.id === "report")!.definition();
  definition.nodes.reverse();
  expect(new Set(upstreamSources(definition, "report").map(item => item.id))).toEqual(new Set(["clean", "uppercase"]));
  expect(upstreamSources(definition, "clean")).toEqual([]);
});

test("cyclic and disconnected drafts do not hang variable selection or mutate layout", () => {
  const definition = workflowTemplates.find(item => item.id === "report")!.definition();
  definition.edges.push({ source: "report", target: "clean" });
  const before = JSON.stringify(definition);
  expect(upstreamSources(definition, "report")).toEqual([]);
  expect(() => arrangeWorkflow(definition)).toThrow("包含环");
  expect(JSON.stringify(definition)).toBe(before);
});

test("layout separates conditional branches and stays within persisted position limits", () => {
  const definition = workflowTemplates.find(item => item.id === "condition")!.definition();
  const before = JSON.stringify(definition), layout = arrangeWorkflow(definition);
  expect(layout.matched.x).toBe(layout.fallback.x);
  expect(layout.matched.y).not.toBe(layout.fallback.y);
  expect(layout.rule.x).toBeGreaterThan(layout.start.x);
  expect(Object.values(layout).every(item => Math.abs(item.x) <= 10000 && Math.abs(item.y) <= 10000)).toBe(true);
  expect(JSON.stringify(definition)).toBe(before);
});

test("template and MCP test references include explicit root input when the main input differs", () => {
  const report = workflowTemplates.find(item => item.id === "report")!.definition().nodes.find(item => item.id === "report")!;
  expect(nodeReferences(report)).toEqual(["uppercase", "input", "clean"]);
  expect(templateReferences("{{input}} {{input}} {{clean}} {{{bad}}} {{bad.path}} {{ input }}")).toEqual(["input", "clean"]);
  expect(nodeReferences({ id: "tool", kind: "mcp", input: "upstream", parameters: { arguments: { a: { ref: "context" }, b: { ref: "upstream" }, c: "input" } } })).toEqual(["upstream", "context"]);
});

test("five templates are independent editable graphs without baked-in user resources", () => {
  expect(workflowTemplates.map(item => item.id)).toEqual(["blank", "llm", "json", "condition", "report"]);
  for (const template of workflowTemplates) {
    const definition = template.definition(), another = template.definition();
    definition.nodes[0].name = "local edit";
    expect(another.nodes[0].name).toBeUndefined();
    expect(another.nodes.filter(item => item.kind === "start")).toHaveLength(1);
    expect(another.nodes.some(item => item.kind === "end")).toBe(true);
    expect(JSON.stringify(another)).not.toMatch(/knowledge_id|server_id|agent_id|api_key/);
    expect(arrangeWorkflow(another)).toHaveProperty("start");
  }
});
