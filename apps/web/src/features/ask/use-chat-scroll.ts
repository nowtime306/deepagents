"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { Message } from "./chat-message";

export function useChatScroll(messages: Message[], stream: string, conversation: string | null, enabled: boolean) {
  const scroll = useRef<HTMLDivElement>(null), content = useRef<HTMLDivElement>(null);
  const following = useRef(true);
  const anchor = useRef<{ top: number; height: number } | null>(null);
  const [away, setAway] = useState(false);

  const reset = useCallback(() => {
    following.current = true; anchor.current = null; setAway(false);
  }, []);
  const preserve = useCallback(() => {
    const element = scroll.current;
    if (element) anchor.current = { top: element.scrollTop, height: element.scrollHeight };
  }, []);
  const latest = useCallback(() => {
    reset();
    const element = scroll.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [reset]);

  useLayoutEffect(() => {
    const element = scroll.current;
    if (!element) return;
    if (anchor.current) {
      element.scrollTop = anchor.current.top + element.scrollHeight - anchor.current.height;
      anchor.current = null;
    } else if (following.current) element.scrollTop = element.scrollHeight;
  }, [messages, stream, conversation, enabled]);

  useEffect(() => {
    const element = scroll.current, column = content.current;
    if (!enabled || !element || !column) return;
    const onScroll = () => {
      const nearBottom = element.scrollHeight - element.clientHeight - element.scrollTop <= 80;
      following.current = nearBottom;
      setAway(!nearBottom);
    };
    const observer = new ResizeObserver(() => {
      if (following.current && !anchor.current) element.scrollTop = element.scrollHeight;
      else onScroll();
    });
    const onExpand = (event: MouseEvent) => {
      if (event.target instanceof Element && event.target.closest("summary")) following.current = false;
    };
    observer.observe(element); observer.observe(column);
    element.addEventListener("scroll", onScroll, { passive: true });
    element.addEventListener("click", onExpand, true);
    return () => { observer.disconnect(); element.removeEventListener("scroll", onScroll); element.removeEventListener("click", onExpand, true); };
  }, [enabled]);

  return { scroll, content, away, reset, preserve, latest };
}
