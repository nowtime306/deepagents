"use client";
import { createContext, useContext } from "react";
export type RunSnapshot = Record<string, unknown>;
export const RunSnapshotContext = createContext<{ id: string | null; mode: "ask"; data: RunSnapshot | null } | null>(null);
export function useRunSnapshot<T>(id: string, mode: string, field: string): T | undefined {
  const snapshot = useContext(RunSnapshotContext);
  return snapshot?.id === id && snapshot.mode === mode ? snapshot.data?.[field] as T | undefined : undefined;
}
export function useLiveSnapshot(id: string, mode: string): boolean {
  const snapshot = useContext(RunSnapshotContext);
  return snapshot?.id === id && snapshot.mode === mode;
}
