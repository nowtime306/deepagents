"use client";

import { useEffect, useRef, useState } from "react";
import { api } from "@/lib/api/platform";

type Summary = {
  enabled: boolean;
  session: {
    state: string;
    transport_ready: boolean;
    data_lost?: boolean;
  } | null;
  control: {
    id: string;
    status: string;
    lease_until: string;
    deadline: string;
  } | null;
  login: { has_saved_state: boolean; status: string } | null;
  actions: { request_id: string; status: string; reviewed_at: string | null }[];
};
type Tabs = {
  tabs?: { index: number; title: string; url: string }[];
  active?: number;
};
const labels: Record<string, string> = {
  READY: "人工控制中",
  CLOSING: "正在保存并结束控制",
  UNKNOWN: "结束状态待核查",
  CLOSED: "人工控制已结束",
};

export function BrowserWorkbench({
  run,
  status,
}: {
  run: string;
  status: string;
}) {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [writable, setWritable] = useState(false);
  const [frame, setFrame] = useState("");
  const [originalSize, setOriginalSize] = useState(false);
  const [frameError, setFrameError] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [url, setUrl] = useState("");
  const [text, setText] = useState("");
  const [tabs, setTabs] = useState<Tabs>({});
  const [reviewed, setReviewed] = useState(false);
  const [now, setNow] = useState(0);
  const actionLock = useRef(false);
  const mounted = useRef(true);
  const control = summary?.control;
  const controlId = control?.id;
  const current = control && control.status !== "CLOSED";
  const ready =
    writable &&
    control?.status === "READY" &&
    Date.parse(control.lease_until) > now &&
    Date.parse(control.deadline) > now;
  const unknown =
    summary?.actions.filter(
      (item) => item.status === "UNKNOWN" && !item.reviewed_at,
    ) || [];
  const pending = summary?.actions.some((item) => item.status === "PENDING");
  const canAct = ready && !busy && !pending && !unknown.length;
  const live =
    summary?.session?.state === "READY" && summary.session.transport_ready;

  async function refresh() {
    const data = await api<Summary>(`/work-runs/${run}/browser`);
    if (mounted.current) setSummary(data);
  }

  useEffect(() => {
    mounted.current = true;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    api<{ is_admin: boolean; permissions: string[] }>("/auth/me")
      .then((account) => {
        if (active)
          setWritable(
            account.is_admin || account.permissions.includes("work.execute"),
          );
      })
      .catch(() => {});
    async function poll() {
      try {
        const data = await api<Summary>(`/work-runs/${run}/browser`);
        if (active) {
          setSummary(data);
          setNow(Date.now());
        }
      } catch (reason) {
        if (active) setError((reason as Error).message);
      }
      if (active) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      active = false;
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [run]);

  useEffect(() => {
    if (!live) return;
    let active = true;
    let image = "";
    let timer: ReturnType<typeof setTimeout>;
    const abort = new AbortController();
    async function poll() {
      try {
        const response = await fetch(`/api/v1/work-runs/${run}/browser/frame`, {
          cache: "no-store",
          signal: abort.signal,
        });
        if (!response.ok)
          throw new Error(
            "暂时无法读取画面，请等待当前操作完成或检查环境状态。",
          );
        const blob = await response.blob();
        if (active) {
          const previous = image;
          image = URL.createObjectURL(blob);
          setFrame(image);
          setFrameError("");
          if (previous) URL.revokeObjectURL(previous);
        }
      } catch (reason) {
        if (active) setFrameError((reason as Error).message);
      }
      if (active) timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      active = false;
      clearTimeout(timer);
      abort.abort();
      if (image) URL.revokeObjectURL(image);
    };
  }, [run, live]);

  useEffect(() => {
    if (!ready || !controlId) return;
    const id = controlId;
    const timer = setInterval(() => {
      api(`/work-browser-controls/${id}/renew`, { method: "POST" }).catch(
        (reason) => {
          if (mounted.current) setError((reason as Error).message);
        },
      );
    }, 30000);
    return () => clearInterval(timer);
  }, [controlId, ready]);

  async function perform(work: () => Promise<unknown>) {
    if (actionLock.current) return;
    actionLock.current = true;
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (reason) {
      if (mounted.current) setError((reason as Error).message);
    } finally {
      try {
        await refresh();
      } catch {
        /* The polling loop reports persistent read failures. */
      }
      actionLock.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function action(kind: string, parameters: Record<string, unknown>) {
    if (!canAct || !control) return;
    await perform(async () => {
      const result = await api<Tabs>(
        `/work-browser-controls/${control.id}/actions`,
        {
          method: "POST",
          body: JSON.stringify({
            request_id: crypto.randomUUID(),
            kind,
            parameters,
          }),
        },
      );
      if (mounted.current) setTabs(result);
    });
  }

  async function close(release = false, clear = false) {
    if (
      (release || clear) &&
      !window.confirm(
        clear
          ? "清除本任务保存的登录状态并关闭浏览器？之后需要重新登录。"
          : "关闭浏览器环境？当前页面会结束，已保存的登录状态可供下次恢复。",
      )
    )
      return;
    await perform(() =>
      current
        ? api(`/work-browser-controls/${control!.id}/close`, {
            method: "POST",
            body: JSON.stringify({
              release_environment: release,
              clear_login: clear,
            }),
          })
        : api(`/work-runs/${run}/browser/controls`, {
            method: "POST",
            body: JSON.stringify({
              request_id: crypto.randomUUID(),
              release_environment: release,
              clear_login: clear,
            }),
          }),
    );
  }

  return (
    <section
      className="browser-workbench"
      aria-label="浏览器工作台"
    >
      <header>
        <h4>浏览器工作台</h4>
        <span>
          {current ? labels[control!.status] || "正在准备" : "查看任务浏览器"}
        </span>
      </header>
      <p>
        先由任务打开授权网页。暂停后可接管同一页面完成登录，结束控制后再继续任务。登录状态仅在本任务中加密保存。
      </p>
      {!summary?.enabled && <p>浏览器工作台尚未就绪。</p>}
      {summary?.login?.has_saved_state && (
        <small>
          已加密保存登录状态
          {summary.login.status === "REVIEW" ? "，恢复前请检查" : ""}。
        </small>
      )}
      {live && frame && (
        <div
          className="browser-stage"
          data-original={originalSize}
        >
          <button
            type="button"
            className="browser-frame"
            aria-label="点击浏览器画面"
            disabled={!canAct || !!frameError}
            onClick={(event) => {
              if (event.detail === 0) return;
              const bounds = event.currentTarget.getBoundingClientRect();
              void action("pointer", {
                x: Math.min(
                  1279,
                  Math.max(
                    0,
                    Math.floor(
                      ((event.clientX - bounds.left) / bounds.width) * 1280,
                    ),
                  ),
                ),
                y: Math.min(
                  719,
                  Math.max(
                    0,
                    Math.floor(
                      ((event.clientY - bounds.top) / bounds.height) * 720,
                    ),
                  ),
                ),
              });
            }}
          >
            {/* The authenticated image is a short-lived blob, with no public media URL. */}
            <img
              src={frame}
              alt="任务浏览器当前画面"
              draggable={false}
            />
          </button>
        </div>
      )}
      {!live && (
        <p>
          {summary?.session?.state === "RELEASED"
            ? "浏览器环境已结束。恢复任务时页面会重新加载，并尝试使用最近保存的登录状态；未确认的操作不会自动重做。"
            : "尚无可观看的页面。等待浏览器打开后再查看。"}
        </p>
      )}
      {summary?.session?.data_lost && (
        <p role="alert">环境已过期，未保存的变更可能丢失，请先核对运行记录。</p>
      )}
      {frameError && <p role="status">{frameError} 画面可能已过期。</p>}
      <div className="browser-toolbar">
        {live && frame && (
          <button
            type="button"
            aria-pressed={originalSize}
            onClick={() => setOriginalSize(!originalSize)}
          >
            {originalSize ? "适应窗口" : "原始尺寸"}
          </button>
        )}
        {!current && writable && live && (
          <button
            type="button"
            disabled={busy || !["PAUSED", "NEEDS_REVIEW"].includes(status)}
            onClick={() =>
              void perform(() =>
                api(`/work-runs/${run}/browser/controls`, {
                  method: "POST",
                  body: JSON.stringify({ request_id: crypto.randomUUID() }),
                }),
              )
            }
          >
            接管浏览器
          </button>
        )}
        {status !== "PAUSED" && !current && (
          <small>请先使用“请求暂停”，等待安全暂停。</small>
        )}
        {current && writable && (
          <button
            type="button"
            disabled={
              busy ||
              pending ||
              !!unknown.length ||
              control!.status === "CLOSING"
            }
            onClick={() => void close()}
          >
            结束控制并保存
          </button>
        )}
        {writable &&
          ["PAUSED", "NEEDS_REVIEW"].includes(status) &&
          (live || summary?.login?.has_saved_state) && (
            <>
              <button
                type="button"
                disabled={busy || pending}
                onClick={() => void close(true)}
              >
                关闭浏览器环境
              </button>
              <button
                type="button"
                disabled={busy || pending}
                onClick={() => void close(true, true)}
              >
                清除登录状态
              </button>
            </>
          )}
      </div>
      {current && !ready && control!.status === "READY" && (
        <p role="status">人工控制已过期，请等待保存结束后重新接管。</p>
      )}
      {ready && (
        <>
          <p>
            点击画面定位输入框，或按 Tab 切换焦点。输入只发送到当前焦点；按
            Enter 可能提交表单。控制最长持续 30 分钟。
          </p>
          <form
            className="browser-toolbar"
            onSubmit={(event) => {
              event.preventDefault();
              void action("open", { url });
            }}
          >
            <label>
              授权网页地址
              <input
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://已授权域名/"
                maxLength={4000}
                required
              />
            </label>
            <button
              disabled={!canAct}
              type="submit"
            >
              打开网页
            </button>
            <button
              disabled={!canAct || !url}
              type="button"
              onClick={() => void action("new_tab", { url })}
            >
              新标签页
            </button>
          </form>
          <form
            className="browser-toolbar"
            autoComplete="off"
            onSubmit={(event) => {
              event.preventDefault();
              const value = text;
              setText("");
              void action("type", { text: value });
            }}
          >
            <label>
              发送到当前焦点
              <input
                type="password"
                autoComplete="new-password"
                value={text}
                onChange={(event) => setText(event.target.value)}
                maxLength={4000}
                required
              />
            </label>
            <button
              type="submit"
              disabled={!canAct || !text}
            >
              输入到网页
            </button>
          </form>
          <div className="browser-toolbar">
            {[
              ["Tab", "切换焦点"],
              ["Enter", "按 Enter"],
              ["Control+a", "全选"],
              ["Backspace", "退格"],
              ["Escape", "按 Esc"],
            ].map(([key, label]) => (
              <button
                key={key}
                type="button"
                disabled={!canAct}
                onClick={() => void action("key", { key })}
              >
                {label}
              </button>
            ))}
            <button
              type="button"
              disabled={!canAct}
              onClick={() => void action("scroll", { pixels: -500 })}
            >
              向上滚动
            </button>
            <button
              type="button"
              disabled={!canAct}
              onClick={() => void action("scroll", { pixels: 500 })}
            >
              向下滚动
            </button>
            <button
              type="button"
              disabled={!canAct}
              onClick={() => void action("close_tab", {})}
            >
              关闭当前标签页
            </button>
          </div>
          {!!tabs.tabs?.length && (
            <label>
              浏览器标签页
              <select
                aria-label="浏览器标签页"
                disabled={!canAct}
                value={tabs.active ?? 0}
                onChange={(event) =>
                  void action("select_tab", {
                    index: Number(event.target.value),
                  })
                }
              >
                {tabs.tabs.map((tab) => (
                  <option
                    key={tab.index}
                    value={tab.index}
                  >
                    {tab.index + 1} · {tab.title || tab.url}
                  </option>
                ))}
              </select>
            </label>
          )}
        </>
      )}
      {!!unknown.length && (
        <div role="alert">
          <p>
            上一次操作结果未知。请检查画面，避免重复提交或支付；系统不会自动重试。
          </p>
          <label>
            <input
              type="checkbox"
              checked={reviewed}
              onChange={(event) => setReviewed(event.target.checked)}
            />
            我已检查页面并核实操作结果
          </label>
          <button
            type="button"
            disabled={!writable || !reviewed || busy}
            onClick={() =>
              void perform(async () => {
                for (const item of unknown)
                  await api(
                    `/work-browser-controls/${control!.id}/actions/${item.request_id}/review`,
                    {
                      method: "POST",
                      body: JSON.stringify({ acknowledged: true }),
                    },
                  );
                setReviewed(false);
              })
            }
          >
            确认已检查
          </button>
        </div>
      )}
      {error && <p role="alert">{error}</p>}
    </section>
  );
}
