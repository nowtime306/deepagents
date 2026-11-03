"use client";

import { FormEvent, useState } from "react";
import { api } from "@/lib/api/platform";

export function ToolAuthorization({ id, isAdmin }: { id: string; isAdmin: boolean }) {
  const [client, setClient] = useState(""), [secret, setSecret] = useState(""), [scopes, setScopes] = useState("");
  const [clear, setClear] = useState(false), [error, setError] = useState("");
  const [configured, setConfigured] = useState(false), [expires, setExpires] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [loaded, setLoaded] = useState(false);
  async function load() {
    const state = await api<{ configured: boolean; grant: { expires_at: string } | null }>(`/tool-oauth/${id}/status`);
    setConfigured(state.configured); setExpires(state.grant?.expires_at || null);
    if (isAdmin) {
      const config = await api<{ client_id: string; scopes: string }>(`/tool-oauth/${id}/client`);
      setClient(config.client_id); setScopes(config.scopes);
    }
    setLoaded(true);
  }
  async function action(fn: () => Promise<void>) {
    setError(""); setBusy(true);
    try { await fn(); } catch (reason) { setError((reason as Error).message); } finally { setBusy(false); }
  }
  function save(event: FormEvent) {
    event.preventDefault();
    void action(async () => {
      await api(`/tool-oauth/${id}/client`, { method: "PUT", body: JSON.stringify({ client_id: client,
        secret, scopes, clear_secret: clear }) });
      setSecret(""); setClear(false); await load();
    });
  }
  return <details onToggle={(event) => { if (event.currentTarget.open && !loaded) void action(load); }}>
    <summary>OAuth授权（本人账号）</summary>
    <p>{configured ? expires ? `本人授权到期：${new Date(expires).toLocaleString("zh-CN")}` : "尚未授权或授权已撤销" : "未配置OAuth，沿用连接原鉴权"}</p>
    {isAdmin && <form onSubmit={save} aria-label="工具OAuth客户端">
      <label>Client ID<input required maxLength={400} value={client} onChange={(e) => setClient(e.target.value)} /></label>
      <label>Client Secret<input type="password" autoComplete="new-password" disabled={clear} value={secret}
        placeholder="留空保留已保存密钥" onChange={(e) => setSecret(e.target.value)} /></label>
      <label>授权scope<input maxLength={2000} value={scopes} onChange={(e) => setScopes(e.target.value)} /></label>
      <label><input type="checkbox" checked={clear} onChange={(e) => setClear(e.target.checked)} />清除客户端密钥</label>
      <p>保存客户端配置会撤销旧授权；回调地址为当前站点 /api/v1/tool-oauth/callback。</p>
      <button disabled={busy}>保存OAuth客户端</button>
    </form>}
    <button disabled={busy || !configured} onClick={() => void action(async () => {
      const result = await api<{ authorization_url: string }>(`/tool-oauth/${id}/authorize`, { method: "POST" });
      window.location.assign(result.authorization_url);
    })}>授权 / 重新授权</button>
    <button disabled={busy || !expires} onClick={() => void action(async () => {
      await api(`/tool-oauth/${id}/grant`, { method: "DELETE" }); await load();
    })}>撤销本人授权</button>
    {error && <p role="alert">{error}<button disabled={busy} onClick={() => void action(load)}>重试读取</button></p>}
  </details>;
}
