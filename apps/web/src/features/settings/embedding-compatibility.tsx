"use client";

import { useState } from "react";
import { api } from "@/lib/api/platform";

export function EmbeddingCompatibility({ modelId, model, revision, baseUrl }: {
  modelId: string; model: string; revision: number; baseUrl: string;
}) {
  const [previous, setPrevious] = useState(""), [dimensions, setDimensions] = useState("");
  const [evidence, setEvidence] = useState(""), [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState("");
  return <details className="configuration-history"><summary>地址迁移兼容核对</summary>
    <form className="configuration-history-content" onSubmit={async (event) => {
      event.preventDefault(); if (!confirmed || busy) return;
      setBusy(true); setMessage("");
      try {
        const result = await api<{ note: string }>(`/capabilities/embedding-compatibility/${encodeURIComponent(modelId)}/verify`,
          { method: "POST", body: JSON.stringify({ previous_base_url: previous, confirmed_model: model, dimensions: Number(dimensions), evidence,
            expected_revision: revision, expected_base_url: baseUrl }) });
        setMessage(result.note); setConfirmed(false);
      } catch (reason) { setMessage((reason as Error).message); }
      finally { setBusy(false); }
    }}>
      <p>仅用于同一实际模型迁移服务地址。维度相同不足以证明兼容；这是管理员根据证据核对，不会自动调用服务商做验证。</p>
      <p>当前模型：{model}。未知旧索引仍需重建，不会补造历史身份。</p>
      <p>当前服务地址：{baseUrl} · 修订 {revision}</p>
      <label>迁移前 API 基础地址<input type="url" required value={previous} onChange={(event) => setPrevious(event.target.value)} maxLength={2000}/></label>
      <label>已核对输出维度<input type="number" min={1} max={8192} required value={dimensions} onChange={(event) => setDimensions(event.target.value)}/></label>
      <label>核对依据（至少20字，不含密钥）<textarea required minLength={20} maxLength={2000} value={evidence} onChange={(event) => setEvidence(event.target.value)}/></label>
      <label><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)}/>我已核对两侧实际模型及版本、预处理方式、输出和维度兼容</label>
      <button type="submit" disabled={busy || !confirmed}>{busy ? "正在保存证据摘要…" : "确认兼容并保存摘要"}</button>
      {message && <p role="status">{message}</p>}
    </form>
  </details>;
}
