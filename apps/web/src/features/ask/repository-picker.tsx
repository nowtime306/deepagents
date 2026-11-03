"use client";

import { usePagedResources } from "./use-paged-resources";

export type RepositoryPermissions = { read?: boolean; use?: boolean; publish?: boolean };

export function RepositoryPicker({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  const { items, cursor, loading, error, more, reload } = usePagedResources<{ id: string; name: string; slug: string }>("/repositories/options");
  return <label>代码仓库（可选）
    <select aria-label="任务代码仓库" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">不绑定仓库</option>
      {items.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.slug}</option>)}
    </select>
    <small>发起时固定源码版本。新增连接请打开“代码仓库”，访问令牌仅在服务端使用。</small>
    {cursor && <button type="button" disabled={loading} onClick={() => void more().catch(() => {})}>加载更多可选仓库</button>}
    {error && <span role="alert">{error}<button type="button" onClick={() => void (cursor ? more() : reload()).catch(() => {})}>重试读取仓库</button></span>}
  </label>;
}
