"use client";
import { useRef, useState } from "react";
import type { WorkflowDefinition, WorkflowLayout } from "./workflow-editor";

type Snapshot = { definition: WorkflowDefinition; layout: WorkflowLayout };

/** Store configuration only; runtime records and database revisions never enter this stack. */
export function useWorkflowHistory(current: Snapshot, restore: (snapshot: Snapshot) => void) {
  const past = useRef<Snapshot[]>([]), future = useRef<Snapshot[]>([]);
  const [, update] = useState(0);
  const changed = () => update(value => value + 1);
  function remember() {
    if (JSON.stringify(past.current.at(-1)) !== JSON.stringify(current)) {
      past.current = [...past.current.slice(-39), structuredClone(current)];
    }
    future.current = []; changed();
  }
  function undo() {
    const previous = past.current.pop();
    if (!previous) return;
    future.current.push(structuredClone(current)); restore(structuredClone(previous)); changed();
  }
  function redo() {
    const next = future.current.pop();
    if (!next) return;
    past.current.push(structuredClone(current)); restore(structuredClone(next)); changed();
  }
  function reset() { past.current = []; future.current = []; changed(); }
  return { remember, undo, redo, reset, canUndo: past.current.length > 0, canRedo: future.current.length > 0 };
}
