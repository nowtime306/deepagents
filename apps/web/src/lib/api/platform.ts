export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: {
      ...(options?.body instanceof FormData
        ? {}
        : { "Content-Type": "application/json" }),
      ...options?.headers,
    },
  });
  if (!response.ok) {
    const data = await response.json().catch(() => ({}));
    throw new ApiError(
      response.status,
      data.error?.message || "服务暂时不可用，请稍后重试",
    );
  }
  return response.json();
}

export type RunEvent = {
  event_id?: number;
  event: string;
  data: { text?: string; message?: string; message_id?: string; role?: string; code?: string; status?: number | string; status_code?: number; [key: string]: unknown };
};

/** Decode complete SSE frames, preserving split network chunks. */
export function decodeFrames(buffer: string): {
  events: RunEvent[];
  rest: string;
} {
  const frames = buffer.replace(/\r\n/g, "\n").split("\n\n");
  const rest = frames.pop() || "";
  const events = frames.flatMap((frame) => {
    const data = frame
      .split("\n")
      .filter((line) => line.startsWith("data:"))
      .map((line) => line.slice(5).trimStart())
      .join("\n");
    return data ? [JSON.parse(data) as RunEvent] : [];
  });
  return { events, rest };
}

async function followEvents(
  path: string,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
  legacyTerminal = false,
  onConnection?: (reconnecting: boolean) => void,
): Promise<void> {
  let cursor = 0, terminal = false, failures = 0, legacyDone = false, controlSeen = false;
  while (!terminal && !signal.aborted) {
    try {
      const response = await fetch(`/api/v1${path}${path.includes("?") ? "&" : "?"}after_event_id=${cursor}`, {
        signal, headers: { Accept: "text/event-stream" }, cache: "no-store",
      });
      if (!response.ok) throw new ApiError(response.status, "无法读取运行进度");
      onConnection?.(false);
      if (!response.body) throw new Error("浏览器不支持流式响应");
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      try {
        while (!signal.aborted) {
          const { done, value } = await reader.read();
          buffer += decoder.decode(value, { stream: !done });
          const decoded = decodeFrames(buffer); buffer = decoded.rest;
          for (const event of decoded.events) {
            if (signal.aborted) return;
            if (event.event === "stream.error")
              throw new ApiError(Number(event.data.status_code) || 500, String(event.data.message || "运行订阅已中断"));
            controlSeen ||= event.event.startsWith("stream.");
            if (event.event_id !== undefined) {
              if (!Number.isSafeInteger(event.event_id) || event.event_id < 0) throw new Error("事件序号无效");
              if (event.event_id <= cursor) continue;
              cursor = event.event_id;
              failures = 0;
            }
            onEvent(event);
            terminal ||= event.event === "stream.end";
            legacyDone ||= legacyTerminal && ["run.completed", "run.failed", "run.cancelled"].includes(event.event);
          }
          if (done && legacyDone && !controlSeen) terminal = true;
          if (done || terminal) break;
        }
      } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
      if (!terminal) throw new Error("连接已断开");
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof ApiError && [401, 403, 404, 422].includes(error.status)) throw error;
      onConnection?.(true);
      if (++failures > 4) throw new Error("连接中断，请重新打开此运行以恢复进度", { cause: error });
      await new Promise<void>((resolve) => {
        const finish = () => { clearTimeout(timer); signal.removeEventListener("abort", finish); resolve(); };
        const timer = setTimeout(finish, Math.min(1000 * failures, 4000));
        signal.addEventListener("abort", finish, { once: true });
        if (signal.aborted) finish();
      });
    }
  }
}

export function followRun(id: string, onEvent: (event: RunEvent) => void, signal: AbortSignal, onConnection?: (reconnecting: boolean) => void): Promise<void> {
  return followEvents(`/runs/${id}/events`, onEvent, signal, true, onConnection);
}
