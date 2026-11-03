"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";

type Version = {
  id: string;
  version: number;
  revision: number;
  active: boolean;
  snapshot: { model_id: string; instructions: string };
};

export function AgentVersionPicker({
  agent,
  value,
  change,
  disabled = false,
  model,
}: {
  agent: string;
  value: string;
  change: (id: string) => void;
  disabled?: boolean;
  model?: (id: string) => void;
}) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setVersions([]);
    setError("");
    if (agent)
      api<{ items: Version[] }>(`/agents/${agent}/versions`)
        .then((data) => {
          if (active) setVersions(data.items);
        })
        .catch((reason) => {
          if (active) setError(reason.message);
        });
    return () => {
      active = false;
    };
  }, [agent]);
  useEffect(() => {
    model?.(
      versions.find((item) => item.id === value)?.snapshot.model_id || "",
    );
  }, [versions, value, model]);
  if (!agent) return null;
  return (
    <label>
      智能体版本
      <select
        aria-label="智能体版本"
        value={value}
        disabled={disabled}
        onChange={(event) => change(event.target.value)}
      >
        <option value="">当前草稿（每次运行固定快照）</option>
        {versions
          .filter((item) => item.active || item.id === value)
          .map((item) => (
            <option
              key={item.id}
              value={item.id}
            >
              已发布 v{item.version}
              {item.active ? "" : "（已下线）"}
            </option>
          ))}
      </select>
      {error && <small role="alert">{error}</small>}
    </label>
  );
}

export function AgentVersions({
  agent,
  revision,
  enabled,
}: {
  agent: string;
  revision: number;
  enabled: boolean;
}) {
  const [versions, setVersions] = useState<Version[]>([]);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function load() {
    setVersions(
      (await api<{ items: Version[] }>(`/agents/${agent}/versions`)).items,
    );
  }
  async function action(perform: () => Promise<unknown>) {
    setBusy(true);
    setMessage("");
    try {
      await perform();
      await load();
      setOpen(true);
    } catch (reason) {
      setMessage((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div>
      <button
        type="button"
        disabled={!enabled || busy}
        onClick={() =>
          void action(() =>
            api(`/agents/${agent}/publish`, {
              method: "POST",
              body: JSON.stringify({ revision }),
            }),
          )
        }
      >
        发布当前修订
      </button>
      <button
        type="button"
        disabled={busy}
        onClick={() => void action(async () => {})}
      >
        查看发布版本
      </button>
      <p>
        发布固定指令、模型修订和能力引用。资源变更后需重新发布；停用和撤权仍生效。
      </p>
      {message && <p role="alert">{message}</p>}
      {open &&
        versions.map((item) => (
          <section key={item.id}>
            <strong>
              智能体 v{item.version} · {item.active ? "已发布" : "已下线"}
            </strong>
            <p>
              草稿修订 {item.revision} · 模型 {item.snapshot.model_id}
            </p>
            <details>
              <summary>查看已发布指令</summary>
              <pre className="work-output">
                {item.snapshot.instructions || "未设置额外指令"}
              </pre>
            </details>
            <button
              type="button"
              disabled={busy}
              onClick={() =>
                void action(() =>
                  api(`/agents/${agent}/versions/${item.id}/state`, {
                    method: "PUT",
                    body: JSON.stringify({ active: !item.active }),
                  }),
                )
              }
            >
              {item.active ? "下线版本" : "重新启用版本"}
            </button>
          </section>
        ))}
      {open && !versions.length && <p>尚未发布版本。</p>}
    </div>
  );
}
