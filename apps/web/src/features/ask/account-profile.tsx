"use client";

import { useEffect, useState, type FormEvent } from "react";
import { api } from "@/lib/api/platform";
import "./accounts.css";
import { IdentitySecurity } from "@/features/identity/identity-security";

export type AccountIdentity = {
  id?: string;
  username: string;
  is_admin: boolean;
  role_name?: string;
  role_id?: string;
  permissions?: string[];
  must_change_password?: boolean;
};
type Session = {
  id: string;
  created_at: string | null;
  expires_at: string;
  current: boolean;
};

export function AccountProfile({
  forced = false,
  onSignedOut,
}: {
  forced?: boolean;
  onSignedOut: () => void;
}) {
  const [user, setUser] = useState<AccountIdentity | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [current, setCurrent] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    Promise.all([
      api<AccountIdentity>("/auth/me"),
      api<{ items: Session[] }>("/auth/sessions"),
    ])
      .then(([identity, response]) => {
        if (active) {
          setUser(identity);
          setSessions(response.items);
        }
      })
      .catch((reason) => {
        if (active) setError(reason.message);
      });
    return () => {
      active = false;
    };
  }, []);

  async function change(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (password !== confirmation) {
      setError("两次输入的新密码不一致");
      return;
    }
    setBusy(true);
    try {
      await api("/auth/password", {
        method: "POST",
        body: JSON.stringify({
          current_password: current,
          new_password: password,
        }),
      });
      setCurrent("");
      setPassword("");
      setConfirmation("");
      onSignedOut();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function revoke(session: Session) {
    setBusy(true);
    setError("");
    try {
      await api(`/auth/sessions/${session.id}`, { method: "DELETE" });
      if (session.current) onSignedOut();
      else
        setSessions((items) => items.filter((item) => item.id !== session.id));
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="accounts-page">
      <header>
        <div>
          <h1>{forced ? "请先修改临时密码" : "个人设置"}</h1>
          <p>
            {forced
              ? "管理员为你设置了临时密码。修改后重新登录，即可使用已授权功能。"
              : "管理你的账号、密码和登录会话。"}
          </p>
        </div>
      </header>
      {error && (
        <p
          className="accounts-error"
          role="alert"
        >
          {error}
        </p>
      )}
      <article className="account-card">
        <h2>账号信息</h2>
        <p>
          {user?.username || "正在加载…"} · {user?.role_name || "成员"}
        </p>
        <small>
          功能权限由平台管理员分配，组织内角色在组织管理中单独维护。
        </small>
      </article>
      <article className="account-card">
        <h2>修改密码</h2>
        <p>至少 12 个字符。修改后所有旧会话都会退出，请使用新密码重新登录。</p>
        <form
          className="account-form"
          onSubmit={(event) => void change(event)}
        >
          <label>
            原密码
            <input
              aria-label="原密码"
              type="password"
              autoComplete="current-password"
              required
              maxLength={256}
              value={current}
              onChange={(event) => setCurrent(event.target.value)}
            />
          </label>
          <label>
            新密码
            <input
              aria-label="新密码"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={256}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          <label>
            确认新密码
            <input
              aria-label="确认新密码"
              type="password"
              autoComplete="new-password"
              required
              minLength={12}
              maxLength={256}
              value={confirmation}
              onChange={(event) => setConfirmation(event.target.value)}
            />
          </label>
          <button
            className="account-primary"
            disabled={busy}
          >
            {busy ? "正在修改…" : "修改密码并退出旧会话"}
          </button>
        </form>
      </article>
      {!forced && (
        <article className="account-card">
          <h2>登录会话</h2>
          <p>发现不熟悉的会话时，可立即撤销。</p>
          {sessions.map((session) => (
            <div
              className="account-session"
              key={session.id}
            >
              <div>
                <strong>{session.current ? "当前会话" : "其他会话"}</strong>
                <small>
                  {session.created_at
                    ? `创建于 ${new Date(session.created_at).toLocaleString("zh-CN")}`
                    : "创建时间未记录"}{" "}
                  · 到期 {new Date(session.expires_at).toLocaleString("zh-CN")}
                </small>
              </div>
              <button
                disabled={busy}
                onClick={() => void revoke(session)}
              >
                {session.current ? "退出此会话" : "撤销会话"}
              </button>
            </div>
          ))}
        </article>
      )}
      {!forced && <IdentitySecurity />}
    </section>
  );
}
