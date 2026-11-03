"use client";

import type { MemoryScope, ScopeOptions } from "./memory-scope-values";
export function MemoryScopeEditor({
  value,
  options,
  onChange,
}: {
  value: MemoryScope;
  options: ScopeOptions;
  onChange: (scope: MemoryScope) => void;
}) {
  return (
    <div className="memory-scope-editor">
      <label>
        记忆使用范围
        <select
          aria-label="记忆使用范围"
          value={value.kind}
          onChange={(e) =>
            onChange({
              ...value,
              kind: e.target.value as MemoryScope["kind"],
              id: "",
            })
          }
        >
          <option value="global">本人全局</option>
          <option value="project">仅指定项目</option>
          <option value="agent">仅指定智能体</option>
        </select>
      </label>
      {value.kind !== "global" && (
        <label>
          {value.kind === "project" ? "所属项目" : "所属智能体"}
          <select
            aria-label={value.kind === "project" ? "所属项目" : "所属智能体"}
            required
            value={value.id}
            onChange={(e) => onChange({ ...value, id: e.target.value })}
          >
            <option value="">请选择</option>
            {(value.kind === "project" ? options.projects : options.agents).map(
              (item) => (
                <option
                  key={item.id}
                  value={item.id}
                >
                  {item.name}
                </option>
              ),
            )}
          </select>
        </label>
      )}
      <label>
        到期时间（可选，按本机时区）
        <input
          type="datetime-local"
          value={value.expires}
          onChange={(e) => onChange({ ...value, expires: e.target.value })}
        />
      </label>
      <p>
        范围只限制记忆召回，不共享项目资料或增加智能体权限。到期后自动停止召回。
      </p>
    </div>
  );
}
