"use client";

import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ThumbsUp } from "lucide-react";
import { api } from "@/lib/api/platform";

type Feedback = {
  rating: "helpful" | "unhelpful" | null;
  citations_correct: boolean | null;
  refusal_correct: boolean | null;
  comment: string;
  revision: number;
};
const empty: Feedback = {
  rating: null,
  citations_correct: null,
  refusal_correct: null,
  comment: "",
  revision: 0,
};
const choice = (value: string) => (value === "" ? null : value === "true");

export function MessageFeedback({ id }: { id: string }) {
  const [open, setOpen] = useState(false),
    [busy, setBusy] = useState(false);
  const [value, setValue] = useState<Feedback>(empty);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  return (
    <>
      <button
        type="button"
        aria-label="评价回答"
        title={saved ? "已保存反馈" : "评价回答"}
        aria-pressed={saved}
        onClick={async () => {
          setError("");
          setBusy(true);
          setOpen(true);
          try {
            setValue(await api<Feedback>(`/messages/${id}/feedback`));
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <ThumbsUp size={15} />
      </button>
      <Dialog.Root
        open={open}
        onOpenChange={(next) => {
          if (!busy) setOpen(next);
        }}
      >
        <Dialog.Portal>
          <Dialog.Overlay className="workspace-overlay" />
          <Dialog.Content
            className="workspace-dialog answer-feedback-dialog"
            aria-describedby={undefined}
          >
            <Dialog.Title>评价这条回答</Dialog.Title>
            <p>
              你的反馈只保存为人工评价，不会自动改变提示词、记忆或权限。未核对的项目请保留“未评价”。
            </p>
            {error && (
              <p
                role="alert"
                className="platform-error"
              >
                {error}
              </p>
            )}
            <form
              onSubmit={async (event) => {
                event.preventDefault();
                setBusy(true);
                setError("");
                try {
                  const result = await api<{ revision: number }>(
                    `/messages/${id}/feedback`,
                    { method: "PUT", body: JSON.stringify(value) },
                  );
                  setValue({ ...value, revision: result.revision });
                  setSaved(true);
                  setOpen(false);
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              <label>
                回答是否有帮助
                <select
                  aria-label="回答是否有帮助"
                  required
                  disabled={busy}
                  value={value.rating || ""}
                  onChange={(e) =>
                    setValue({
                      ...value,
                      rating: e.target.value as Feedback["rating"],
                    })
                  }
                >
                  <option value="">请选择</option>
                  <option value="helpful">有帮助</option>
                  <option value="unhelpful">没有帮助</option>
                </select>
              </label>
              <label>
                引用是否正确
                <select
                  aria-label="引用是否正确"
                  disabled={busy}
                  value={
                    value.citations_correct === null
                      ? ""
                      : String(value.citations_correct)
                  }
                  onChange={(e) =>
                    setValue({
                      ...value,
                      citations_correct: choice(e.target.value),
                    })
                  }
                >
                  <option value="">未评价 / 不适用</option>
                  <option value="true">已人工核对，正确</option>
                  <option value="false">已人工核对，有错误</option>
                </select>
              </label>
              <label>
                拒答是否恰当
                <select
                  aria-label="拒答是否恰当"
                  disabled={busy}
                  value={
                    value.refusal_correct === null
                      ? ""
                      : String(value.refusal_correct)
                  }
                  onChange={(e) =>
                    setValue({
                      ...value,
                      refusal_correct: choice(e.target.value),
                    })
                  }
                >
                  <option value="">未评价 / 不适用</option>
                  <option value="true">已人工核对，恰当</option>
                  <option value="false">已人工核对，不恰当</option>
                </select>
              </label>
              <label>
                补充说明
                <textarea
                  aria-label="反馈说明"
                  rows={4}
                  maxLength={2000}
                  disabled={busy}
                  value={value.comment}
                  onChange={(e) =>
                    setValue({ ...value, comment: e.target.value })
                  }
                />
              </label>
              <footer>
                {value.revision > 0 && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={async () => {
                      setBusy(true);
                      setError("");
                      try {
                        await api(
                          `/messages/${id}/feedback?revision=${value.revision}`,
                          { method: "DELETE" },
                        );
                        setValue(empty);
                        setSaved(false);
                        setOpen(false);
                      } catch (e) {
                        setError((e as Error).message);
                      } finally {
                        setBusy(false);
                      }
                    }}
                  >
                    清除反馈
                  </button>
                )}
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setOpen(false)}
                >
                  取消
                </button>
                <button disabled={busy || !value.rating}>保存反馈</button>
              </footer>
            </form>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </>
  );
}
