import { useEffect } from "react";
import { create } from "zustand";
export type ModelSuggestion = { id: string; label?: string; reasoning_efforts?: string[]; custom?: boolean };
type Catalog = { models: Record<string, ModelSuggestion[]>; source?: string; checked_on?: string };
const useCatalog = create<{ catalog: Catalog; loaded: boolean }>(() => ({ catalog: { models: {} }, loaded: false }));
let pending: Promise<void> | null = null;
/** Re-read the suggestions (after Settings changes the custom model list). */
export function refreshModelCatalog() {
  pending = fetch("/api/settings/models").then((r) => r.ok ? r.json() : { models: {} }).catch(() => ({ models: {} }))
    .then((catalog: Catalog) => useCatalog.setState({ catalog, loaded: true }));
  return pending;
}
export function useModelCatalog() {
  const catalog = useCatalog((s) => s.catalog);
  useEffect(() => { if (!pending) void refreshModelCatalog(); }, []);
  return catalog;
}
/** Reasoning effort levels a channel understands ([] = the channel has no such setting). */
export const PROVIDER_EFFORTS: Record<string, string[]> = {
  local_claude: ["low", "medium", "high", "xhigh", "max"],
  local_codex: ["minimal", "low", "medium", "high", "xhigh"],
};
export function effortsFor(catalog: Catalog, provider: string, model: string | null | undefined) {
  if (!PROVIDER_EFFORTS[provider]) return [];
  return catalog.models[provider]?.find((m) => m.id === model)?.reasoning_efforts || PROVIDER_EFFORTS[provider];
}
export function ModelOptions({ id, provider }: { id: string; provider: string }) {
  const catalog = useModelCatalog();
  return <datalist id={id}>{(catalog.models[provider] || []).map((m) => <option key={m.id} value={m.id}>{m.label || m.id}</option>)}</datalist>;
}
