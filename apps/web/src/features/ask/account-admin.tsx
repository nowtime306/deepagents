"use client";

import {
  useCallback,
  useEffect,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X, Plus, Search, ShieldCheck } from "lucide-react";
import { api, ApiError } from "@/lib/api/platform";
import "./accounts.css";
import "./resource-list.css";

type Role = {
  id: string;
  name: string;
  permissions: string[];
  built_in: boolean;
};
type User = {
  id: string;
  username: string;
  role_id: string;
  role_name: string;
  is_admin: boolean;
  enabled: boolean;
  approval_status: string;
  must_change_password: boolean;
};
type Invitation = {
  id: string;
  name: string;
  token_hint: string;
  role_name: string;
  used_at: string | null;
  revoked_at: string | null;
  expires_at: string;
};
type Edit =
  | { kind: "user"; user: User }
  | { kind: "role"; role?: Role }
  | { kind: "create" }
  | { kind: "invite" }
  | { kind: "reset"; user: User };

export function AccountAdmin({ onSignedOut }: { onSignedOut: () => void }) {
  const [tab, setTab] = useState("users");
  const [users, setUsers] = useState<User[]>([]);
  const [roleQuery, setRoleQuery] = useState("");
  const [invitationQuery, setInvitationQuery] = useState("");
  const [roles, setRoles] = useState<Role[]>([]);
  const [permissions, setPermissions] = useState<Record<string, string>>({});
  const [invitations, setInvitations] = useState<Invitation[]>([]);
  const [mode, setMode] = useState("approval");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [edit, setEdit] = useState<Edit | null>(null);
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(
    null,
  );
  const load = useCallback(async () => {
    return await Promise.all([
      api<{ items: User[]; total: number }>(
        `/admin/accounts?q=${encodeURIComponent(query)}&page=${page}`,
      ),
      api<{ items: Role[]; permissions: Record<string, string> }>(
        "/admin/roles",
      ),
      api<{ items: Invitation[] }>("/admin/invitations"),
      api<{ registration_mode: string }>("/admin/account-settings"),
    ]);
  }, [query, page]);
  const apply = useCallback(
    ([accounts, catalog, invites, settings]: Awaited<
      ReturnType<typeof load>
    >) => {
      setUsers(accounts.items);
      setTotal(accounts.total);
      setRoles(catalog.items);
      setPermissions(catalog.permissions);
      setInvitations(invites.items);
      setMode(settings.registration_mode);
    },
    [],
  );
  const reload = useCallback(async () => apply(await load()), [apply, load]);
  useEffect(() => {
    let active = true;
    load()
      .then((data) => {
        if (active) apply(data);
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, [load, apply]);

  async function action(operation: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await reload();
      setNotice(message);
    } catch (reason) {
      if (reason instanceof ApiError && reason.status === 401) onSignedOut();
      else setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function save(value: Record<string, unknown>) {
    if (!edit) return;
    if (edit.kind === "user")
      await api(`/admin/accounts/${edit.user.id}`, {
        method: "PUT",
        body: JSON.stringify(value),
      });
    else if (edit.kind === "role")
      await api(`/admin/roles${edit.role?.id ? `/${edit.role.id}` : ""}`, {
        method: edit.role?.id ? "PUT" : "POST",
        body: JSON.stringify(value),
      });
    else if (edit.kind === "create") {
      const result = await api<{ temporary_password: string }>(
        "/admin/accounts",
        { method: "POST", body: JSON.stringify(value) },
      );
      setSecret({ label: "临时密码", value: result.temporary_password });
    } else if (edit.kind === "reset") {
      const result = await api<{ temporary_password: string }>(
        `/admin/accounts/${edit.user.id}/reset-password`,
        { method: "POST" },
      );
      setSecret({ label: "临时密码", value: result.temporary_password });
    } else {
      const result = await api<{ invitation: string }>("/admin/invitations", {
        method: "POST",
        body: JSON.stringify(value),
      });
      setSecret({ label: "邀请码", value: result.invitation });
    }
    setEdit(null);
    await reload().catch((reason) => {
      if (reason instanceof ApiError && reason.status === 401) onSignedOut();
      else setError(reason.message);
    });
    setNotice("已保存。账号或角色变更后，相关用户需重新登录。");
  }
  return (
    <section className="accounts-page">
      <header>
        <div>
          <h1>账号与权限</h1>
          <p>审核注册、分配功能角色，管理账号和登录安全。</p>
        </div>
        <ShieldCheck size={25} />
      </header>
      <nav
        className="account-tabs"
        aria-label="账号管理分类"
      >
        {[
          ["users", "账号"],
          ["roles", "功能角色"],
          ["invites", "邀请与注册"],
        ].map(([id, label]) => (
          <button
            key={id}
            aria-pressed={tab === id}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      {error && (
        <p
          role="alert"
          className="accounts-error"
        >
          {error}
        </p>
      )}
      {notice && (
        <p
          role="status"
          className="accounts-notice"
        >
          {notice}
        </p>
      )}
      {tab === "users" && (
        <>
          <div className="account-toolbar">
            <form
              onSubmit={(event) => {
                event.preventDefault();
                setPage(0);
                setQuery(search.trim());
              }}
            >
              <input
                aria-label="搜索账号"
                placeholder="搜索账号名称"
                value={search}
                maxLength={80}
                onChange={(event) => setSearch(event.target.value)}
              />
              <button aria-label="执行账号搜索">
                <Search size={16} />
              </button>
            </form>
            <button
              className="account-primary"
              onClick={() => setEdit({ kind: "create" })}
            >
              <Plus size={15} />
              创建账号
            </button>
          </div>
          <p className="account-help">
            注册审核通过后才能使用系统。停用、角色变更、重置密码会撤销旧会话；停用不会删除私有数据。
          </p>
          <div className="accounts-table">
            <table>
              <thead>
                <tr>
                  <th>账号</th>
                  <th>角色</th>
                  <th>状态</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => (
                  <tr key={user.id}>
                    <td>
                      {user.username}
                      {user.must_change_password && <small>登录后需改密</small>}
                    </td>
                    <td>{user.is_admin ? "平台管理员" : user.role_name}</td>
                    <td>
                      {!user.enabled
                        ? "已停用"
                        : user.approval_status === "pending"
                          ? "待审核"
                          : user.approval_status === "rejected"
                            ? "审核未通过"
                            : "正常"}
                    </td>
                    <td>
                      <div className="account-row-actions">
                        <button onClick={() => setEdit({ kind: "user", user })}>
                          权限与状态
                        </button>
                        <button
                          onClick={() => setEdit({ kind: "reset", user })}
                        >
                          重置密码
                        </button>
                        <button
                          disabled={busy}
                          onClick={() =>
                            void action(
                              () =>
                                api(`/admin/accounts/${user.id}/sessions`, {
                                  method: "DELETE",
                                }),
                              "登录会话已撤销",
                            )
                          }
                        >
                          撤销会话
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!users.length && <p>没有匹配的账号。</p>}
          </div>
          <div className="account-pagination">
            <span>共 {total} 个账号</span>
            <button
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              上一页
            </button>
            <span>第 {page + 1} 页</span>
            <button
              disabled={(page + 1) * 50 >= total}
              onClick={() => setPage(page + 1)}
            >
              下一页
            </button>
          </div>
        </>
      )}
      {tab === "roles" && (
        <>
          <div className="account-toolbar">
            <p>功能权限不会扩大资源归属或绕过任务审批。</p>
            <button
              className="account-primary"
              onClick={() => setEdit({ kind: "role" })}
            >
              新增角色
            </button>
          </div>
          <label className="resource-list-search"><input aria-label="搜索角色" placeholder="搜索角色名称" value={roleQuery} onChange={(e) => setRoleQuery(e.target.value)}/><span>{roles.length}个角色</span></label>
          <div className="account-role-grid">
      {roles.length > 0 && !roles.some((item) => item.name.toLowerCase().includes(roleQuery.toLowerCase())) && <p>没有匹配的记录。</p>}
          {roles.filter((role) => role.name.toLowerCase().includes(roleQuery.toLowerCase())).map((role) => (
              <article
                className="account-card"
                key={role.id}
              >
                <h2>{role.name}</h2>
                <small>
                  {role.built_in ? "预设角色" : "自定义角色"} ·{" "}
                  {role.id === "admin"
                    ? "全部权限与平台管理"
                    : `${role.permissions.length} 项功能权限`}
                </small>
                <div className="account-permission-tags">
                  {role.permissions.slice(0, 5).map((permission) => (
                    <span key={permission}>
                      {permission === "*"
                        ? "平台管理"
                        : permissions[permission] || permission}
                    </span>
                  ))}
                </div>
                <div className="account-row-actions">
                  <button
                    onClick={() =>
                      setEdit({
                        kind: "role",
                        role: role.built_in
                          ? {
                              ...role,
                              id: "",
                              name: role.name + "副本",
                              permissions: role.permissions.filter(
                                (item) => item !== "*",
                              ),
                            }
                          : role,
                      })
                    }
                  >
                    {role.built_in ? "基于此创建" : "编辑角色"}
                  </button>
                  {!role.built_in && (
                    <button
                      disabled={busy}
                      onClick={() =>
                        void action(
                          () =>
                            api(`/admin/roles/${role.id}`, {
                              method: "DELETE",
                            }),
                          "未使用的角色已删除",
                        )
                      }
                    >
                      删除角色
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </>
      )}
      {tab === "invites" && (
        <>
          <article className="account-card">
            <h2>注册方式</h2>
            <form
              className="account-inline-form"
              onSubmit={(event) => {
                event.preventDefault();
                void action(
                  () =>
                    api("/admin/account-settings", {
                      method: "PUT",
                      body: JSON.stringify({ registration_mode: mode }),
                    }),
                  "注册方式已更新",
                );
              }}
            >
              <label>
                注册方式
                <select
                  aria-label="注册方式"
                  value={mode}
                  onChange={(event) => setMode(event.target.value)}
                >
                  <option value="approval">提交后由管理员审核</option>
                  <option value="open">开放注册，自动成为成员</option>
                  <option value="closed">关闭注册，仅允许邀请码</option>
                </select>
              </label>
              <button
                className="account-primary"
                disabled={busy}
              >
                保存注册方式
              </button>
            </form>
            <p>
              有效邀请码可直接使用指定角色，一次使用后失效。邀请明文只在创建时显示。
            </p>
          </article>
          <div className="account-toolbar">
            <h2>邀请码</h2>
            <button
              className="account-primary"
              onClick={() => setEdit({ kind: "invite" })}
            >
              创建邀请
            </button>
          </div>
          <label className="resource-list-search"><input aria-label="搜索邀请" placeholder="搜索邀请名称" value={invitationQuery} onChange={(e) => setInvitationQuery(e.target.value)}/><span>{invitations.length}条邀请</span></label>
          <div className="account-role-grid">
      {invitations.length > 0 && !invitations.some((item) => item.name.toLowerCase().includes(invitationQuery.toLowerCase())) && <p>没有匹配的记录。</p>}
          {invitations.filter((invitation) => invitation.name.toLowerCase().includes(invitationQuery.toLowerCase())).map((invitation) => (
              <article
                key={invitation.id}
                className="account-card"
              >
                <h2>{invitation.name}</h2>
                <p>
                  {invitation.role_name} · 尾号 {invitation.token_hint}
                </p>
                <small>
                  {invitation.used_at
                    ? "已使用"
                    : invitation.revoked_at
                      ? "已撤销"
                      : `有效期至 ${new Date(invitation.expires_at).toLocaleString("zh-CN")}`}
                </small>
                <button
                  disabled={
                    busy || !!invitation.revoked_at || !!invitation.used_at
                  }
                  onClick={() =>
                    void action(
                      () =>
                        api(`/admin/invitations/${invitation.id}`, {
                          method: "DELETE",
                        }),
                      "邀请已撤销",
                    )
                  }
                >
                  撤销邀请
                </button>
              </article>
            ))}
          </div>
        </>
      )}
      {edit && (
        <EditDialog
          key={
            edit.kind +
            ("user" in edit
              ? edit.user.id
              : "role" in edit
                ? edit.role?.id || "new"
                : "new")
          }
          edit={edit}
          roles={roles}
          permissions={permissions}
          onClose={() => setEdit(null)}
          onSave={save}
        />
      )}
      {secret && (
        <AccountDialog
          title={secret.label}
          onClose={() => setSecret(null)}
        >
          <p>请妥善交给使用者；关闭后无法再次查看。临时密码登录后必须修改。</p>
          <label>
            {secret.label}
            <input
              aria-label={secret.label}
              readOnly
              value={secret.value}
              onFocus={(event) => event.target.select()}
            />
          </label>
          <footer>
            <button
              className="account-primary"
              onClick={() => setSecret(null)}
            >
              我已保存
            </button>
          </footer>
        </AccountDialog>
      )}
    </section>
  );
}

function EditDialog({
  edit,
  roles,
  permissions,
  onClose,
  onSave,
}: {
  edit: Edit;
  roles: Role[];
  permissions: Record<string, string>;
  onClose: () => void;
  onSave: (value: Record<string, unknown>) => Promise<void>;
}) {
  const user = edit.kind === "user" ? edit.user : null;
  const role = edit.kind === "role" ? edit.role : null;
  const [name, setName] = useState(role?.name || "");
  const [selected, setSelected] = useState(role?.permissions || []);
  const [roleId, setRoleId] = useState(
    user?.is_admin ? "admin" : user?.role_id || "member",
  );
  const [enabled, setEnabled] = useState(user?.enabled ?? true);
  const [status, setStatus] = useState(user?.approval_status || "approved");
  const [days, setDays] = useState(7);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const title = {
    user: "权限与状态",
    role: role?.id ? "编辑角色" : "新增角色",
    create: "创建账号",
    invite: "创建邀请",
    reset: "重置密码",
  }[edit.kind];
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    const value =
      edit.kind === "user"
        ? { role_id: roleId, enabled, approval_status: status }
        : edit.kind === "role"
          ? { name, permissions: selected }
          : edit.kind === "invite"
            ? { name, role_id: roleId, valid_days: days }
            : { username: name };
    try {
      await onSave(value);
    } catch (reason) {
      setError((reason as Error).message);
      setBusy(false);
    }
  }
  return (
    <AccountDialog
      title={title}
      busy={busy}
      onClose={onClose}
    >
      <form onSubmit={(event) => void submit(event)}>
        {edit.kind === "reset" ? (
          <p>
            将为 {edit.user.username}{" "}
            生成新的临时密码并退出所有旧会话。原密码无法查看。
          </p>
        ) : edit.kind === "user" ? (
          <p>
            {user?.username}：变更会撤销旧会话；最后一个可用管理员受到保护。
          </p>
        ) : (
          <label>
            {edit.kind === "create"
              ? "账号名称"
              : edit.kind === "invite"
                ? "邀请名称"
                : "角色名称"}
            <input
              aria-label={
                edit.kind === "create"
                  ? "账号名称"
                  : edit.kind === "invite"
                    ? "邀请名称"
                    : "角色名称"
              }
              required
              minLength={edit.kind === "create" ? 3 : 1}
              maxLength={edit.kind === "create" ? 40 : 80}
              pattern={edit.kind === "create" ? "[A-Za-z0-9_.-]+" : undefined}
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
        )}
        {(edit.kind === "user" || edit.kind === "invite") && (
          <label>
            分配角色
            <select
              aria-label="分配角色"
              value={roleId}
              onChange={(event) => setRoleId(event.target.value)}
            >
              {roles.map((item) => (
                <option
                  key={item.id}
                  value={item.id}
                >
                  {item.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {edit.kind === "user" && (
          <>
            <label>
              审核状态
              <select
                aria-label="审核状态"
                value={status}
                onChange={(event) => setStatus(event.target.value)}
              >
                <option value="approved">通过</option>
                <option value="pending">待审核</option>
                <option value="rejected">不通过</option>
              </select>
            </label>
            <label className="account-checkbox">
              <input
                type="checkbox"
                aria-label="启用账号"
                checked={enabled}
                onChange={(event) => setEnabled(event.target.checked)}
              />
              启用账号
            </label>
          </>
        )}
        {edit.kind === "invite" && (
          <label>
            有效天数
            <input
              aria-label="有效天数"
              type="number"
              min={1}
              max={30}
              required
              value={days}
              onChange={(event) => setDays(Number(event.target.value))}
            />
          </label>
        )}
        {edit.kind === "role" && (
          <fieldset className="account-permissions">
            <legend>允许使用的功能</legend>
            {Object.entries(permissions).map(([key, label]) => (
              <label
                className="account-checkbox"
                key={key}
              >
                <input
                  type="checkbox"
                  checked={selected.includes(key)}
                  onChange={(event) =>
                    setSelected(
                      event.target.checked
                        ? [...selected, key]
                        : selected.filter((item) => item !== key),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </fieldset>
        )}
        {edit.kind === "create" && (
          <p>
            账号仅允许 3–40
            位字母、数字、点、短横线或下划线。生成的临时密码只显示一次。
          </p>
        )}
        {error && (
          <p
            role="alert"
            className="accounts-error"
          >
            {error}
          </p>
        )}
        <footer>
          <button
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            取消
          </button>
          <button
            className="account-primary"
            disabled={busy}
          >
            {busy ? "正在保存…" : edit.kind === "reset" ? "确认重置" : "保存"}
          </button>
        </footer>
      </form>
    </AccountDialog>
  );
}

function AccountDialog({
  title,
  children,
  onClose,
  busy = false,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
  busy?: boolean;
}) {
  return (
    <Dialog.Root
      open
      onOpenChange={(open) => {
        if (!open && !busy) onClose();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="workspace-overlay" />
        <Dialog.Content
          className="workspace-dialog account-dialog"
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault();
          }}
          onPointerDownOutside={(event) => {
            if (busy) event.preventDefault();
          }}
        >
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description className="sr-only">
            管理账号与权限。更改后权限由服务器校验。
          </Dialog.Description>
          <Dialog.Close
            className="workspace-dialog-close"
            aria-label="关闭弹窗"
            disabled={busy}
          >
            <X size={18} />
          </Dialog.Close>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
