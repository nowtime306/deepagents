"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Plus, RefreshCw } from "lucide-react";
import { api } from "@/lib/api/platform";
import { ResourceDialog } from "../ask/resource-dialog";
import "./browser-skills.css";

type Parameter = { name: string; description: string; required?: boolean };
type Step = { id: string; op: string; url?: string; target?: { role: string; name: string; ctx?: string }; parameter?: string; blocked: string[] };
type Recording = { id: string; title: string; description: string; status: string; revision: number;
  draft: { steps: Step[]; parameters: Parameter[] }; updated_at?: string; error_code?: string | null; skill_id?: string | null; skill_version?: number | null };
type Connection = { ready: boolean; code: string; message: string; connections: { id: string; name: string; version: string; busy: boolean }[] };
const active = (status: string) => ["starting", "recording", "stopping"].includes(status);
const labels: Record<string, string> = { starting: "正在建立录制", recording: "正在录制", stopping: "正在结束与整理", review: "待检查与发布", published: "已发布技能", cancelled: "已取消", failed: "录制未完成" };
const actions: Record<string, string> = { navigate: "打开页面", click: "点击", fill: "填写", select: "选择" };

export function BrowserSkills({ isAdmin, canManage, onChanged, onDirty, onOpenSkill }: { isAdmin: boolean; canManage: boolean; onChanged: () => Promise<void>; onDirty: (dirty: boolean) => void; onOpenSkill: (id: string) => void }) {
  const [items, setItems] = useState<Recording[]>([]), [connection, setConnection] = useState<Connection | null>(null);
  const [selected, setSelected] = useState<Recording | null>(null), [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false), [title, setTitle] = useState(""), [url, setUrl] = useState("");
  const [loading, setLoading] = useState(true), [busy, setBusy] = useState(false), [more, setMore] = useState(false);
  const [error, setError] = useState(""), [notice, setNotice] = useState("");
  const [detailDirty, setDetailDirty] = useState(false);
  useEffect(() => { onDirty(detailDirty || creating && !!(title || url)); }, [detailDirty, creating, title, url, onDirty]);
  const live = useRef(false), selectedRef = useRef<string | null>(null), readSequence = useRef(0), detailSequence = useRef(0);
  const reload = useCallback(async (offset = 0) => {
    const sequence = ++readSequence.current;
    const [page, status] = await Promise.all([api<{ items: Recording[] }>(`/browser-skills/recordings?limit=30&offset=${offset}`),
      isAdmin ? api<Connection>("/browser-skills/status") : Promise.resolve(null)]);
    if (!live.current || sequence !== readSequence.current) return;
    setItems(previous => offset ? [...previous, ...page.items.filter(item => !previous.some(old => old.id === item.id))] : page.items);
    setMore(page.items.length === 30); setConnection(status);
  }, [isAdmin]);
  const loadRecord = useCallback(async (id: string) => {
    const sequence = ++detailSequence.current;
    const row = await api<Recording>(`/browser-skills/recordings/${id}`);
    if (live.current && selectedRef.current === id && sequence === detailSequence.current) setSelected(previous => previous?.id === row.id && previous.revision > row.revision ? previous : row);
  }, []);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => { void reload().catch(reason => { if (live.current) setError(reason.message); }).finally(() => { if (live.current) setLoading(false); }); }, [reload]);
  const hasActive = items.some(item => active(item.status)) || !!selected && active(selected.status);
  useEffect(() => {
    if (!hasActive || busy) return;
    const timer = setInterval(() => {
      void reload().then(() => selectedRef.current ? loadRecord(selectedRef.current) : undefined)
        .catch(reason => { if (live.current) setError(reason.message); });
    }, 2000);
    return () => clearInterval(timer);
  }, [hasActive, busy, reload, loadRecord]);
  async function open(id: string) {
    selectedRef.current = id; setSelectedId(id); setSelected(null); setError("");
    try { await loadRecord(id); } catch (reason) { if (live.current && selectedRef.current === id) setError((reason as Error).message); }
  }
  async function change(operation: () => Promise<string | null>, message: string): Promise<boolean> {
    setBusy(true); setError(""); setNotice(""); let committed = false;
    try {
      const id = await operation(); committed = true;
      if (!live.current) return true;
      setNotice(message);
      if (id) { selectedRef.current = id; setSelectedId(id); await loadRecord(id); }
      await reload(); await onChanged(); return true;
    } catch (reason) {
      if (live.current) setError(`${committed ? "操作已完成，但刷新失败，请刷新记录；不要重复提交。" : ""}${(reason as Error).message}`);
      return committed;
    } finally { if (live.current) setBusy(false); }
  }
  return <section className="browser-skills" aria-label="浏览器录制管理">
    <header><div><h2>浏览器录制</h2><p>录制一遍操作路径，检查参数后生成可复用的私有 Skill。</p></div><div>
      <button type="button" disabled={busy} onClick={() => void reload().catch(reason => setError(reason.message))}><RefreshCw size={14}/>刷新连接与列表</button>
      {canManage && isAdmin && <button className="settings-primary" type="button" disabled={busy || !connection?.ready || connection.connections.some(item => item.busy)} onClick={() => { if (detailDirty && !window.confirm("当前参数修改尚未保存，放弃修改并新增录制？")) return; selectedRef.current = null; setSelectedId(null); setSelected(null); setDetailDirty(false); setTitle(""); setUrl(""); setCreating(true); }}><Plus size={14}/>新增浏览器录制</button>}
    </div></header>
    <p className="browser-skills-help">本机录制与执行仅主机管理员可用。使用专用浏览器窗口；发布只创建技能，不会自动执行网页操作。任务执行还须单独授权本机浏览器并批准完整路径。</p>
    {isAdmin && <p role="status">{connection?.message || (loading ? "正在检查连接…" : "连接状态暂不可用")}</p>}
    {error && <p className="platform-error" role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    {selectedId ? <><button type="button" disabled={busy} onClick={() => { if (detailDirty && !window.confirm("参数修改尚未保存，放弃这些修改并返回列表？")) return; selectedRef.current = null; setSelectedId(null); setSelected(null); setDetailDirty(false); }}>返回录制列表</button>
      {!selected ? <p>正在读取录制详情。<button type="button" onClick={() => void loadRecord(selectedId).catch(reason => setError(reason.message))}>重新读取详情</button></p>
        : <RecordingDetail key={`${selected.id}:${selected.revision}`} row={selected} busy={busy} canManage={canManage} isAdmin={isAdmin} onDirty={setDetailDirty} onOpenSkill={onOpenSkill}
          save={body => change(async () => { await api(`/browser-skills/recordings/${selected.id}/draft`, { method: "PUT", body: JSON.stringify(body) }); return selected.id; }, "参数草稿已保存，请检查完整路径后发布。")}
          control={operation => void change(async () => { await api(`/browser-skills/recordings/${selected.id}/${operation}`, { method: "POST" }); return selected.id; }, operation === "stop" ? "已请求结束录制，正在整理安全步骤。" : "已请求取消录制。")}
          publish={() => void change(async () => { await api(`/browser-skills/recordings/${selected.id}/publish`, { method: "POST", body: JSON.stringify({ revision: selected.revision }) }); return selected.id; }, "私有技能已发布。请在智能体资源中选择并按原流程发布版本。")}
          remove={() => void change(async () => { await api(`/browser-skills/recordings/${selected.id}`, { method: "DELETE", body: JSON.stringify({ revision: selected.revision }) }); selectedRef.current = null; setSelectedId(null); setSelected(null); return null; }, "录制记录已删除；已发布技能保留，可在原技能列表管理。")}/>}</>
      : <div className="browser-recording-list">{loading && <p>正在加载录制…</p>}{!loading && !items.length && <p>还没有录制记录。连接专用浏览器后，点击新增开始。</p>}
        {items.map(item => <button type="button" key={item.id} className="browser-recording-row" onClick={() => void open(item.id)}><div><strong>{item.title}</strong><small>{item.draft?.steps?.find(step => step.url)?.url || "站点待采集"} · {item.draft?.steps?.length || 0} 步</small></div><span>{labels[item.skill_id ? "published" : item.status] || "状态未知"}</span><small>修订 {item.revision}{item.skill_id && item.skill_version ? ` · Skill v${item.skill_version}` : ""}{item.updated_at && <span>{new Date(item.updated_at).toLocaleString("zh-CN")}</span>}</small></button>)}
        {more && <button type="button" disabled={busy} onClick={() => void reload(items.length).catch(reason => setError(reason.message))}>加载更多录制</button>}</div>}
    {creating && <ResourceDialog title="新增浏览器录制" busy={busy} close={() => setCreating(false)}><form onSubmit={event => {
      event.preventDefault(); void change(async () => { const row = await api<{ id: string }>("/browser-skills/recordings", { method: "POST", body: JSON.stringify({ connection_id: "local", start_url: url.trim(), title: title.trim() }) }); setCreating(false); return row.id; }, "录制已发起，等待专用窗口确认后才会显示正在录制。");
    }}><label>录制名称<input aria-label="录制名称" required maxLength={80} value={title} onChange={event => setTitle(event.target.value)}/></label>
      <label>起始网址<input aria-label="录制起始网址" type="url" required value={url} onChange={event => setUrl(event.target.value)} placeholder="https://example.com"/></label>
      <p>使用不含账号、令牌和查询参数的网址。在专用窗口完成操作后点击结束；普通输入将转换为参数，密码、验证码或不支持的动作会阻止发布。</p>
      <button className="settings-primary" disabled={busy || !title.trim() || !url.trim()}>打开专用窗口并录制</button></form></ResourceDialog>}
  </section>;
}

function RecordingDetail({ row, busy, canManage, isAdmin, save, control, publish, remove, onDirty, onOpenSkill }: {
  row: Recording; busy: boolean; canManage: boolean; isAdmin: boolean;
  save: (body: object) => Promise<boolean>; control: (operation: "stop" | "cancel") => void; publish: () => void; remove: () => void;
  onDirty: (dirty: boolean) => void;
  onOpenSkill: (id: string) => void;
}) {
  const [title, setTitle] = useState(row.title), [description, setDescription] = useState(row.description || "");
  const [parameters, setParameters] = useState(row.draft?.parameters || []);
  const steps = row.draft?.steps || [], blocked = steps.some(step => step.blocked?.length);
  const dirty = title !== row.title || description !== (row.description || "") || JSON.stringify(parameters) !== JSON.stringify(row.draft?.parameters || []);
  useEffect(() => { onDirty(dirty); }, [dirty, onDirty]);
  return <article className="browser-recording-detail"><header><h3>{row.title}</h3><span>{labels[row.skill_id ? "published" : row.status] || "状态未知"} · 修订 {row.revision}</span></header>
    {row.status === "starting" && <p>正在等待录制确认，还没有开始采集。请检查专用浏览器扩展的提示。</p>}
    {row.status === "recording" && <p>在专用窗口操作页面。结束后会整理步骤，不会立即发布或自动重放。</p>}
    {row.status === "failed" && <p>录制未完成。请检查连接后新建录制；系统不会自动重录或重放未知操作。</p>}
    {active(row.status) && canManage && isAdmin && <div className="browser-recording-actions"><button disabled={busy || row.status !== "recording"} onClick={() => control("stop")}>结束并整理</button><button disabled={busy} onClick={() => control("cancel")}>取消录制</button></div>}
    {!!steps.length && <><h4>完整操作路径（{steps.length} 步）</h4><ol>{steps.map(step => <li key={step.id}><strong>{actions[step.op] || `未支持动作：${step.op}`}</strong><span>{step.url || [step.target?.role, step.target?.name, step.target?.ctx].filter(Boolean).join(" · ")}</span>{step.parameter && <code>参数：{step.parameter}</code>}{step.blocked?.length > 0 && <p className="platform-error">此步骤阻止发布：{step.blocked.join("、")}</p>}</li>)}</ol></>}
    {row.status === "review" && !row.skill_id && <form onSubmit={event => { event.preventDefault(); void save({ revision: row.revision, title, description, parameters: parameters.map(({ name, description: note }) => ({ name, description: note })) }); }}>
      <fieldset disabled={busy || !canManage}><label>技能用途名称<input aria-label="录制草稿名称" required maxLength={80} value={title} onChange={event => setTitle(event.target.value)}/></label>
        <label>使用场景<textarea aria-label="录制使用场景" required value={description} maxLength={300} onChange={event => setDescription(event.target.value)}/></label>
        <h4>输入参数</h4><p>不保留录制时的原始输入值。运行时由当前任务明确提供，网页标签只作为定位数据。</p>
        {parameters.map((parameter, index) => <div className="browser-recording-parameter" key={index}><label>参数名<input aria-label={`参数 ${index + 1} 名称`} required pattern="[a-z][a-z0-9_]{0,39}" maxLength={40} value={parameter.name} onChange={event => setParameters(previous => previous.map((item, at) => at === index ? { ...item, name: event.target.value } : item))}/></label>
          <label>参数说明<input aria-label={`参数 ${index + 1} 说明`} required maxLength={200} value={parameter.description} onChange={event => setParameters(previous => previous.map((item, at) => at === index ? { ...item, description: event.target.value } : item))}/></label></div>)}
        <button type="submit" disabled={!dirty}>保存参数草稿</button></fieldset>
      {canManage && <button type="button" className="settings-primary" disabled={busy || dirty || blocked || !steps.length} onClick={publish}>检查完成，发布私有技能</button>}
      {dirty && <p>先保存当前修改后再发布。</p>}{blocked && <p>存在敏感或不支持的步骤。请重新录制支持的路径；不能删除阻断标记强行发布。</p>}
    </form>}
    {row.skill_id && <p>已生成 Skill v{row.skill_version}，在技能库选择后可供智能体读取；实际重放须在已授权的任务中审批。<button type="button" onClick={() => onOpenSkill(row.skill_id!)}>查看已发布技能</button></p>}
    {canManage && !active(row.status) && <button type="button" disabled={busy} onClick={() => { if (window.confirm("删除这条录制记录？已发布技能会保留。")) remove(); }}>删除录制记录</button>}
  </article>;
}
