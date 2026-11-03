export type MemoryScope = {
  kind: "global" | "project" | "agent";
  id: string;
  expires: string;
};
export type ScopeOptions = {
  projects: { id: string; name: string }[];
  agents: { id: string; name: string }[];
};
export const emptyScope: MemoryScope = { kind: "global", id: "", expires: "" };
export function localExpiry(value?: string | null) {
  if (!value) return "";
  const point = new Date(value);
  return new Date(point.getTime() - point.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
export function scopePayload(value: MemoryScope) {
  return {
    scope_kind: value.kind,
    scope_id: value.kind === "global" ? null : value.id || null,
    expires_at: value.expires ? new Date(value.expires).toISOString() : null,
  };
}
