"use client";

import { useState } from "react";
import { ResourceDialog } from "./resource-dialog";
import type { WorkflowDefinition, WorkflowLayout } from "./workflow-editor";
import type { InputSchema } from "./workflow-input-form";
import "./workflow-versions.css";

export type PublishedWorkflowVersion = {
  version: number; name: string; instructions?: string; model_id: string; draft_revision: number;
  active?: boolean; created_at?: string; input_schema: InputSchema; execution?: "readonly" | "work";
  definition: WorkflowDefinition; canvas_definition?: WorkflowDefinition | null;
  layout?: WorkflowLayout; layout_known?: boolean; schema_supported?: boolean;
};

export function WorkflowVersions({ items, current, preview, loading, error, busy, canManage, dirty,
  onPreview, onRestore, onCopy, onCurrent, onState, onEdit }: {
  items: PublishedWorkflowVersion[]; current: number | null; preview: PublishedWorkflowVersion | null;
  loading: boolean; error: string; busy: boolean; canManage: boolean; dirty: boolean;
  onPreview: (version: number) => void; onRestore: (version: number) => Promise<boolean>;
  onCopy: (version: number, name: string) => Promise<boolean>; onCurrent: (version: number) => void;
  onState: (version: number, active: boolean) => void; onEdit: () => void;
}) {
  const [dialog, setDialog] = useState<"restore" | "copy" | null>(null), [copyName, setCopyName] = useState("");
  const usable = !!preview && preview.active !== false && preview.schema_supported !== false;
  return <section className="workflow-version-panel" aria-label="工作流版本历史">
    <header><h2>版本历史</h2><button type="button" disabled={busy} onClick={onEdit}>返回编辑草稿</button></header>
    <p className="workflow-panel-help">{current ? `当前发布版 v${current}` : "尚未指定当前发布版"}。发布历史保持不变，恢复草稿与切换发布版是不同操作。</p>
    <div className="workflow-version-list">{!items.length && <p>还没有发布版本。整体测试通过后可发布第一个版本。</p>}
      {items.map(item => <button type="button" key={item.version} aria-label={`预览 v${item.version}`} aria-pressed={preview?.version === item.version}
        disabled={busy} onClick={() => onPreview(item.version)}><span><strong>v{item.version}</strong>{current === item.version && <b>当前发布版</b>}<small>{item.active === false ? "已下线" : "可运行"}</small></span>
        <small>来源修订 {item.draft_revision}{item.created_at ? ` · ${new Date(item.created_at).toLocaleString("zh-CN")}` : ""}</small></button>)}
    </div>
    {loading && <p role="status">正在读取历史快照…</p>}{error && <p role="alert" className="platform-error">{error}</p>}
    {preview && !loading && <div className="workflow-version-detail">
      <h3>v{preview.version} 配置预览</h3><p>名称：{preview.name}</p><p>模型：{preview.model_id}</p>
      {!preview.layout_known && <p>此版本未保存自定义布局，画布按默认位置展示。</p>}
      {preview.schema_supported === false && <p role="status">当前编辑器不支持该历史结构。原始快照保留，可查看配置，暂不能恢复、复制或运行。</p>}
      <details><summary>历史模型指令</summary><pre>{preview.instructions || "没有模型指令"}</pre></details>
      <details><summary>完整历史配置</summary><pre>{JSON.stringify(preview.definition, null, 2)}</pre></details>
      {canManage && <div className="workflow-version-actions">
        <button type="button" disabled={busy || !usable || current === preview.version} onClick={() => onCurrent(preview.version)}>设为当前发布版</button>
        <button type="button" disabled={busy || preview.schema_supported === false} onClick={() => setDialog("restore")}>恢复为草稿</button>
        <button type="button" disabled={busy || preview.schema_supported === false} onClick={() => { setCopyName(`${preview.name.slice(0, 75)} · v${preview.version} 副本`.slice(0, 100)); setDialog("copy"); }}>复制为新工作流</button>
        <button type="button" disabled={busy} onClick={() => onState(preview.version, preview.active === false)}>{preview.active === false ? "重新上线" : "下线版本"}</button>
      </div>}
    </div>}
    {dialog && preview && <ResourceDialog title={dialog === "restore" ? `恢复 v${preview.version} 为草稿` : `复制 v${preview.version}`} busy={busy} close={() => setDialog(null)}>
      {dialog === "restore" ? <><p>{dirty ? "先保存当前未保存修改，再" : "将"}把当前草稿另存为可访问的独立备份，然后恢复历史配置。新草稿须重新整体测试；当前发布版不变。</p>
        <p>保存或恢复失败时保留当前编辑，不自动重试。恢复成功会显示备份名称。</p>
        <button className="settings-primary" disabled={busy} onClick={() => void onRestore(preview.version).then(ok => { if (ok) setDialog(null); })}>备份当前草稿并恢复</button></>
        : <form onSubmit={event => { event.preventDefault(); void onCopy(preview.version, copyName.trim()).then(ok => { if (ok) setDialog(null); }); }}>
          <label>新工作流名称<input aria-label="副本工作流名称" required maxLength={100} value={copyName} disabled={busy} onChange={event => setCopyName(event.target.value)}/></label>
          <p>仅复制历史配置，不复制运行记录、API Key、共享权限或测试资格。当前未保存编辑会留在原页。</p>
          <button className="settings-primary" disabled={busy || !copyName.trim()}>创建独立副本</button></form>}
    </ResourceDialog>}
  </section>;
}
