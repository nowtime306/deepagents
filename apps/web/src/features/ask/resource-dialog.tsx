"use client";

import { type ReactNode, useEffect, useRef } from "react";
import { X } from "lucide-react";
import "./resource-list.css";

export function ResourceDialog({ title, busy, close, children }: {
  title: string; busy: boolean; close: () => void; children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  return <dialog ref={dialog} className="resource-dialog" aria-label={title} onCancel={(event) => { event.preventDefault(); if (!busy) close(); }}>
    <header><strong>{title}</strong><button type="button" aria-label="关闭编辑窗口" disabled={busy} onClick={close}><X size={18}/></button></header>
    {children}
  </dialog>;
}
