"use client";

import { useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { GitBranch, Pencil } from "lucide-react";
import { api } from "@/lib/api/platform";

export function MessageBranches({
  conversation,
  message,
  text,
  userMessage,
  onCreated,
  onError,
}: {
  conversation: string;
  message: string;
  text: string;
  userMessage: boolean;
  onCreated: (id: string, draft: string) => Promise<void>;
  onError: (error: string) => void;
}) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false),
    [edited, setEdited] = useState(text);
  const request = useRef<{ key: string; id: string } | null>(null);
  async function create(mode: "fork" | "edit") {
    const key = `${mode}:${mode === "edit" ? edited : ""}`;
    if (request.current?.key !== key)
      request.current = { key, id: crypto.randomUUID() };
    setBusy(true);
    try {
      const result = await api<{ id: string; draft: string }>(
        `/conversations/${conversation}/branches`,
        {
          method: "POST",
          body: JSON.stringify({
            message_id: message,
            request_id: request.current.id,
            mode,
            ...(mode === "edit" ? { edit_text: edited } : {}),
          }),
        },
      );
      setOpen(false);
      await onCreated(result.id, result.draft);
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <button
        type="button"
        aria-label="从此消息创建分支"
        title="从此消息创建分支"
        disabled={busy}
        onClick={() => void create("fork")}
      >
        <GitBranch size={15} />
      </button>
      {userMessage && (
        <Dialog.Root
          open={open}
          onOpenChange={(value) => {
            if (!busy) {
              setOpen(value);
              if (value) setEdited(text);
            }
          }}
        >
          <Dialog.Trigger asChild>
            <button
              type="button"
              aria-label="编辑提问并创建分支"
              title="编辑提问并创建分支"
              disabled={busy}
            >
              <Pencil size={15} />
            </button>
          </Dialog.Trigger>
          <Dialog.Portal>
            <Dialog.Overlay className="workspace-dialog-overlay" />
            <Dialog.Content className="workspace-dialog">
              <Dialog.Title>编辑提问并创建分支</Dialog.Title>
              <Dialog.Description>
                保留原会话，只导入此提问之前的文字与附件。工具不会重放，历史费用不会重复记账。创建后可在输入框中继续修改并发送。
              </Dialog.Description>
              <label>
                修改后的提问
                <textarea
                  aria-label="修改后的提问"
                  maxLength={16000}
                  value={edited}
                  onChange={(event) => setEdited(event.target.value)}
                />
              </label>
              <div className="workspace-dialog-actions">
                <Dialog.Close asChild>
                  <button
                    type="button"
                    disabled={busy}
                  >
                    取消
                  </button>
                </Dialog.Close>
                <button
                  type="button"
                  disabled={busy || !edited.trim()}
                  onClick={() => void create("edit")}
                >
                  {busy ? "创建中…" : "创建分支"}
                </button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </>
  );
}
