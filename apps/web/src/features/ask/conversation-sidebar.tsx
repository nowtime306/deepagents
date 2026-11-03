"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import * as Dialog from "@radix-ui/react-dialog";
import {
  Folder,
  FolderPlus,
  MoreHorizontal,
  Pin,
  Search,
  X,
} from "lucide-react";
import { api } from "@/lib/api/platform";

type Conversation = {
  id: string;
  title: string;
  archived?: boolean;
  pinned?: boolean;
  project_id?: string | null;
};
type Project = { id: string; name: string; conversations: number };
type Page = { items: Conversation[]; next_cursor?: string | null };
type Action = {
  kind:
    | "project-create"
    | "project-rename"
    | "project-delete"
    | "rename"
    | "move"
    | "delete";
  id?: string;
  name?: string;
  projectId?: string | null;
};

export function ConversationSidebar({
  revision,
  archived,
  onArchivedChange,
  selected,
  onOpen,
  onRemoved,
  projectId,
  onProjectChange,
  canManage = true,
}: {
  revision: number;
  archived: boolean;
  onArchivedChange: (value: boolean) => void;
  selected: string | null;
  onOpen: (id: string) => void;
  onRemoved: (id: string) => void;
  projectId: string;
  onProjectChange: (id: string) => void;
  canManage?: boolean;
}) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [items, setItems] = useState<Conversation[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [menu, setMenu] = useState<string | null>(null);
  const generation = useRef(0);
  const menuRoot = useRef<HTMLDivElement>(null);
  const project = projects.find((item) => item.id === projectId);

  const url = useCallback(
    (before?: string) => {
      const params = new URLSearchParams({
        archived: String(archived),
        q: query,
      });
      if (projectId === "unassigned") params.set("unassigned", "true");
      else if (projectId) params.set("project_id", projectId);
      if (before) params.set("before", before);
      return `/conversations?${params}`;
    },
    [archived, query, projectId],
  );

  const reload = useCallback(async () => {
    const version = ++generation.current;
    const [list, groups] = await Promise.all([
      api<Page>(url()),
      api<{ items: Project[] }>("/conversation-projects"),
    ]);
    if (version !== generation.current) return;
    setItems(list.items);
    setCursor(list.next_cursor || null);
    setProjects(groups.items || []);
    setLoading(false);
    setError("");
  }, [url]);

  useEffect(() => {
    let active = true;
    reload().catch((reason) => {
      if (active) {
        setError(reason.message);
        setLoading(false);
      }
    });
    return () => {
      active = false;
    };
  }, [reload, revision]);

  useEffect(() => {
    if (!menu) return;
    function dismiss(event: PointerEvent) {
      if (!menuRoot.current?.contains(event.target as Node)) setMenu(null);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape") setMenu(null);
    }
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [menu]);

  async function perform(operation: () => Promise<unknown>) {
    setBusy(true);
    setMenu(null);
    setError("");
    try {
      await operation();
      await reload();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function loadMore() {
    if (!cursor || loading) return;
    const version = generation.current;
    setLoading(true);
    try {
      const data = await api<Page>(url(cursor));
      if (version !== generation.current) return;
      setItems((current) => [
        ...current,
        ...data.items.filter(
          (item) => !current.some((old) => old.id === item.id),
        ),
      ]);
      setCursor(data.next_cursor || null);
    } catch (reason) {
      if (version === generation.current) setError((reason as Error).message);
    } finally {
      if (version === generation.current) setLoading(false);
    }
  }

  async function saveAction(name: string, destination: string) {
    if (!action) return;
    if (action.kind === "project-create") {
      const created = await api<Project>("/conversation-projects", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      onProjectChange(created.id);
    } else if (action.kind === "project-rename") {
      await api(`/conversation-projects/${action.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
    } else if (action.kind === "project-delete") {
      await api(`/conversation-projects/${action.id}`, { method: "DELETE" });
      if (projectId === action.id) onProjectChange("");
    } else if (action.kind === "rename") {
      await api(`/conversations/${action.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: name }),
      });
    } else if (action.kind === "move") {
      await api(`/conversations/${action.id}/workspace`, {
        method: "PATCH",
        body: JSON.stringify({ project_id: destination || null }),
      });
    } else {
      await api(`/conversations/${action.id}`, { method: "DELETE" });
      onRemoved(action.id!);
    }
    setAction(null);
    // A deleted/currently changed project is refreshed by the filter effect.
    if (action.kind !== "project-delete" && action.kind !== "project-create") {
      await reload().catch((reason) => {
        setError(reason.message);
        setLoading(false);
      });
    }
  }

  function chooseProject(id: string) {
    if (id === projectId) return;
    setLoading(true);
    setMenu(null);
    onProjectChange(id);
  }

  return (
    <div className="conversation-workspace">
      <form
        className="sidebar-search"
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setLoading(true);
          setQuery(search.trim());
          if (search.trim() === query)
            void reload().catch((reason) => {
              setError(reason.message);
              setLoading(false);
            });
        }}
      >
        <button
          type="submit"
          aria-label="搜索对话"
        >
          <Search size={16} />
        </button>
        <input
          aria-label="搜索对话内容"
          placeholder="搜索对话"
          value={search}
          maxLength={200}
          onChange={(event) => setSearch(event.target.value)}
        />
        {(search || query) && (
          <button
            type="button"
            aria-label="清除对话搜索"
            onClick={() => {
              setSearch("");
              setQuery("");
            }}
          >
            <X size={14} />
          </button>
        )}
      </form>
      <div className="sidebar-label">
        <span>项目</span>
        <button
          type="button"
          aria-label="新建项目"
          disabled={!canManage}
          onClick={() => setAction({ kind: "project-create" })}
        >
          <FolderPlus size={15} />
        </button>
      </div>
      <div className="sidebar-projects">
        <button
          type="button"
          className={!projectId ? "selected" : ""}
          onClick={() => chooseProject("")}
        >
          全部对话
        </button>
        <button
          type="button"
          className={projectId === "unassigned" ? "selected" : ""}
          onClick={() => chooseProject("unassigned")}
        >
          未分组
        </button>
        {projects.map((item) => (
          <button
            key={item.id}
            type="button"
            className={projectId === item.id ? "selected" : ""}
            title={item.name}
            onClick={() => chooseProject(item.id)}
          >
            <Folder size={14} />
            <span>{item.name}</span>
            <small>{item.conversations}</small>
          </button>
        ))}
      </div>
      {project && canManage && (
        <div className="project-actions">
          <button
            onClick={() =>
              setAction({
                kind: "project-rename",
                id: project.id,
                name: project.name,
              })
            }
          >
            重命名项目
          </button>
          <button
            onClick={() =>
              setAction({
                kind: "project-delete",
                id: project.id,
                name: project.name,
              })
            }
          >
            删除项目
          </button>
        </div>
      )}
      <div className="sidebar-label">
        <span>{archived ? "归档对话" : project?.name || "最近对话"}</span>
        <button
          type="button"
          disabled={busy}
          onClick={() => {
            setLoading(true);
            onArchivedChange(!archived);
          }}
        >
          {archived ? "查看最近对话" : "查看已归档"}
        </button>
      </div>
      {error && (
        <div
          className="sidebar-error"
          role="alert"
        >
          {error}
          <button
            type="button"
            onClick={() =>
              void reload().catch((reason) => setError(reason.message))
            }
          >
            重试
          </button>
        </div>
      )}
      <div
        className="conversation-list"
        aria-busy={loading}
      >
        {items.map((item) => (
          <div
            key={item.id}
            className={`conversation ${selected === item.id ? "selected" : ""}`}
          >
            <button
              title={item.title}
              onClick={() => onOpen(item.id)}
            >
              {item.pinned && (
                <Pin
                  size={12}
                  aria-label="已置顶"
                />
              )}
              <span>{item.title}</span>
            </button>
            <div
              ref={menu === item.id ? menuRoot : undefined}
              className="conversation-actions"
            >
              <button
                type="button"
                aria-label={`对话操作 ${item.title}`}
                aria-expanded={menu === item.id}
                disabled={busy || !canManage}
                onClick={() => setMenu(menu === item.id ? null : item.id)}
              >
                <MoreHorizontal size={17} />
              </button>
              {menu === item.id && (
                <div
                  className="conversation-menu"
                  aria-label={`${item.title}的操作`}
                >
                  <button
                    onClick={() => {
                      setMenu(null);
                      setAction({
                        kind: "rename",
                        id: item.id,
                        name: item.title,
                      });
                    }}
                  >
                    重命名 {item.title}
                  </button>
                  <button
                    onClick={() =>
                      void perform(() =>
                        api(`/conversations/${item.id}/workspace`, {
                          method: "PATCH",
                          body: JSON.stringify({ pinned: !item.pinned }),
                        }),
                      )
                    }
                  >
                    {item.pinned ? "取消置顶" : "置顶对话"}
                  </button>
                  <button
                    onClick={() => {
                      setMenu(null);
                      setAction({
                        kind: "move",
                        id: item.id,
                        name: item.title,
                        projectId: item.project_id,
                      });
                    }}
                  >
                    移动到项目
                  </button>
                  <button
                    aria-label={`${item.archived ? "恢复" : "归档"} ${item.title}`}
                    onClick={() =>
                      void perform(async () => {
                        await api(`/conversations/${item.id}/archive`, {
                          method: "POST",
                          body: JSON.stringify({ archived: !item.archived }),
                        });
                        onRemoved(item.id);
                      })
                    }
                  >
                    {item.archived ? "恢复对话" : "归档对话"}
                  </button>
                  <button
                    className="danger"
                    onClick={() => {
                      setMenu(null);
                      setAction({
                        kind: "delete",
                        id: item.id,
                        name: item.title,
                      });
                    }}
                  >
                    删除对话
                  </button>
                </div>
              )}
            </div>
          </div>
        ))}
        {!items.length && (
          <p className="sidebar-empty">
            {loading
              ? "正在加载对话…"
              : query
                ? "没有匹配的对话，试试其他关键词"
                : archived
                  ? "还没有归档对话"
                  : "开始一段对话，记录你的灵感"}
          </p>
        )}
        {cursor && (
          <button
            type="button"
            disabled={loading}
            onClick={() => void loadMore()}
          >
            加载更多对话
          </button>
        )}
      </div>
      {action && (
        <WorkspaceDialog
          key={`${action.kind}-${action.id || "new"}`}
          action={action}
          projects={projects}
          onClose={() => setAction(null)}
          onSave={saveAction}
        />
      )}
    </div>
  );
}

function WorkspaceDialog({
  action,
  projects,
  onClose,
  onSave,
}: {
  action: Action;
  projects: Project[];
  onClose: () => void;
  onSave: (name: string, projectId: string) => Promise<void>;
}) {
  const [name, setName] = useState(action.name || "");
  const [projectId, setProjectId] = useState(action.projectId || "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const title = {
    "project-create": "新建项目",
    "project-rename": "重命名项目",
    "project-delete": "删除项目",
    rename: "重命名对话",
    move: "移动到项目",
    delete: "删除对话",
  }[action.kind];
  const removing = action.kind === "delete" || action.kind === "project-delete";
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await onSave(name.trim(), projectId);
    } catch (reason) {
      setError((reason as Error).message);
      setBusy(false);
    }
  }
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
          className="workspace-dialog"
          onEscapeKeyDown={(event) => {
            if (busy) event.preventDefault();
          }}
          onPointerDownOutside={(event) => {
            if (busy) event.preventDefault();
          }}
        >
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>
            {action.kind === "project-delete"
              ? `删除“${action.name}”后，会话将移至未分组，消息和附件保留。项目仅用于组织会话，不共享上下文或授权。`
              : action.kind === "delete"
                ? `确定删除“${action.name}”及其消息和附件？此操作无法撤销。`
                : "整理你的私有工作空间。项目分组不改变智能体权限或其他账号的数据。"}
          </Dialog.Description>
          <Dialog.Close
            className="workspace-dialog-close"
            aria-label="关闭弹窗"
            disabled={busy}
          >
            <X size={18} />
          </Dialog.Close>
          <form onSubmit={(event) => void submit(event)}>
            {action.kind === "move" ? (
              <label>
                所属项目
                <select
                  aria-label="所属项目"
                  value={projectId}
                  onChange={(event) => setProjectId(event.target.value)}
                >
                  <option value="">未分组</option>
                  {projects.map((item) => (
                    <option
                      key={item.id}
                      value={item.id}
                    >
                      {item.name}
                    </option>
                  ))}
                </select>
              </label>
            ) : (
              !removing && (
                <label>
                  {action.kind.startsWith("project-") ? "项目名称" : "对话名称"}
                  <input
                    aria-label={
                      action.kind.startsWith("project-")
                        ? "项目名称"
                        : "对话名称"
                    }
                    required
                    maxLength={action.kind.startsWith("project-") ? 80 : 120}
                    value={name}
                    onChange={(event) => setName(event.target.value)}
                  />
                </label>
              )
            )}
            {error && (
              <p
                role="alert"
                className="sidebar-error"
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
                className={removing ? "workspace-danger" : "workspace-primary"}
                disabled={
                  busy || (!removing && action.kind !== "move" && !name.trim())
                }
              >
                {busy ? "正在保存…" : removing ? "确认删除" : "保存"}
              </button>
            </footer>
          </form>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
