import { expect, test } from "@playwright/test";
import { ApiError, decodeFrames, followRun } from "../src/lib/api/platform";

test("SSE split CRLF frames and heartbeats retain unfinished data", () => {
  const first = decodeFrames(': heartbeat\r\n\r\ndata: {"event_id":1,"event":"message.delta","data":{"text":"你"}}\r');
  expect(first.events).toEqual([]);
  const next = decodeFrames(first.rest + '\n\r\n');
  expect(next.events[0].data.text).toBe("你");
});

test("text reaches callback before upstream end with split UTF8 bytes", async () => {
  const original = globalThis.fetch, controller = new AbortController();
  const bytes = new TextEncoder().encode('data: {"event_id":1,"event":"message.delta","data":{"text":"中文"}}\r\n\r\n');
  let stream!: ReadableStreamDefaultController<Uint8Array>;
  const received: string[] = [];
  globalThis.fetch = async () => new Response(new ReadableStream({ start(c) { stream = c; } }));
  try {
    const running = followRun("probe", event => received.push(String(event.data.text)), controller.signal);
    await Promise.resolve();
    for (const byte of bytes) stream.enqueue(Uint8Array.of(byte));
    await expect.poll(() => received).toEqual(["中文"]);
    stream.enqueue(new TextEncoder().encode('data: {"event":"stream.end","data":{"status":"SUCCEEDED"}}\n\n'));
    stream.close(); await running;
  } finally { controller.abort(); globalThis.fetch = original; }
});

test("reconnect resumes cursor, deduplicates events, retains control snapshots", async () => {
  const original = globalThis.fetch, urls: string[] = [], received: string[] = [];
  globalThis.fetch = async input => {
    urls.push(String(input));
    return new Response(urls.length === 1
      ? 'data: {"event_id":7,"event":"message.delta","data":{"text":"first"}}\n\n'
      : 'data: {"event_id":7,"event":"message.delta","data":{"text":"duplicate"}}\n\ndata: {"event":"stream.snapshot","data":{"status":"RUNNING"}}\n\ndata: {"event_id":8,"event":"message.delta","data":{"text":"second"}}\n\ndata: {"event":"stream.end","data":{}}\n\n');
  };
  try {
    await followRun("probe", event => received.push(String(event.data.text || event.event)), new AbortController().signal);
    expect(urls[1]).toContain("after_event_id=7");
    expect(received).toEqual(["first", "stream.snapshot", "second", "stream.end"]);
  } finally { globalThis.fetch = original; }
});

test("revocation control closes without retry", async () => {
  const original = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; return new Response('data: {"event":"stream.error","data":{"status_code":403,"code":"FORBIDDEN","message":"权限已撤销"}}\n\n'); };
  try {
    await expect(followRun("probe", () => {}, new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
    expect(requests).toBe(1);
  } finally { globalThis.fetch = original; }
});

test("abort cancels retry timer without fetching another view", async () => {
  const original = globalThis.fetch, controller = new AbortController(); let requests = 0;
  globalThis.fetch = async () => { requests++; throw new Error("offline"); };
  try {
    const running = followRun("probe", () => {}, controller.signal);
    await Promise.resolve(); controller.abort(); await running;
    expect(requests).toBe(1);
  } finally { globalThis.fetch = original; }
});

test("Ask legacy terminal EOF without control frames remains compatible", async () => {
  const original = globalThis.fetch; const seen: string[] = [];
  globalThis.fetch = async () => new Response('data: {"event_id":1,"event":"run.completed","data":{}}\n\n');
  try {
    await followRun("test", event => seen.push(event.event), new AbortController().signal);
    expect(seen).toEqual(["run.completed"]);
  } finally { globalThis.fetch = original; }
});


test("snapshot-only broken connections exhaust retries instead of reconnecting forever", async () => {
  const original = globalThis.fetch; let requests = 0;
  globalThis.fetch = async () => { requests++; return new Response('data: {"event":"stream.snapshot","data":{"run":{"status":"RUNNING"}}}\n\n'); };
  try {
    await expect(followRun("probe", () => {}, new AbortController().signal)).rejects.toThrow("连接中断");
    expect(requests).toBe(5);
  } finally { globalThis.fetch = original; }
});

test("new Ask terminal EOF reconnects until final snapshot and stream end", async () => {
  const original = globalThis.fetch; const urls: string[] = [], seen: string[] = [];
  globalThis.fetch = async input => {
    urls.push(String(input));
    return new Response(urls.length === 1
      ? 'data: {"event":"stream.snapshot","data":{"marker":"initial"}}\n\ndata: {"event_id":8,"event":"run.completed","data":{}}\n\n'
      : 'data: {"event":"stream.snapshot","data":{"marker":"final"}}\n\ndata: {"event":"stream.end","data":{}}\n\n');
  };
  try {
    await followRun("probe", event => seen.push(String(event.data.marker || event.event)), new AbortController().signal);
    expect(urls).toHaveLength(2); expect(urls[1]).toContain("after_event_id=8");
    expect(seen).toEqual(["initial", "run.completed", "final", "stream.end"]);
  } finally { globalThis.fetch = original; }
});
