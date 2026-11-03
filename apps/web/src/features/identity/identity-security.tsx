"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";

export function IdentitySecurity() {
  const [status, setStatus] = useState({
    enabled: false,
    recovery_remaining: 0,
  });
  const [password, setPassword] = useState(""),
    [code, setCode] = useState("");
  const [secret, setSecret] = useState(""),
    [codes, setCodes] = useState<string[]>([]);
  const [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [message, setMessage] = useState("");
  const [kind, setKind] = useState("email"),
    [address, setAddress] = useState(""),
    [token, setToken] = useState("");
  const [contacts, setContacts] = useState<
    { kind: string; verified: boolean }[]
  >([]);
  const refresh = useCallback(async () => {
    setStatus(await api("/auth/security"));
    setContacts(
      (await api<{ items: typeof contacts }>("/auth/security/contacts")).items,
    );
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh().catch((e) => setError(e.message));
    }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  const proof = { password, code };
  return (
    <section aria-label="个人身份安全">
      <h3>多因素验证</h3>
      <p>
        {status.enabled
          ? `已启用 · 剩余 ${status.recovery_remaining} 个恢复码`
          : "尚未启用验证器"}
      </p>
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      <label>
        当前密码（企业登录5分钟内可留空）
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      {!status.enabled && (
        <button
          disabled={busy}
          onClick={() =>
            void run(async () => {
              setSecret(
                (
                  await api<{ secret: string }>("/auth/security/mfa/enroll", {
                    method: "POST",
                    body: JSON.stringify(proof),
                  })
                ).secret,
              );
            })
          }
        >
          配置验证器
        </button>
      )}
      {secret && !status.enabled && (
        <p>
          在验证器中手动录入密钥：<code>{secret}</code>
          ，然后填写动态验证码完成启用。
        </p>
      )}
      <label>
        动态验证码或恢复码
        <input
          autoComplete="one-time-code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      {secret && !status.enabled && (
        <button
          disabled={busy || !code}
          onClick={() =>
            void run(async () => {
              setCodes(
                (
                  await api<{ recovery_codes: string[] }>(
                    "/auth/security/mfa/confirm",
                    { method: "POST", body: JSON.stringify(proof) },
                  )
                ).recovery_codes,
              );
              setSecret("");
              setCode("");
            })
          }
        >
          确认启用多因素验证
        </button>
      )}
      {status.enabled && (
        <button
          disabled={busy || !code}
          onClick={() =>
            void run(async () => {
              await api("/auth/security/mfa/disable", {
                method: "POST",
                body: JSON.stringify(proof),
              });
              setCode("");
              setCodes([]);
            })
          }
        >
          验证并关闭验证器
        </button>
      )}
      {codes.length > 0 && (
        <div role="status">
          <p>恢复码仅显示这一次，请离线保存，每个只能使用一次。</p>
          <pre>{codes.join("\n")}</pre>
          <button
            onClick={() => void navigator.clipboard.writeText(codes.join("\n"))}
          >
            复制恢复码
          </button>
          <button onClick={() => setCodes([])}>我已保存</button>
        </div>
      )}
      <IdentityBindings />
      <h3>找回密码联系方式</h3>
      <ul>
        {contacts.map((c) => (
          <li key={c.kind}>
            {c.kind === "email" ? "电子邮箱" : "短信手机"} ·{" "}
            {c.verified ? "已验证" : "待验证"}
          </li>
        ))}
      </ul>
      <label>
        验证方式
        <select
          value={kind}
          onChange={(e) => setKind(e.target.value)}
        >
          <option value="email">电子邮箱</option>
          <option value="sms">短信</option>
        </select>
      </label>
      <label>
        联系方式
        <input
          value={address}
          onChange={(e) => setAddress(e.target.value)}
        />
      </label>
      <button
        disabled={busy || !address}
        onClick={() =>
          void run(async () => {
            await api("/auth/security/contacts", {
              method: "POST",
              body: JSON.stringify({ kind, address, password }),
            });
            setMessage("验证消息已发送，请填写收到的凭据");
          })
        }
      >
        验证当前密码并发送
      </button>
      <label>
        联系方式验证凭据
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
        />
      </label>
      <button
        disabled={busy || !token}
        onClick={() =>
          void run(async () => {
            await api("/auth/security/contacts/verify", {
              method: "POST",
              body: JSON.stringify({ token }),
            });
            setToken("");
          })
        }
      >
        确认联系方式
      </button>
    </section>
  );
}

export function IdentityBindings() {
  const [providers, setProviders] = useState<{ id: string; name: string }[]>(
      [],
    ),
    [bound, setBound] = useState<{ id: string; name: string }[]>([]);
  const [password, setPassword] = useState(""),
    [code, setCode] = useState(""),
    [error, setError] = useState("");
  const refresh = useCallback(async () => {
    setProviders(
      (await api<{ items: typeof providers }>("/auth/identity/providers"))
        .items,
    );
    setBound(
      (await api<{ items: typeof bound }>("/auth/security/bindings")).items,
    );
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh().catch((e) => setError(e.message));
    }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);
  async function action(operation: () => Promise<void>) {
    setError("");
    try {
      await operation();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <section aria-label="企业身份绑定">
      <h3>企业身份绑定</h3>
      {error && <p role="alert">{error}</p>}
      <p>绑定前重新验证当前密码；已开启验证器时还需动态验证码或恢复码。</p>
      <label>
        绑定验证密码
        <input
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
      </label>
      <label>
        绑定验证动态码
        <input
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </label>
      <ul>
        {bound.map((p) => (
          <li key={p.id}>
            {p.name}
            <button
              disabled={false}
              onClick={() =>
                void action(async () => {
                  await api(`/auth/security/bindings/${p.id}/remove`, {
                    method: "POST",
                    body: JSON.stringify({ password, code }),
                  });
                })
              }
            >
              验证并解绑{p.name}
            </button>
          </li>
        ))}
      </ul>
      {providers
        .filter((p) => !bound.some((b) => b.id === p.id))
        .map((p) => (
          <button
            key={p.id}
            disabled={false}
            onClick={() =>
              void action(async () => {
                location.href = (
                  await api<{ url: string }>(`/auth/identity/${p.id}/begin`, {
                    method: "POST",
                    body: JSON.stringify({ bind: true, password, code }),
                  })
                ).url;
              })
            }
          >
            绑定{p.name}
          </button>
        ))}
    </section>
  );
}

export function IdentityLogin({
  challenge: initial = "",
  onAuthenticated,
  onRestart,
}: {
  challenge?: string;
  onAuthenticated: () => void;
  onRestart?: () => void;
}) {
  const pendingCancelled = useRef(false);
  const [storedChallenge, setChallenge] = useState(initial),
    [code, setCode] = useState(""),
    [error, setError] = useState("");
  const [providers, setProviders] = useState<{ id: string; name: string }[]>(
      [],
    ),
    [busy, setBusy] = useState(false);
  const [recover, setRecover] = useState(false),
    [username, setUsername] = useState(""),
    [kind, setKind] = useState("email"),
    [token, setToken] = useState(""),
    [password, setPassword] = useState(""),
    [message, setMessage] = useState("");
  const challenge = initial || storedChallenge;
  useEffect(() => {
    void api<{ items: typeof providers }>("/auth/identity/providers")
      .then((r) => setProviders(r.items))
      .catch(() => {});
    void api<{ challenge: string }>("/auth/security/pending")
      .then((r) => {
        if (!pendingCancelled.current && r.challenge) setChallenge(r.challenge);
      })
      .catch(() => {});
  }, []);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="企业登录与身份验证">
      {error && <p role="alert">{error}</p>}
      {message && <p role="status">{message}</p>}
      {challenge ? (
        <>
          <label>
            动态验证码或一次恢复码
            <input
              autoComplete="one-time-code"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <button
            disabled={busy || !code}
            onClick={() =>
              void run(async () => {
                await api("/auth/security/challenge", {
                  method: "POST",
                  body: JSON.stringify({ challenge, code }),
                });
                onAuthenticated();
              })
            }
          >
            完成身份验证
          </button>
          <button
            disabled={busy}
            onClick={() =>
              void run(async () => {
                pendingCancelled.current = true;
                await api("/auth/security/pending/cancel", {
                  method: "POST",
                  body: JSON.stringify({ challenge }),
                });
                setChallenge("");
                setCode("");
                setMessage("");
                onRestart?.();
              })
            }
          >
            返回登录，重新开始
          </button>
        </>
      ) : (
        providers.map((p) => (
          <button
            key={p.id}
            disabled={busy}
            onClick={() =>
              void run(async () => {
                location.href = (
                  await api<{ url: string }>(`/auth/identity/${p.id}/begin`, {
                    method: "POST",
                    body: "{}",
                  })
                ).url;
              })
            }
          >
            {p.name}登录
          </button>
        ))
      )}
      <button onClick={() => setRecover(!recover)}>找回密码</button>
      {recover && (
        <div>
          <label>
            账号
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </label>
          <label>
            找回方式
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value)}
            >
              <option value="email">已验证邮箱</option>
              <option value="sms">已验证手机</option>
            </select>
          </label>
          <button
            disabled={busy || !username}
            onClick={() =>
              void run(async () => {
                const r = await api<{ message: string }>(
                  "/auth/identity/recovery/request",
                  { method: "POST", body: JSON.stringify({ username, kind }) },
                );
                setMessage(r.message);
              })
            }
          >
            发送找回凭据
          </button>
          <label>
            收到的找回凭据
            <input
              value={token}
              onChange={(e) => setToken(e.target.value)}
            />
          </label>
          <label>
            新密码
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <label>
            动态验证码或恢复码（已启用验证器时必填）
            <input
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <button
            disabled={busy || !token || password.length < 12}
            onClick={() =>
              void run(async () => {
                await api("/auth/identity/recovery/reset", {
                  method: "POST",
                  body: JSON.stringify({ token, password, mfa_code: code }),
                });
                setMessage("密码已更新，请重新登录");
                setPassword("");
                setToken("");
              })
            }
          >
            更新密码并撤销旧会话
          </button>
        </div>
      )}
    </section>
  );
}

export function IdentityAdmin() {
  const [busy, setBusy] = useState(false);
  const [items, setItems] = useState<
      {
        id: string;
        name: string;
        issuer: string;
        enabled: boolean;
        client_id: string;
        allow_registration: boolean;
        allowed_domains: string[];
      }[]
    >([]),
    [open, setOpen] = useState(false),
    [error, setError] = useState("");
  const [name, setName] = useState(""),
    [issuer, setIssuer] = useState(""),
    [client, setClient] = useState(""),
    [secret, setSecret] = useState("");
  const [selected, setSelected] = useState(""),
    [enabled, setEnabled] = useState(true),
    [register, setRegister] = useState(false),
    [domains, setDomains] = useState("");
  const [channel, setChannel] = useState("email"),
    [host, setHost] = useState(""),
    [sender, setSender] = useState(""),
    [login, setLogin] = useState(""),
    [credential, setCredential] = useState("");
  const [channelRows, setChannelRows] = useState<
    { kind: string; name: string; enabled: boolean }[]
  >([]);
  const [providerSearch, setProviderSearch] = useState("");
  const [providerStatus, setProviderStatus] = useState("all");
  const visibleProviders = items.filter(item =>
    `${item.name} ${item.issuer}`.toLocaleLowerCase().includes(providerSearch.trim().toLocaleLowerCase()) &&
    (providerStatus === "all" || item.enabled === (providerStatus === "enabled")));
  const refresh = useCallback(async () => {
    setItems(
      (await api<{ items: typeof items }>("/admin/identity/providers")).items,
    );
    setChannelRows(
      (await api<{ items: typeof channelRows }>("/admin/identity/channels"))
        .items,
    );
  }, []);
  useEffect(() => {
    const timer = setTimeout(() => {
      void refresh().catch((e) => setError(e.message));
    }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);
  async function run(action: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section aria-label="企业认证管理">
      <h3>企业登录来源</h3>
      {error && <p role="alert">{error}</p>}
      <button
        onClick={() => {
          setSelected("");
          setName("");
          setIssuer("");
          setClient("");
          setSecret("");
          setRegister(false);
          setDomains("");
          setEnabled(true);
          setOpen(true);
        }}
      >
        新增认证来源
      </button>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12, margin: "16px 0" }}>
        <label style={{ display: "flex", flexDirection: "column", maxWidth: "100%" }}>搜索认证来源<input type="search" value={providerSearch} placeholder="名称或Issuer"
          onChange={event => setProviderSearch(event.target.value)} /></label>
        <label style={{ display: "flex", flexDirection: "column" }}>认证来源状态<select aria-label="认证来源状态" value={providerStatus} onChange={event => setProviderStatus(event.target.value)}>
          <option value="all">全部状态</option><option value="enabled">已启用</option><option value="disabled">已停用</option>
        </select></label>
      </div>
      {!visibleProviders.length && <p>{items.length ? "未找到符合条件的认证来源" : "暂无认证来源，请先新增"}</p>}
      <ul aria-label="认证来源列表">
        {visibleProviders.map((i) => (
          <li key={i.id}>
            {i.name} · {i.enabled ? "已启用" : "已停用"} · {i.issuer}
            <button
              onClick={() => {
                setSelected(i.id);
                setName(i.name);
                setIssuer(i.issuer);
                setClient(i.client_id);
                setSecret("");
                setEnabled(i.enabled);
                setRegister(i.allow_registration);
                setDomains(i.allowed_domains.join(","));
                setOpen(true);
              }}
            >
              编辑{i.name}
            </button>
            <button
              onClick={() =>
                void run(async () => {
                  await api(`/admin/identity/providers/${i.id}`, {
                    method: "PUT",
                    body: JSON.stringify({
                      ...i,
                      enabled: !i.enabled,
                      id: undefined,
                      revision: undefined,
                      has_secret: undefined,
                    }),
                  });
                })
              }
            >
              {i.enabled ? "停用" : "启用"}
              {i.name}
            </button>
          </li>
        ))}
      </ul>
      {open && (
        <ResourceDialog
          title={selected ? "编辑认证来源" : "新增认证来源"}
          busy={busy}
          close={() => setOpen(false)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run(async () => {
                await api(
                  `/admin/identity/providers/${selected || crypto.randomUUID()}`,
                  {
                    method: "PUT",
                    body: JSON.stringify({
                      name,
                      issuer,
                      client_id: client,
                      secret,
                      enabled,
                      allow_registration: register,
                      allowed_domains: domains
                        .split(",")
                        .map((v) => v.trim())
                        .filter(Boolean),
                    }),
                  },
                );
                setOpen(false);
                setSecret("");
              });
            }}
          >
            <label>
              显示名称
              <input
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </label>
            <label>
              Issuer地址
              <input
                required
                value={issuer}
                disabled={Boolean(selected)}
                onChange={(e) => setIssuer(e.target.value)}
              />
              <small>创建后不可更改；更换身份服务请新建来源。</small>
            </label>
            <label>
              Client ID
              <input
                required
                value={client}
                onChange={(e) => setClient(e.target.value)}
              />
            </label>
            <label>
              客户端密钥
              <input
                type="password"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
            </label>
            <p>新企业身份不会按邮箱自动合并或获得管理员权限。</p>
            <label>
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              启用来源
            </label>
            <label>
              <input
                type="checkbox"
                checked={register}
                onChange={(e) => setRegister(e.target.checked)}
              />
              允许企业身份创建普通账号
            </label>
            <label>
              允许的邮箱域名（逗号分隔）
              <input
                value={domains}
                onChange={(e) => setDomains(e.target.value)}
              />
            </label>
            <button disabled={busy}>保存认证来源</button>
          </form>
        </ResourceDialog>
      )}
      <h3>找回通道</h3>
      <ul>
        {channelRows.map((c) => (
          <li key={c.kind}>
            {c.name} · {c.enabled ? "已启用" : "未启用"}
            <button
              onClick={() =>
                void run(async () => {
                  await api(`/admin/identity/channels/${c.kind}`, {
                    method: "PUT",
                    body: JSON.stringify({ name: c.name, enabled: !c.enabled }),
                  });
                })
              }
            >
              {c.enabled ? "停用" : "启用"}
              {c.name}
            </button>
          </li>
        ))}
      </ul>
      <label>
        渠道
        <select
          value={channel}
          onChange={(e) => setChannel(e.target.value)}
        >
          <option value="email">SMTP邮件</option>
          <option value="sms">签名短信Webhook</option>
        </select>
      </label>
      <label>
        {channel === "email"
          ? "SMTP主机（端口587，STARTTLS）"
          : "短信Webhook地址"}
        <input
          value={host}
          onChange={(e) => setHost(e.target.value)}
        />
      </label>
      {channel === "email" && (
        <>
          <label>
            发件邮箱
            <input
              value={sender}
              onChange={(e) => setSender(e.target.value)}
            />
          </label>
          <label>
            SMTP账号
            <input
              value={login}
              onChange={(e) => setLogin(e.target.value)}
            />
          </label>
        </>
      )}
      <label>
        {channel === "email" ? "SMTP密码" : "签名密钥"}
        <input
          type="password"
          value={credential}
          placeholder="留空保留已保存的凭证"
          onChange={(e) => setCredential(e.target.value)}
        />
      </label>
      <button
        onClick={() =>
          void run(async () => {
            await api(`/admin/identity/channels/${channel}`, {
              method: "PUT",
              body: JSON.stringify({
                name: channel === "email" ? "邮件找回" : "短信找回",
                enabled: false,
                clear_credential: true,
              }),
            });
            setCredential("");
          })
        }
      >
        停用并清除该渠道凭证
      </button>
      <button
        onClick={() =>
          void run(async () => {
            const configuration = JSON.stringify(
              channel === "email"
                ? {
                    host,
                    port: 587,
                    sender,
                    username: login,
                    password: credential,
                    starttls: true,
                  }
                : { url: host, secret: credential },
            );
            await api(`/admin/identity/channels/${channel}`, {
              method: "PUT",
              body: JSON.stringify({
                name: channel === "email" ? "邮件找回" : "短信找回",
                configuration,
                enabled: true,
              }),
            });
            setCredential("");
          })
        }
      >
        保存并启用找回通道
      </button>
    </section>
  );
}
