"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Step = { op: string; url?: string; target?: { role: string; name: string; ctx?: string }; parameter?: string; blocked: string[] };
type Path = { steps: Step[]; parameters: { name: string }[] };
const labels: Record<string, string> = { navigate: "打开页面", click: "点击", fill: "填写", select: "选择" };

export function BrowserSkillApproval({ args, approvalId, checkKey, checked }: {
  args: Record<string, unknown>; approvalId: string; checkKey: string; checked: (key: string, valid: boolean) => void;
}) {
  const [path, setPath] = useState<Path | null>(null), [error, setError] = useState("");
  const encoded = JSON.stringify(args);
  useEffect(() => {
    const controller = new AbortController();
    checked(checkKey, false);
    async function load() {
      const request = JSON.parse(encoded) as Record<string, unknown>;
      if (typeof request.skill_id !== "string" || !/^[a-f0-9-]{36}$/i.test(request.skill_id) || request.execution_boundary !== "local_authenticated_browser") throw new Error("审批未指定有效的本机浏览器技能。");
      const skill = await api<{ name: string; version: number; enabled: boolean; files: Record<string, string> }>(`/browser-skills/approvals/${approvalId}/skills/${request.skill_id}`, { signal: controller.signal });
      if (!skill.enabled || skill.version !== request.version || skill.name !== request.skill_name) throw new Error("本次固定技能版本不符、已停用或不可访问，请拒绝此次操作。");
      const value = JSON.parse(skill.files["trajectory.json"]) as Path;
      const parameters = request.parameters;
      if (!Array.isArray(value.steps) || !value.steps.length || value.steps.length !== request.step_count || !Array.isArray(value.parameters) || !parameters || typeof parameters !== "object" || Array.isArray(parameters)) throw new Error("无法验证完整录制路径与参数。");
      const names = value.parameters.map(item => item.name);
      if (names.length !== Object.keys(parameters).length || names.some(name => typeof (parameters as Record<string, unknown>)[name] !== "string" || !(parameters as Record<string, string>)[name])) throw new Error("运行参数不完整，不能批准。");
      if (value.steps.some(step => !labels[step.op] || !Array.isArray(step.blocked) || step.blocked.length || (step.op === "navigate" ? typeof step.url !== "string" : !step.target?.role || !step.target.name) || (["fill", "select"].includes(step.op) && !names.includes(step.parameter || "")))) throw new Error("路径包含无法验证的步骤，请拒绝此次操作。");
      if (!controller.signal.aborted) { setPath(value); checked(checkKey, true); }
    }
    void load().catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "路径验证失败，请拒绝此次操作。"); });
    return () => { controller.abort(); checked(checkKey, false); };
  }, [encoded, approvalId, checkKey, checked]);
  return <section aria-label="浏览器完整路径审批">
    <p><strong>本次批准整个录制路径，共 {String(args.step_count)} 步。</strong> 使用本机已登录的专用浏览器，可能以该账号填写、点击和提交数据。此浏览器没有网络硬隔离，域名只约束顶层导航。</p>
    {error ? <p role="alert" className="platform-error">{error}</p> : !path ? <p>正在读取并核对你发布的技能路径…</p> : <ol>{path.steps.map((step, index) => <li key={index}>
      <strong>{labels[step.op]}</strong> · {step.url || [step.target?.role, step.target?.name, step.target?.ctx].filter(Boolean).join(" · ")}
      {step.parameter && <pre className="work-output">{step.parameter}: {String((args.parameters as Record<string, string>)[step.parameter])}</pre>}
    </li>)}</ol>}
    <p>仅本次审批有效；参数或技能版本变化后需重新审批。中途结果未知不会自动重放。</p>
  </section>;
}
