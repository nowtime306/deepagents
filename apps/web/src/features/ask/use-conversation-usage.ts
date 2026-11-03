"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api/platform";
import type { Usage } from "./token-usage";

export function useConversationUsage(
  conversation: string | null,
  activeRun: string | null,
  messages: number,
) {
  const [data, setData] = useState<{
    id: string;
    runs: Usage[];
    total: Usage;
  } | null>(null);
  useEffect(() => {
    if (!conversation || activeRun) return;
    const controller = new AbortController();
    const load = () =>
      api<{ runs: Usage[]; total: Usage }>(
        `/conversations/${conversation}/usage`,
        {
          signal: controller.signal,
        },
      )
        .then((result) => {
          if (!controller.signal.aborted)
            setData({ id: conversation, ...result });
        })
        .catch(() => {
          /* Missing statistics must not interrupt a conversation. */
        });
    void load();
    return () => {
      controller.abort();
    };
  }, [conversation, activeRun, messages]);
  return data?.id === conversation ? data : null;
}
