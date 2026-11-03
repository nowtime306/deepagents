"use client";
import { useRunSnapshot, useLiveSnapshot } from "./run-snapshot";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import "./agent-team.css";

export type TeamMember = { name: string; version_id: string; goal: string };
type Choice = {
  id: string;
  agent_id: string;
  name: string;
  version: number;
  model_id: string;
  model_name?: string | null;
};

export function AgentTeamEditor({
  value,
  onChange,
  agentId,
}: {
  value: TeamMember[];
  onChange: (members: TeamMember[]) => void;
  agentId?: string;
}) {
  const [choices, setChoices] = useState<Choice[]>([]);
  const [error, setError] = useState("");
  const request = useCallback(
    () => api<{ items: Choice[] }>("/agents/team-options"),
    [],
  );
  const load = async () => {
    try {
      setChoices((await request()).items);
      setError("");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  useEffect(() => {
    let current = true;
    void request()
      .then((result) => {
        if (current) {
          setChoices(result.items);
          setError("");
        }
      })
      .catch((reason) => {
        if (current) setError(reason.message);
      });
    return () => {
      current = false;
    };
  }, [request]);
  const options = choices.filter((item) => item.agent_id !== agentId);
  const update = (index: number, patch: Partial<TeamMember>) =>
    onChange(
      value.map((member, position) =>
        position === index ? { ...member, ...patch } : member,
      ),
    );
  return (
    <fieldset className="agent-team-editor">
      <legend>团队协作</legend>
      <p>
        选择已发布的独立智能体作为只读成员。最多六个角色、两个并行子任务，每轮最多委派八次；共用本轮预算。
      </p>
      <small>
        成员的知识、技能、工具、记忆和联网须包含在上方主智能体授权中。成员不能继续委派或执行写操作；首版团队暂不混用工作流。
      </small>
      {error && (
        <p role="alert">
          {error}{" "}
          <button
            type="button"
            onClick={() => void load()}
          >
            重新加载版本
          </button>
        </p>
      )}
      {!options.length && !error && (
        <p>
          暂无可选版本。先保存并发布一个不含团队、也未绑定工作流的成员智能体，再重新加载。
        </p>
      )}
      {value.map((member, index) => (
        <div
          key={index}
          className="agent-team-member"
        >
          <label>
            角色名 {index + 1}
            <input
              required
              pattern={"[A-Za-z\\u4e00-\\u9fff][A-Za-z0-9_\\u4e00-\\u9fff\\-]*"}
              maxLength={40}
              aria-label={`成员 ${index + 1} 角色名`}
              value={member.name}
              onChange={(event) => update(index, { name: event.target.value })}
            />
          </label>
          <label>
            已发布成员版本
            <select
              required
              aria-label={`成员 ${index + 1} 版本`}
              value={member.version_id}
              onChange={(event) =>
                update(index, { version_id: event.target.value })
              }
            >
              <option value="">请选择已发布版本</option>
              {member.version_id &&
                !options.some((item) => item.id === member.version_id) && (
                  <option value={member.version_id}>
                    当前版本已不可用，请更换或移除
                  </option>
                )}
              {options.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                >
                  {item.name} · v{item.version} ·{" "}
                  {item.model_name || "模型不可用"}
                </option>
              ))}
            </select>
          </label>
          <label>
            委派目标
            <textarea
              required
              maxLength={1500}
              aria-label={`成员 ${index + 1} 目标`}
              value={member.goal}
              onChange={(event) => update(index, { goal: event.target.value })}
              placeholder="例如：收集可引用的证据，说明无法确认的内容。"
            />
          </label>
          <button
            type="button"
            aria-label={`移除成员 ${index + 1}`}
            onClick={() =>
              onChange(value.filter((_, position) => position !== index))
            }
          >
            移除成员
          </button>
        </div>
      ))}
      <div className="agent-team-actions">
        <button
          type="button"
          disabled={value.length >= 6 || !options.length}
          onClick={() => {
            let serial = 1;
            while (value.some((item) => item.name === `成员_${serial}`))
              serial++;
            onChange([
              ...value,
              { name: `成员_${serial}`, version_id: "", goal: "" },
            ]);
          }}
        >
          添加团队成员
        </button>
        <button
          type="button"
          onClick={() => void load()}
        >
          重新加载版本
        </button>
      </div>
    </fieldset>
  );
}

type TeamCall = {
  id: string;
  role_name: string;
  model_id: string;
  model_name?: string | null;
  status: string;
  cancel_requested: boolean;
  error_code: string | null;
  calls: number;
  reported_calls: number;
  input_tokens: number | null;
  output_tokens: number | null;
};
const statuses: Record<string, string> = {
  RUNNING: "执行中",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  CANCELLED: "已取消",
  UNKNOWN: "结果未知",
};

export function TeamProgress({
  id,
  mode,
  active,
}: {
  id: string;
  mode: "ask" | "work";
  active: boolean;
}) {
  const pushed = useRunSnapshot<{ items: TeamCall[] }>(id, mode, "team");
  const liveSnapshot = useLiveSnapshot(id, mode);
  const [loaded, setItems] = useState<TeamCall[]>([]);
  const items = pushed?.items ?? loaded;
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const path = mode === "work" ? `/work-runs/${id}/team` : `/runs/${id}/team`;
  const request = useCallback(() => api<{ items: TeamCall[] }>(path), [path]);
  const load = async () => {
    try {
      setItems((await request()).items);
      setError("");
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
  useEffect(() => {
    if (liveSnapshot) return;
    let current = true;
    const poll = () =>
      void request()
        .then((result) => {
          if (current) {
            setItems(result.items);
            setError("");
          }
        })
        .catch((reason) => {
          if (current) setError(reason.message);
        });
    poll();
    const interval = active && mode === "work" ? window.setInterval(poll, 2000) : undefined;
    return () => {
      current = false;
      window.clearInterval(interval);
    };
  }, [request, active, mode, liveSnapshot]);
  return (
    <section
      className="diagnostic-team"
      aria-label="团队执行"
    >
      <h4>团队执行</h4>
      {error && (
        <p role="alert">
          {error} <button onClick={() => void load()}>重试</button>
        </p>
      )}
      {!items.length && <p>尚未委派成员；主智能体会按任务决定是否委派。</p>}
      {items.map((item) => (
        <article key={item.id}>
          <strong>
            {item.role_name} · {statuses[item.status] || item.status}
          </strong>
          <p>
            {item.model_name || "模型已移除"} · {item.calls} 次模型调用 · 输入{" "}
            {item.input_tokens?.toLocaleString("zh-CN") ?? "未报告"} / 输出{" "}
            {item.output_tokens?.toLocaleString("zh-CN") ?? "未报告"}
          </p>
          {item.reported_calls < item.calls && (
            <small>
              包含未取得完整用量的调用，不能把已报告部分当作完整消耗。
            </small>
          )}
          {item.error_code && (
            <small>
              {item.status === "UNKNOWN"
                ? "执行证据不完整，未自动重放。"
                : item.status === "CANCELLED"
                  ? "成员已取消，已发生的用量保留。"
                  : "成员失败，请核对版本、授权和运行记录。"}
            </small>
          )}
          {item.status === "RUNNING" && active && (
            <button
              disabled={!!busy || item.cancel_requested}
              onClick={async () => {
                setBusy(item.id);
                try {
                  await api(`${path}/${item.id}/cancel`, { method: "POST" });
                  await load();
                } catch (reason) {
                  setError((reason as Error).message);
                } finally {
                  setBusy("");
                }
              }}
            >
              {item.cancel_requested ? "已请求取消" : "取消此成员"}
            </button>
          )}
        </article>
      ))}
      <small>
        取消在下一执行边界生效；已发出的模型请求和已产生的用量不能撤回。
      </small>
    </section>
  );
}
