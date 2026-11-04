"use client";

import {
  createElement,
  FormEvent,
  Fragment,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import {
  ArrowUp,
  ArrowDown,
  Check,
  ChevronRight,
  SquareTerminal,
  Workflow,
  BookOpen,
  History,
  Bot,
  AudioLines,
  Image as ImageIcon,
  Settings,
  ChartNoAxesCombined,
  Users,
  Globe,
  PanelLeftClose,
  PanelLeftOpen,
  Paperclip,
  X,
  LogOut,
  MessageSquare,
  Plus,
  Square,
  Sparkles,
} from "lucide-react";
import { api, ApiError, followRun } from "@/lib/api/platform";
import { modes, statuses } from "@/locales/zh-CN/platform";
import { ModelSettings } from "@/features/settings/model-settings";
import { ModelRoutes } from "@/features/settings/model-routes";
import { CapabilityCenter } from "@/features/capabilities/capability-center";
import { RunHistory } from "./run-history";
import { AgentSettings, Agent } from "./agent-settings";
import { AgentVersionPicker } from "./agent-versions";
import { Workflows } from "./workflows";
import { Speech } from "./speech";
import { Vision } from "./vision";
import { WorkTasks } from "./work-tasks";
import { RepositorySettings } from "./repository-settings";
import { Operations } from "./operations";
import { MonitoringDashboard } from "./monitoring-dashboard";
import { IdentityAdmin, IdentityLogin } from "@/features/identity/identity-security";
import { ExtraCosts } from "./extra-costs";
import { Organizations } from "./organizations";
import type { Message } from "./chat-message";
import { RunSnapshotContext, type RunSnapshot } from "./run-snapshot";
import type { Usage } from "./token-usage";
import { ChatTranscript } from "./chat-transcript";
import { ConversationSidebar } from "./conversation-sidebar";
import { ConversationReferences } from "./conversation-references";
import { AccountAdmin } from "./account-admin";
import { Automations } from "./automations";
import { AccountProfile, type AccountIdentity } from "./account-profile";
import { DraftEstimate, TokenUsage } from "./token-usage";
import { useConversationUsage } from "./use-conversation-usage";
import { useChatScroll } from "./use-chat-scroll";
import "./platform.css";
import "./workspace-theme.css";

const navigationGroups = [
  { title: "工作空间", items: ["智能对话", "任务工作台", "代码仓库", "自动任务", "工作流", "运行记录"] },
  { title: "资源与能力", items: ["智能体管理", "能力中心", "语音合成", "图片理解"] },
  { title: "管理与配置", items: ["设置", "运营管理", "监测大盘", "组织管理", "账号管理"] },
];

const navigationIcons: Record<string, typeof MessageSquare> = {
  智能对话: MessageSquare,
  任务工作台: SquareTerminal,
  代码仓库: SquareTerminal,
  工作流: Workflow,
  能力中心: BookOpen,
  运行记录: History,
  智能体管理: Bot,
  语音合成: AudioLines,
  图片理解: ImageIcon,
  设置: Settings,
  运营管理: ChartNoAxesCombined,
  监测大盘: ChartNoAxesCombined,
  组织管理: Users,
  账号管理: Users,
  自动任务: History,
};

type Conversation = { id: string; title: string; archived?: boolean };
type Model = {
  id: string;
  name: string;
  supports_images?: boolean;
  native_search_mode?: string | null;
};
type Attachment = {
  id: string;
  name: string;
  size: number;
  media_type?: string;
};
type History = {
  saved_draft?: string;
  items: Message[];
  latest_run: { id: string; status: string } | null;
  agent_id?: string | null;
  agent_version_id?: string | null;
  next_before?: string | null;
  pending_attachments?: Attachment[];
  archived?: boolean;
  native_search?: boolean;
};

export function PlatformApp() {
  const [user, setUser] = useState<string | null | undefined>(undefined);
  const [isAdmin, setIsAdmin] = useState(false);
  const [permissions, setPermissions] = useState<string[] | undefined>();
  const [mustChangePassword, setMustChangePassword] = useState(false);
  const [error, setError] = useState("");
  const [archived, setArchived] = useState(false);
  const [selectedArchived, setSelectedArchived] = useState(false);
  const [conversationRevision, setConversationRevision] = useState(0);
  const [referenceRevision, setReferenceRevision] = useState(0);
  const [projectId, setProjectId] = useState("");
  const [messageCursor, setMessageCursor] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingConversation, setLoadingConversation] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [modelId, setModelId] = useState("");
  const [modelRouting, setModelRouting] = useState(false);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentId] = useState("");
  const [agentVersion, setAgentVersion] = useState("");
  const [versionModel, setVersionModel] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [stream, setStream] = useState("");
  const [run, setRun] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [runSnapshot, setRunSnapshot] = useState<RunSnapshot | null>(null);
  const snapshotContext = useMemo(() => ({ id: run, mode: "ask" as const, data: runSnapshot }), [run, runSnapshot]);
  const loadedUsage = useConversationUsage(selected, run, messages.length);
  const pushedUsage = runSnapshot?.usage as { runs: Usage[]; total: Usage } | undefined;
  const usage = pushedUsage ?? loadedUsage;
  const [section, setSection] = useState("智能对话");
  const [menuOpen, setMenuOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [nativeSearch, setNativeSearch] = useState(true);
  const composerInput = useRef<HTMLTextAreaElement>(null);
  const [status, setStatus] = useState("");
  const subscription = useRef<AbortController | null>(null);
  const viewVersion = useRef(0);
  const { scroll, content, away, reset: resetScroll, preserve: preserveScroll, latest } = useChatScroll(
    messages, stream, selected, !!user && !mustChangePassword && section === "智能对话",
  );

  const refreshConversations = useCallback(async () => {
    setConversationRevision((value) => value + 1);
  }, []);

  const refreshModels = useCallback(async () => {
    const data = await api<{ items: Model[]; vision_models?: Model[] }>(
      "/models",
    );
    setModels([...data.items, ...(data.vision_models || [])]);
    setModelId((current) =>
      data.items.some((model) => model.id === current)
        ? current
        : data.items[0]?.id || data.vision_models?.[0]?.id || "",
    );
  }, []);

  const refreshAgents = useCallback(async () => {
    const data = await api<{ items: Agent[] }>("/agents");
    setAgents(data.items || []);
  }, []);

  const acceptAccount = useCallback((account: AccountIdentity) => {
    setUser(account.username);
    setIsAdmin(!!account.is_admin);
    setPermissions(account.permissions);
    setMustChangePassword(!!account.must_change_password);
    if (account.must_change_password || (!account.is_admin && account.permissions && !account.permissions.includes("chat.read"))) setSection("个人设置");
  }, []);

  useEffect(() => {
    api<AccountIdentity>("/auth/me").then(acceptAccount).catch((reason) => {
      setUser(null);
      if (!(reason instanceof ApiError && reason.status === 401)) setError(reason.message);
    });
    return () => subscription.current?.abort();
  }, [acceptAccount]);

  function can(permission: string) {
    return isAdmin || permissions === undefined || permissions.includes(permission);
  }

  function signedOut() {
    newConversation(); setUser(null); setProjectId(""); setModels([]); setAgents([]);
    setPermissions(undefined); setMustChangePassword(false);
  }

  useEffect(() => {
    if (!user || mustChangePassword) return;
    refreshModels().catch((error) => setError(error.message));
    if (isAdmin || permissions === undefined || permissions.includes("agents.read")) refreshAgents().catch((error) => setError(error.message));
  }, [user, isAdmin, permissions, mustChangePassword, refreshModels, refreshAgents]);

  const watch = useCallback(async (runId: string, conversationId: string, version: number) => {
    subscription.current?.abort();
    const controller = new AbortController();
    subscription.current = controller;
    setRun(runId);
    setStream("");
    setRunSnapshot(null);
    setStatus("RUNNING");
    let following = true, reading = false, pending = false;
    let buffer = "", frame = 0;
    const discardBuffer = () => { cancelAnimationFrame(frame); frame = 0; buffer = ""; };
    controller.signal.addEventListener("abort", discardBuffer, { once: true });
    async function refreshProgress() {
      pending = true;
      if (reading) return;
      reading = true;
      try {
        while (pending && following && !controller.signal.aborted) {
          pending = false;
          const history = await api<History>(`/conversations/${conversationId}/messages`, { signal: controller.signal });
          if (following && !controller.signal.aborted && version === viewVersion.current) {
            setMessages(history.items); setMessageCursor(history.next_before || null);
          }
        }
      } catch { /* Final history reload remains authoritative when a progress read fails. */ }
      finally { reading = false; }
    }
    try {
      await followRun(
        runId,
        (event) => {
          if (version !== viewVersion.current) return;
          if (event.event === "stream.snapshot") setRunSnapshot(event.data);
          if (event.event === "message.delta") {
            buffer += event.data.text || "";
            if (!frame) frame = requestAnimationFrame(() => {
              frame = 0;
              const chunk = buffer; buffer = "";
              if (!controller.signal.aborted && version === viewVersion.current) setStream((text) => text + chunk);
            });
          }
          if (event.event === "message.completed") {
            if (event.data.role === "assistant") { discardBuffer(); setStream(""); }
            void refreshProgress();
          }
          if (event.event === "run.failed") {
            setStatus("FAILED");
            setError(String(event.data.message));
          }
          if (event.event === "run.completed") setStatus("SUCCEEDED");
          if (event.event === "run.cancelled") setStatus("CANCELLED");
        },
        controller.signal,
        reconnecting => { if (!controller.signal.aborted && version === viewVersion.current) setError(old => reconnecting ? "连接中断，正在重新连接…" : old === "连接中断，正在重新连接…" ? "" : old); },
      );
      following = false;
      discardBuffer();
      if (!controller.signal.aborted && version === viewVersion.current) {
        const history = await api<History>(
          `/conversations/${conversationId}/messages`,
        );
        if (version === viewVersion.current) {
          setMessages(history.items);
          setMessageCursor(history.next_before || null);
          setAttachments(history.pending_attachments || []);
          setStream("");
          setRun(null);
        }
      }
    } catch (error) {
      following = false;
      discardBuffer();
      if (!controller.signal.aborted && version === viewVersion.current) {
        setError((error as Error).message);
        setRun(null);
      }
    } finally { controller.signal.removeEventListener("abort", discardBuffer); }
  }, []);

  const openConversation = useCallback(async (id: string) => {
    setMenuOpen(false);
    const version = ++viewVersion.current;
    subscription.current?.abort();
    setRun(null);
    setStream("");
    setStatus("");
    setRunSnapshot(null);
    setSelected(id);
    resetScroll();
    setLoadingConversation(true);
    setMessages([]);
    setMessageCursor(null);
    setDraft("");
    setAttachments([]);
    setError("");
    try {
      const history = await api<History>(`/conversations/${id}/messages`);
      if (version !== viewVersion.current) return;
      setMessages(history.items);
      setMessageCursor(history.next_before || null);
      setSelectedArchived(!!history.archived);
      setDraft(history.saved_draft || "");
      setNativeSearch(history.native_search ?? true);
      setAttachments(history.pending_attachments || []);
      setAgentId(history.agent_id || "");
      setAgentVersion(history.agent_version_id || "");
      setStatus(history.latest_run?.status || "");
      if (
        history.latest_run &&
        ["PENDING", "RUNNING"].includes(history.latest_run.status)
      ) {
        void watch(history.latest_run.id, id, version);
      }
    } catch (error) {
      if (version === viewVersion.current) setError((error as Error).message);
    } finally {
      if (version === viewVersion.current) {
        setLoadingConversation(false);
        requestAnimationFrame(() => { if (version === viewVersion.current) composerInput.current?.focus(); });
      }
    }
  }, [watch, resetScroll]);

  async function send(event: FormEvent) {
    event.preventDefault();
    const hasImages = attachments.some((file) =>
      file.media_type?.startsWith("image/"),
    );
    if (
      !can("chat.run") ||
      (!draft.trim() && !hasImages) ||
      !effectiveModel ||
      busy ||
      run ||
      loadingConversation ||
      selectedArchived
    )
      return;
    const version = viewVersion.current;
    const text = draft.trim() || "请描述这张图片。";
    setError("");
    setBusy(true);
    try {
      let id = selected;
      if (!id) {
        const conversation = await api<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({
            title: text.slice(0, 32),
            agent_id: agentId || null,
            agent_version_id: agentVersion || null,
            project_id: projectId && projectId !== "unassigned" ? projectId : null,
          }),
        });
        id = conversation.id;
        if (version !== viewVersion.current) return;
        setRunSnapshot(null);
        setSelected(id);
        await refreshConversations();
      }
      const result = await api<{ run_id: string }>(
        `/conversations/${id}/runs`,
        {
          method: "POST",
          body: JSON.stringify({
            message: text,
            model_id: effectiveModel,
            model_routing: modelRouting,
            native_search: searchAvailable && nativeSearch,
            attachment_ids: attachments.map((file) => file.id),
          }),
        },
      );
      if (version !== viewVersion.current) return;
      resetScroll();
      setMessages((previous) => [
        ...previous,
        {
          id: result.run_id,
          run_id: result.run_id,
          role: "user",
          content: {
            text,
            attachments: attachments.map(({ id, name }) => ({ id, name })),
          },
        },
      ]);
      setDraft("");
      setAttachments([]);
      composerInput.current?.focus();
      void watch(result.run_id, id, version);
    } catch (error) {
      if (version === viewVersion.current) setError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }

  function newConversation() {
    setMenuOpen(false);
    ++viewVersion.current;
    subscription.current?.abort();
    resetScroll();
    setLoadingConversation(false);
    setDraft("");
    setRunSnapshot(null);
    setSelected(null);
    setSelectedArchived(false);
    setAttachments([]);
    setMessageCursor(null);
    setArchived(false);
    setAgentId("");
    setNativeSearch(true);
    setAgentVersion("");
    setVersionModel("");
    setMessages([]);
    setRun(null);
    setStream("");
    setStatus("");
    setError("");
    setSection("智能对话");
    const version = viewVersion.current;
    requestAnimationFrame(() => { if (version === viewVersion.current) composerInput.current?.focus(); });
  }

  async function uploadAttachment(input?: File | File[]) {
    const files = Array.isArray(input) ? input : input ? [input] : [];
    if (!can("chat.manage") || !files.length || busy || run || loadingConversation || selectedArchived) return;
    if (
      files.some((file) => file.size > 4 * 1024 * 1024) ||
      attachments.length + files.length > 5
    ) {
      setError("每次最多选择 5 个附件，单个文件不能超过 4 MB");
      return;
    }
    const version = viewVersion.current;
    setBusy(true);
    setError("");
    try {
      let id = selected;
      if (!id) {
        const item = await api<Conversation>("/conversations", {
          method: "POST",
          body: JSON.stringify({
            title: files[0].name.slice(0, 32),
            agent_id: agentId || null,
            agent_version_id: agentVersion || null,
            project_id: projectId && projectId !== "unassigned" ? projectId : null,
          }),
        });
        if (version !== viewVersion.current) return;
        id = item.id;
        setRunSnapshot(null);
        setSelected(id);
        await refreshConversations();
      }
      for (const file of files) {
        if (version !== viewVersion.current) return;
        const response = await fetch(
          `/api/v1/conversations/${id}/attachments?name=${encodeURIComponent(file.name)}`,
          { method: "POST", body: file },
        );
        const data = await response.json();
        if (!response.ok)
          throw new Error(data.error?.message || "附件上传失败");
        if (version === viewVersion.current)
          setAttachments((current) => [...current, data]);
      }
    } catch (reason) {
      if (version === viewVersion.current) setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function removeAttachment(id: string) {
    try {
      await api(`/attachments/${id}`, { method: "DELETE" });
      setAttachments((current) => current.filter((file) => file.id !== id));
    } catch (reason) {
      setError((reason as Error).message);
    }
  }

  async function startReferencedConversation() {
    if (!can("chat.manage") || busy) return;
    const version = viewVersion.current;
    setBusy(true);
    setError("");
    try {
      const item = await api<Conversation>("/conversations", {
        method: "POST", body: JSON.stringify({ title: "引用资料的新对话", agent_id: agentId || null,
          agent_version_id: agentVersion || null,
          project_id: projectId && projectId !== "unassigned" ? projectId : null }),
      });
      if (version !== viewVersion.current) return;
      setSelected(item.id);
      await refreshConversations();
    } catch (reason) {
      if (version === viewVersion.current) setError((reason as Error).message);
    } finally { setBusy(false); }
  }

  async function loadOlderMessages() {
    if (!selected || !messageCursor || loadingHistory) return;
    const version = viewVersion.current;
    setLoadingHistory(true);
    try {
      const history = await api<History>(
        `/conversations/${selected}/messages?before=${messageCursor}`,
      );
      if (version !== viewVersion.current) return;
      preserveScroll();
      setMessages((current) => [
        ...history.items.filter(
          (item) => !current.some((old) => old.id === item.id),
        ),
        ...current,
      ]);
      setMessageCursor(history.next_before || null);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setLoadingHistory(false);
    }
  }

  const effectiveModel =
    versionModel ||
    agents.find((agent) => agent.id === agentId)?.model_id ||
    modelId;
  const selectedModel = models.find((model) => model.id === effectiveModel);
  const selectedAgent = agents.find((agent) => agent.id === agentId);
  const searchAvailable = agentId
    ? !!selectedAgent?.web_enabled
    : !!selectedModel?.native_search_mode;
  const imageWarning =
    attachments.some((file) => file.media_type?.startsWith("image/")) &&
    !selectedModel?.supports_images;
  const canManageChat = can("chat.manage");
  const messageBranch = useMemo(() => selected && !busy && !run && !loadingConversation && canManageChat ? {
    conversation: selected,
    onCreated: async (id: string, text: string) => {
      await refreshConversations(); await openConversation(id); setDraft(text); composerInput.current?.focus();
    },
  } : undefined, [selected, busy, run, loadingConversation, canManageChat, refreshConversations, openConversation]);
  useLayoutEffect(() => {
    const element = composerInput.current;
    if (element) {
      element.style.height = "auto";
      element.style.height = Math.min(element.scrollHeight, 180) + "px";
    }
  }, [draft, section, user]);

  if (user === undefined)
    return <div className="platform-loading">正在连接工作台…</div>;
  if (user === null)
    return (
      <Login
        error={error}
        onLogin={(account) => { acceptAccount(account); setError(""); }}
      />
    );

  if (mustChangePassword) return <div className="platform-shell"><main className="platform-main"><AccountProfile forced onSignedOut={signedOut} /></main></div>;

  const visibleModes = modes.filter((mode) => {
    if (mode === "账号管理") return isAdmin;
    if (mode === "监测大盘") return can("monitoring.read");
    if (mode === "自动任务") return can("automation.read");
    if (mode === "能力中心") return ["knowledge.read", "skills.read", "tools.read", "memory.read", "evaluation.read"].some(can);
    const required: Record<string,string> = {"代码仓库":"repositories.read","智能对话":"chat.read","任务工作台":"work.read","工作流":"workflows.read","运行记录":"chat.read","智能体管理":"agents.read","语音合成":"media.read","图片理解":"media.read","运营管理":"operations.read","组织管理":"organizations.read"};
    return !required[mode] || can(required[mode]);
  });

  return (
    <div
      className={`platform-shell ${menuOpen ? "sidebar-open" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}
    >
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="关闭侧边栏"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside className="platform-sidebar">
        <a
          className="platform-brand"
          href="/"
        >
          <span className="brand-symbol">
            <Sparkles size={20} />
          </span>
          <span>
            知行<span className="brand-caption">智能体工作台</span>
          </span>
        </a>
        <button
          className="new-conversation"
          aria-label="新建对话"
          disabled={!can("chat.manage")}
          onClick={newConversation}
        >
          <Plus size={17} /> 新建对话 <span>＋</span>
        </button>
        <div className="sidebar-body">
        <nav aria-label="主导航">
          {navigationGroups.map((group) => {
            const items = group.items.filter((mode) => visibleModes.includes(mode));
            if (!items.length) return null;
            return <Fragment key={group.title}>
            <span className="nav-group-title">{group.title}</span>
            {items.map((mode, index) => (
            <button
              key={mode}
              aria-label={mode}
              title={mode}
              aria-current={section === mode ? "page" : undefined}
              onClick={() => { setSection(mode); setMenuOpen(false); }}
              className={section === mode ? "active" : ""}
            >
              {createElement(navigationIcons[mode] || MessageSquare, {
                size: 17,
              })}
              <span className="nav-text">{mode}</span>
              {index > 0 &&
                mode !== "任务工作台" &&
                ![
                  "设置",
                  "能力中心",
                  "运行记录",
                  "智能体管理",
                  "工作流",
                  "运营管理",
                  "组织管理",
                  "语音合成",
                  "图片理解",
                  "账号管理",
                  "自动任务",
                  "代码仓库",
                ].includes(mode) && <span className="phase-tag">待开放</span>}
            </button>
          ))}</Fragment>;
          })}
        </nav>
        {can("chat.read") && <ConversationSidebar
          key={user}
          revision={conversationRevision}
          canManage={can("chat.manage")}
          archived={archived}
          onArchivedChange={setArchived}
          selected={selected}
          onOpen={(id) => { setSection("智能对话"); setMenuOpen(false); void openConversation(id); }}
          onRemoved={(id) => { if (selected === id) newConversation(); }}
          projectId={projectId}
          onProjectChange={setProjectId}
        />}
        </div>
        <div className="profile">
          <span className="avatar">{user.slice(0, 1).toUpperCase()}</span>
          <span>
            {user}
            <small>本地工作空间</small>
          </span>
          <button aria-label="个人设置" title="个人设置" onClick={() => setSection("个人设置")}><Settings size={16} /></button>
          <button
            aria-label="退出登录"
            onClick={async () => {
              try {
                await api("/auth/logout", { method: "POST" });
                signedOut();
              } catch (error) {
                setError((error as Error).message);
              }
            }}
          >
            <LogOut size={16} />
          </button>
        </div>
      </aside>
      <main className="platform-main">
        <header className="platform-header">
          <div>
            <button
              className="mobile-menu"
              aria-label="展开侧边栏"
              onClick={() => setMenuOpen(!menuOpen)}
            >
              ☰
            </button>
            <button
              type="button"
              className="desktop-sidebar-toggle"
              aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            >
              {sidebarCollapsed ? (
                <PanelLeftOpen size={19} />
              ) : (
                <PanelLeftClose size={19} />
              )}
            </button>
            <span className="breadcrumb">工作空间</span>
            <ChevronRight size={14} />
            <strong>{section}</strong>
          </div>
          <span className="local-badge">
            <span /> 本地运行
          </span>
        </header>
        {section === "自动任务" ? <Automations isAdmin={isAdmin} canManageNotifications={can("notifications.manage")} canManage={can("automation.manage")} canExecute={can("automation.execute")} canNotify={can("notifications.read")} onOpen={(kind, id) => { if (kind === "chat" && id) { setSection("智能对话"); void openConversation(id); } else if (kind === "work") setSection("任务工作台"); else if (kind === "monitoring") setSection(can("monitoring.read") ? "监测大盘" : "运营管理"); }} /> : section === "账号管理" && isAdmin ? <AccountAdmin onSignedOut={signedOut} /> : section === "个人设置" ? <AccountProfile onSignedOut={signedOut} /> : section === "设置" ? (
          <div className="model-settings-page"><ModelSettings
            isAdmin={isAdmin}
            onChanged={refreshModels}
          /><ModelRoutes isAdmin={isAdmin} models={models}/>{isAdmin && <IdentityAdmin/>}</div>
        ) : section === "能力中心" ? (
          <CapabilityCenter isAdmin={isAdmin} permissions={permissions} onOpenConversation={id => { setSection("智能对话"); void openConversation(id); }} />
        ) : section === "运行记录" ? (
          <RunHistory
            onOpen={(id) => {
              setSection("智能对话");
              void openConversation(id);
            }}
          />
        ) : section === "组织管理" ? (
          <Organizations canCreate={can("organizations.manage")} />
        ) : section === "运营管理" ? (
          <><Operations isAdmin={isAdmin} /><ExtraCosts isAdmin={isAdmin} /></>
        ) : section === "监测大盘" ? (
          <MonitoringDashboard canManage={can("monitoring.manage")} />
        ) : section === "工作流" ? (
          <Workflows models={models} canManage={can("workflows.manage")} canExecute={can("workflows.execute")} canRunWork={can("work.read") && can("work.manage") && can("work.execute")}
            repositoryPermissions={{ read: can("repositories.read"), use: can("repositories.use"), publish: can("repositories.publish") }} />
        ) : section === "代码仓库" ? (
          <RepositorySettings canManage={can("repositories.manage")} />
        ) : section === "任务工作台" ? (
          <WorkTasks canManage={can("work.manage")} repositoryPermissions={{ read: can("repositories.read"), use: can("repositories.use"), publish: can("repositories.publish") }} />
        ) : section === "语音合成" ? (
          <Speech />
        ) : section === "图片理解" ? (
          <Vision />
        ) : section === "智能体管理" ? (
          <AgentSettings
            canManage={can("agents.manage")}
            agents={agents}
            models={models}
            reload={refreshAgents}
            isAdmin={isAdmin}
          />
        ) : section !== "智能对话" ? (
          <div className="coming-soon">
            <Sparkles size={32} />
            <h1>{section}</h1>
            <p>此模块尚未开放，正在按开发阶段建设。</p>
            <button onClick={() => setSection("智能对话")}>返回智能对话</button>
          </div>
        ) : (
          <RunSnapshotContext.Provider value={snapshotContext}>
            <div className="chat-toolbar">
              <label>
                智能体{" "}
                <select
                  aria-label="选择智能体"
                  value={agentId}
                  disabled={!!selected || !!run}
                  onChange={(event) => {
                    setAgentId(event.target.value);
                    setAgentVersion("");
                    setVersionModel("");
                  }}
                >
                  <option value="">通用助手</option>
                  {agents
                    .filter((agent) => agent.enabled || agent.id === agentId)
                    .map((agent) => (
                      <option
                        key={agent.id}
                        value={agent.id}
                      >
                        {agent.name}
                        {agent.enabled ? "" : "（已停用）"}
                      </option>
                    ))}
                </select>
              </label>
              <AgentVersionPicker
                agent={agentId}
                value={agentVersion}
                change={setAgentVersion}
                disabled={!!selected || !!run}
                model={setVersionModel}
              />
              <label>
                模型{" "}
                <select
                  aria-label="选择模型"
                  value={
                    versionModel ||
                    agents.find((agent) => agent.id === agentId)?.model_id ||
                    modelId
                  }
                  disabled={!!run || !!agentId}
                  onChange={(event) => setModelId(event.target.value)}
                >
                  {!models.length && <option value="">尚未配置模型</option>}
                  {models.map((model) => (
                    <option
                      key={model.id}
                      value={model.id}
                    >
                      {model.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <div
              className="chat-scroll"
              ref={scroll}
              aria-live="polite"
              aria-busy={loadingConversation}
            >
              {loadingConversation && <div className="chat-history-loading" role="status">
                <span>正在加载对话…</span><div/><div/><div/>
              </div>}
              {!loadingConversation && !messages.length && !run && (
                <div className="welcome">
                  <div className="welcome-mark">
                    <Sparkles size={30} />
                  </div>
                  <p className="eyebrow">想法，从这里开始</p>
                  <h1>今天，我们一起做点什么？</h1>
                  <p>提出一个问题，梳理一段思路，或探索新的可能。</p>
                  <div className="suggestions">
                    {[
                      "帮我梳理一个项目的开发思路",
                      "解释一下智能体如何调用工具",
                      "帮我把这段文字整理得更清晰",
                    ].map((text) => (
                      <button
                        key={text}
                        onClick={() => setDraft(text)}
                      >
                        {text}
                        <ChevronRight size={15} />
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="message-column" ref={content}>
                {messageCursor && (
                  <button
                    type="button"
                    disabled={loadingHistory}
                    onClick={() => void loadOlderMessages()}
                  >
                    加载更早消息
                  </button>
                )}
                {selectedArchived && (
                  <p role="status">此对话已归档，恢复后可继续发送消息。</p>
                )}
                <ChatTranscript messages={messages} activeRun={run} stream={stream} usage={usage?.runs}
                  error={setError} canFeedback={can("chat.manage")} branch={messageBranch}/>
              </div>
            </div>
            <div className="composer-area">
              {away && <button type="button" className="chat-latest" aria-label="回到最新消息" title="回到最新消息" onClick={latest}><ArrowDown size={18}/></button>}
              <TokenUsage usage={usage?.total} label="会话累计" />
              {!selected && can("chat.manage") && <button type="button" className="new-reference-conversation"
                disabled={busy} onClick={() => void startReferencedConversation()}>引用其他会话</button>}
              {selected && <ConversationReferences key={selected} conversation={selected}
                editable={can("chat.manage") && !busy && !selectedArchived}
                onChange={setReferenceRevision} />}
              {error && (
                <div
                  className="platform-error"
                  role="alert"
                >
                  {error}
                </div>
              )}
              {!models.length && (
                <div className="configuration-note">
                  {isAdmin ? (
                    <button onClick={() => setSection("设置")}>
                      尚未接入模型，前往“设置 → 模型管理”添加
                    </button>
                  ) : (
                    "暂无可用模型，请联系管理员配置。"
                  )}
                </div>
              )}
              <form
                className="composer"
                onSubmit={send}
                onDragOver={(event) => {
                  if (event.dataTransfer.types.includes("Files"))
                    event.preventDefault();
                }}
                onDrop={(event) => {
                  event.preventDefault();
                  void uploadAttachment(Array.from(event.dataTransfer.files));
                }}
              >
                <div className="pending-attachments">
                  {attachments.map((file) => (
                    <span
                      key={file.id}
                      className="attachment-chip"
                    >
                      {file.media_type?.startsWith("image/") ? (
                        <img
                          src={`/api/v1/attachments/${file.id}/download?inline=true`}
                          alt={file.name}
                        />
                      ) : (
                        <Paperclip size={18} />
                      )}
                      <span>{file.name}</span>
                      <button
                        type="button"
                        aria-label={`移除附件 ${file.name}`}
                        onClick={() => void removeAttachment(file.id)}
                      >
                        <X size={14} />
                      </button>
                    </span>
                  ))}
                </div>
                <textarea
                  ref={composerInput}
                  aria-label="发送消息"
                  disabled={loadingConversation}
                  placeholder="向知行提问，或添加图片和文件"
                  onPaste={(event) => {
                    const images = Array.from(event.clipboardData.files).filter(
                      (file) => file.type.startsWith("image/"),
                    );
                    if (images.length) {
                      event.preventDefault();
                      void uploadAttachment(images);
                    }
                  }}
                  value={draft}
                  maxLength={32000}
                  onChange={(event) => setDraft(event.target.value)}
                  onKeyDown={(event) => {
                    if (
                      event.key === "Enter" &&
                      !event.shiftKey &&
                      event.nativeEvent.keyCode !== 229 &&
                      !event.nativeEvent.isComposing
                    ) {
                      event.preventDefault();
                      void send(event);
                    }
                  }}
                />
                <div className="composer-bottom">
                  <div className="composer-tools">
                    <label
                      className="composer-attach"
                      title="添加图片或文件"
                    >
                      <Plus size={21} />
                      <span className="sr-only">添加图片或文件</span>
                      <input
                        className="sr-only"
                        type="file"
                        multiple
                        aria-label="上传图片或文件"
                        accept=".txt,.md,.pdf,.docx,.png,.jpg,.jpeg,.webp"
                        disabled={!can("chat.manage") || busy || !!run || loadingConversation || selectedArchived}
                        onChange={(event) => {
                          void uploadAttachment(
                            Array.from(event.currentTarget.files || []),
                          );
                          event.currentTarget.value = "";
                        }}
                      />
                    </label>
                    <button
                      className="search-toggle"
                      type="button"
                      aria-label="联网搜索"
                      aria-pressed={searchAvailable && nativeSearch}
                      disabled={
                        !searchAvailable || !!run || busy || selectedArchived
                      }
                      title={
                        searchAvailable
                          ? selectedModel?.native_search_mode ===
                            "deepseek_search"
                            ? "使用 DeepSeek 官方搜索接口"
                            : "使用模型内置搜索，或智能体已配置的搜索服务"
                          : "当前模型或智能体未启用联网能力"
                      }
                      onClick={() => setNativeSearch(!nativeSearch)}
                    >
                      <Globe size={16} />
                      联网搜索
                    </button>
                    {!agentId && <button type="button" className="search-toggle" aria-pressed={modelRouting}
                      disabled={!!run || busy || selectedArchived} onClick={() => setModelRouting(!modelRouting)}
                      title="请先在设置中确认候选服务；发出请求后不会自动重发">
                      模型路由{modelRouting ? "已开启" : ""}
                    </button>}
                  </div>
                  {run ? (
                    <button
                      type="button"
                      aria-label="停止生成"
                      className="send-button"
                      onClick={async () => {
                        try {
                          await api(`/runs/${run}/cancel`, { method: "POST" });
                        } catch (error) {
                          setError((error as Error).message);
                        }
                      }}
                    >
                      <Square size={15} />
                    </button>
                  ) : (
                    <button
                      className="send-button"
                      aria-label="发送"
                      disabled={
                        (!draft.trim() &&
                          !attachments.some((file) =>
                            file.media_type?.startsWith("image/"),
                          )) ||
                        !can("chat.run") ||
                        !effectiveModel ||
                        busy ||
                        loadingConversation ||
                        selectedArchived ||
                        imageWarning
                      }
                    >
                      <ArrowUp size={19} />
                    </button>
                  )}
                </div>
              </form>
              {imageWarning && (
                <p
                  className="composer-image-warning"
                  role="status"
                >
                  当前模型未启用图片输入，请选择图片理解模型或在模型设置中开启图片能力。
                </p>
              )}
              <DraftEstimate
                key={`${selected}:${referenceRevision}`}
                enabled={!!draft.trim() && !run && !busy && !loadingConversation && !selectedArchived}
                request={{
                  model_id: versionModel || agents.find((item) => item.id === agentId)?.model_id || modelId,
                  message: draft,
                  conversation_id: selected,
                  agent_id: agentId || null,
                  agent_version_id: agentVersion || null,
                  attachment_ids: attachments.map((item) => item.id),
                }}
              />
              <div className="composer-footnote">
                {status ? (
                  <span>
                    <Check size={12} /> {statuses[status] || status}
                  </span>
                ) : (
                  <span>让思考更深入，让行动更清晰</span>
                )}
                <span>AI 生成内容，请核实重要信息</span>
              </div>
            </div>
          </RunSnapshotContext.Provider>
        )}
      </main>
    </div>
  );
}

function Login({ error: initialError, onLogin }: { error: string; onLogin: (account: AccountIdentity) => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmation, setConfirmation] = useState("");
  const [invitation, setInvitation] = useState("");
  const [registering, setRegistering] = useState(false);
  const [mode, setMode] = useState("approval");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState(initialError);
  const [busy, setBusy] = useState(false);
  const [challenge, setChallenge] = useState("");
  useEffect(() => { api<{registration_mode?: string}>("/auth/registration").then((settings) => setMode(settings.registration_mode || "approval")).catch(() => setMode("approval")); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); setError(""); setNotice("");
    if (registering && password !== confirmation) { setError("两次输入的密码不一致"); return; }
    setBusy(true);
    try {
      if (registering) {
        const result = await api<{message: string}>("/auth/register", { method: "POST", body: JSON.stringify({ username, password, invitation }) });
        setNotice(result.message); setRegistering(false); setPassword(""); setConfirmation(""); setInvitation("");
      } else {
        const result = await api<AccountIdentity | { mfa_required: true; challenge: string }>("/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
        if ("mfa_required" in result) { setChallenge(result.challenge); setPassword(""); }
        else onLogin({ ...result, username: result.username || username });
      }
    } catch (reason) { setError((reason as Error).message); }
    finally { setBusy(false); }
  }
  return <div className="login-page"><div className="login-story"><span className="login-brand"><Sparkles /> 知行</span><div><p className="eyebrow">从想法，到行动</p><h1>与你一起思考，<br />也与你一起完成。</h1><p>一个属于你的智能工作空间。<br />让每次探索，都向前一步。</p></div><span className="login-caption">智能对话 · 自主任务 · 固定工作流</span></div><div className="login-panel">{!challenge && <form onSubmit={(event) => void submit(event)}>
    <div className="login-tabs"><button type="button" aria-pressed={!registering} disabled={busy} onClick={() => { setRegistering(false); setError(""); }}>登录</button><button type="button" aria-pressed={registering} disabled={busy} onClick={() => {setRegistering(true);setError("");setNotice("");}}>注册账号</button></div>
    <span className="login-kicker">你的专属工作空间</span><h2>{registering ? "创建账号" : "欢迎回来"}</h2><p>{registering ? mode === "closed" ? "当前仅接受邀请码注册。" : mode === "open" ? "注册后即可登录，成为平台成员。" : "提交注册后，管理员审核通过即可登录。" : "登录，继续你的探索。"}</p>
    <label>账号<input aria-label="账号" autoComplete="username" required minLength={registering?3:1} maxLength={registering?40:80} pattern={registering?"[A-Za-z0-9_.-]+":undefined} value={username} onChange={(event)=>setUsername(event.target.value)} placeholder={registering?"3–40 位字母、数字、点、短横线或下划线":"请输入账号"} /></label>
    <label>密码<input aria-label="密码" type="password" autoComplete={registering?"new-password":"current-password"} required minLength={registering?12:1} maxLength={256} value={password} onChange={(event)=>setPassword(event.target.value)} placeholder={registering?"至少 12 个字符":"请输入密码"}/></label>
    {registering&&<><label>确认密码<input aria-label="确认密码" type="password" autoComplete="new-password" required minLength={12} maxLength={256} value={confirmation} onChange={(event)=>setConfirmation(event.target.value)}/></label><label>邀请码{mode==="closed"?"":"（可选）"}<input aria-label="邀请码" autoComplete="off" required={mode==="closed"} maxLength={160} value={invitation} onChange={(event)=>setInvitation(event.target.value)} placeholder="由管理员提供"/></label></>}
    {error&&<div className="platform-error" role="alert">{error}</div>}{notice&&<p className="login-notice" role="status">{notice}</p>}
    <button className="login-submit" disabled={busy}>{busy?registering?"正在提交…":"正在登录…":registering?"提交注册":"进入工作台"}<ChevronRight size={17}/></button>
    <small>{registering?"注册不会获得平台管理权限。":"可使用已验证联系方式找回，或联系管理员重置。"}</small>
  </form>}<IdentityLogin key={challenge || "identity-login"} challenge={challenge} onRestart={() => setChallenge("")} onAuthenticated={() => { void api<AccountIdentity>("/auth/me").then(onLogin).catch(reason => setError(reason.message)); }} /></div></div>;
}
