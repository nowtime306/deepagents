"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "./resource-dialog";

type Organization = { id: string; name: string; role: string };
type Member = { user_id: string; username: string; role: string };
type Version = {
  id: string;
  name: string;
  version: number;
  active: boolean;
  digest?: string;
};
type Run = {
  id: string;
  status: string;
  output: string | null;
  created_at: string;
};
const roleNames: Record<string, string> = {
  owner: "所有者",
  admin: "管理员",
  editor: "编辑者",
  viewer: "查看者",
};
const states: Record<string, string> = {
  PENDING: "待执行",
  RUNNING: "运行中",
  SUCCEEDED: "成功",
  FAILED: "失败",
  NEEDS_REVIEW: "需要核查",
};

export function Organizations({ canCreate = true }: { canCreate?: boolean }) {
  const [editor, setEditor] = useState<"create" | "member" | "share" | "run" | null>(null);
  const [query, setQuery] = useState("");
  const [memberQuery, setMemberQuery] = useState("");
  const [sharedQuery, setSharedQuery] = useState("");
  const [items, setItems] = useState<Organization[]>([]),
    [selected, setSelected] = useState("");
  const [members, setMembers] = useState<Member[]>([]),
    [shared, setShared] = useState<Version[]>([]),
    [versions, setVersions] = useState<Version[]>([]),
    [runs, setRuns] = useState<Run[]>([]);
  const [name, setName] = useState(""),
    [username, setUsername] = useState(""),
    [role, setRole] = useState("viewer"),
    [version, setVersion] = useState(""),
    [input, setInput] = useState(""),
    [executeVersion, setExecuteVersion] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const requestKey = useRef("");
  const current = items.find((item) => item.id === selected);
  const manage = current?.role === "owner" || current?.role === "admin";
  async function reload(id = selected) {
    const organizations = await api<{ items: Organization[] }>(
      "/organizations",
    );
    setItems(organizations.items);
    if (!id) return;
    const [m, w, r] = await Promise.all([
      api<{ items: Member[] }>(`/organizations/${id}/members`),
      api<{ items: Version[] }>(`/organizations/${id}/workflows`),
      api<{ items: Run[] }>(`/organizations/${id}/runs`),
    ]);
    setMembers(m.items);
    setShared(w.items);
    setRuns(r.items);
  }
  useEffect(() => {
    reload().catch((reason) => setError(reason.message));
    api<{ items: { id: string }[] }>("/workflows")
      .then(async (result) => {
        const all = await Promise.all(
          result.items.map((item) =>
            api<{ items: Version[] }>(`/workflows/${item.id}/versions`),
          ),
        );
        setVersions(
          all.flatMap((r) => r.items).filter((v) => v.active && v.digest),
        );
      })
      .catch((reason) => setError(reason.message));
  }, []);
  useEffect(() => {
    if (!selected) return;
    let active = true;
    const timer = setInterval(() => {
      api<{ items: Run[] }>(`/organizations/${selected}/runs`)
        .then((result) => {
          if (active) setRuns(result.items);
        })
        .catch((reason) => {
          if (active) setError(reason.message);
        });
    }, 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [selected]);
  async function action(operation: () => Promise<unknown>) {
    setBusy(true);
    setError("");
    try {
      await operation();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function create(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      const result = await api<{ id: string }>("/organizations", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      setName("");
      setSelected(result.id);
      await reload(result.id);
      setEditor(null);
    });
  }
  return (
    <section
      className="run-history"
      aria-label="组织管理"
    >
      <header className="resource-list-heading"><h1>组织管理</h1><button className="settings-primary" disabled={!canCreate} onClick={() => { setName(""); setError(""); setEditor("create"); }}>新建组织</button></header>
      <p>
        私有会话、知识和记忆仍归原账号管理。组织通过显式共享已发布流程协作；调用可能使用流程所有者绑定的知识、记忆和工具。
      </p>
      {error && !editor && (
        <p
          role="alert"
          className="platform-error"
        >
          {error}
        </p>
      )}
      {editor === "create" && <ResourceDialog title="新建组织" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}
      <form
        className="agent-form"
        onSubmit={create}
      >
        <label>
          组织名称
          <input
            required
            maxLength={100}
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        <button disabled={busy}>创建组织</button>
      </form></ResourceDialog>}
      {!selected && <><label className="resource-list-search"><input aria-label="搜索组织" placeholder="搜索组织名称" value={query} onChange={(e) => setQuery(e.target.value)}/><span>{items.length}个组织</span></label>
      {!items.length && <p>还没有组织，点击右上角“新建组织”开始协作。</p>}
      {items.filter((item) => item.name.toLowerCase().includes(query.toLowerCase())).map((item) => <article className="run-history-card" key={item.id}><div><strong>{item.name}</strong><p>{roleNames[item.role]}</p></div><button onClick={() => { setSelected(item.id); setError(""); void action(() => reload(item.id)); }}>管理组织</button></article>)}</>}
      {selected && <label className="resource-list-search">
        当前组织
        <select
          aria-label="当前组织"
          value={selected}
          onChange={(e) => {
            const id = e.target.value;
            setEditor(null); setError("");
            setSelected(id);
            setMembers([]);
            setShared([]);
            setRuns([]);
            setExecuteVersion("");
            requestKey.current = "";
            void action(() => reload(id));
          }}
        >
          <option value="">请选择组织</option>
          {items.map((item) => (
            <option
              key={item.id}
              value={item.id}
            >
              {item.name} · {roleNames[item.role]}
            </option>
          ))}
        </select>
      </label>}
      {current && (
        <>
          <button onClick={() => { setSelected(""); setEditor(null); setError(""); setMemberQuery(""); setSharedQuery(""); setMembers([]); setShared([]); setRuns([]); }}>返回组织列表</button>
          <h2>
            {current.name} · {roleNames[current.role]}
          </h2>
          {current.role === "owner" && (
            <button
              disabled={busy}
              onClick={() => {
                if (
                  window.confirm(
                    "删除这个空组织？仍有成员、共享或调用记录的组织无法删除。",
                  )
                )
                  void action(async () => {
                    await api(`/organizations/${selected}`, {
                      method: "DELETE",
                    });
                    setSelected("");
                    await reload("");
                  });
              }}
            >
              删除空组织
            </button>
          )}
          <header className="resource-list-heading"><h3>组织成员 · {members.length}</h3>{manage && <button className="settings-primary" onClick={() => { setUsername(""); setRole("viewer"); setError(""); setEditor("member"); }}>添加成员</button>}</header>
          {manage && editor === "member" && (
            <ResourceDialog title="添加或更新组织成员" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}<form
              className="agent-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action(async () => {
                  await api(`/organizations/${selected}/members`, {
                    method: "PUT",
                    body: JSON.stringify({ username, role }),
                  });
                  setUsername("");
                  await reload(); setEditor(null);
                });
              }}
            >
              <label>
                成员账号
                <input
                  required
                  maxLength={80}
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </label>
              <label>
                成员角色
                <select
                  aria-label="成员角色"
                  value={role}
                  onChange={(e) => setRole(e.target.value)}
                >
                  {current.role === "owner" && (
                    <option value="admin">管理员</option>
                  )}
                  <option value="editor">编辑者</option>
                  <option value="viewer">查看者</option>
                </select>
              </label>
              <p>
                只添加已有本地账号。所有者管理管理员，管理员管理编辑者与查看者；查看者不能执行或共享流程。
              </p>
              <button disabled={busy}>添加或更新成员</button>
            </form></ResourceDialog>
          )}
          <label className="resource-list-search"><input aria-label="搜索组织成员" placeholder="搜索成员账号" value={memberQuery} onChange={(e) => setMemberQuery(e.target.value)}/></label>
          {members.filter((member) => member.username.toLowerCase().includes(memberQuery.toLowerCase())).map((member) => (
            <article
              className="run-history-card"
              key={member.user_id}
            >
              <strong>{member.username}</strong>
              <span>{roleNames[member.role]}</span>
              {manage &&
                member.role !== "owner" &&
                (current.role === "owner" || member.role !== "admin") && (
                  <><button disabled={busy} onClick={() => { setUsername(member.username); setRole(member.role); setError(""); setEditor("member"); }}>编辑成员</button>
                  <button
                    disabled={busy}
                    onClick={() => {
                      if (
                        window.confirm(
                          `移除成员“${member.username}”？新操作立即失去授权。`,
                        )
                      )
                        void action(async () => {
                          await api(
                            `/organizations/${selected}/members/${member.user_id}`,
                            { method: "DELETE" },
                          );
                          await reload();
                        });
                    }}
                  >
                    移除成员
                  </button></>
                )}
            </article>
          ))}
          <header className="resource-list-heading"><h3>共享发布版本 · {shared.length}</h3>{current.role !== "viewer" && <button className="settings-primary" onClick={() => { setVersion(""); setError(""); setEditor("share"); }}>共享版本</button>}</header>
          {current.role !== "viewer" && editor === "share" && (
            <ResourceDialog title="共享发布版本" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}<form
              className="agent-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action(async () => {
                  await api(`/organizations/${selected}/workflows`, {
                    method: "POST",
                    body: JSON.stringify({ version_id: version }),
                  });
                  await reload(); setEditor(null);
                });
              }}
            >
              <label>
                共享我的发布版本
                <select
                  aria-label="共享我的发布版本"
                  required
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                >
                  <option value="">请选择自己的已测试上线版本</option>
                  {versions.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.name} · v{item.version}
                    </option>
                  ))}
                </select>
              </label>
              <button disabled={busy || !versions.length}>共享到组织</button>
            </form></ResourceDialog>
          )}
          <label className="resource-list-search"><input aria-label="搜索共享版本" placeholder="搜索共享工作流" value={sharedQuery} onChange={(e) => setSharedQuery(e.target.value)}/></label>
          {!shared.length && <p>还没有共享的发布版本。</p>}
          {shared.filter((item) => item.name.toLowerCase().includes(sharedQuery.toLowerCase())).map((item) => (
            <article
              className="run-history-card"
              key={item.id}
            >
              <strong>
                {item.name} · v{item.version}
              </strong>
              <span>{item.active ? "已上线" : "已下线"}</span>
              {current.role !== "viewer" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    void action(async () => {
                      await api(
                        `/organizations/${selected}/workflows/${item.id}`,
                        { method: "DELETE" },
                      );
                      await reload();
                    })
                  }
                >
                  撤销共享
                </button>
              )}
            </article>
          ))}
          {current.role !== "viewer" && <button className="settings-primary" disabled={!shared.some((item) => item.active)} title="请选择组织内已共享并上线的工作流" onClick={() => { setError(""); setEditor("run"); }}>运行共享流程</button>}
          {current.role !== "viewer" && editor === "run" && (
            <ResourceDialog title="运行共享流程" busy={busy} close={() => setEditor(null)}>{error && <p role="alert">{error}</p>}<form
              className="agent-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action(async () => {
                  requestKey.current ||= crypto.randomUUID();
                  await api(
                    `/organizations/${selected}/workflows/${executeVersion}/runs`,
                    {
                      method: "POST",
                      headers: { "Idempotency-Key": requestKey.current },
                      body: JSON.stringify({ text: input }),
                    },
                  );
                  await reload(); setEditor(null);
                });
              }}
            >
              <label>
                执行共享流程
                <select
                  aria-label="执行共享流程"
                  required
                  value={executeVersion}
                  onChange={(e) => {
                    setExecuteVersion(e.target.value);
                    requestKey.current = "";
                  }}
                >
                  <option value="">请选择组织内上线版本</option>
                  {shared
                    .filter((v) => v.active)
                    .map((item) => (
                      <option
                        value={item.id}
                        key={item.id}
                      >
                        {item.name} · v{item.version}
                      </option>
                    ))}
                </select>
              </label>
              <label>
                流程输入
                <textarea
                  aria-label="组织流程输入"
                  required
                  maxLength={16000}
                  value={input}
                  onChange={(e) => {
                    setInput(e.target.value);
                    requestKey.current = "";
                  }}
                />
              </label>
              <button disabled={busy}>提交组织流程</button>
            </form></ResourceDialog>
          )}
          <h3>运行记录</h3>
          <p>普通成员查看自己的调用；所有者和管理员可查看组织内调用。</p>
          {runs.map((run) => (
            <article
              className="run-history-card"
              key={run.id}
            >
              <strong>{states[run.status] || run.status}</strong>
              <small>
                {run.id} · {new Date(run.created_at).toLocaleString("zh-CN")}
              </small>
              {run.output && <pre className="work-output">{run.output}</pre>}
            </article>
          ))}
        </>
      )}
    </section>
  );
}
