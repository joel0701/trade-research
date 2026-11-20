import { Json } from "./api";

/** A model from /api/models: a Tool of kind "model" plus vendor availability. */
export type ModelInfo = Json & {
  id: number; name: string; description: string; config: Json; enabled: boolean;
  provider: string; provider_name: string; key_env: string | null; available: boolean;
};

export const VENDOR_COLORS: Record<string, string> = {
  anthropic: "#d97757", openai: "#10a37f", google: "#4285f4", meta: "#0866ff",
  xai: "#8a8a8a", mistral: "#fa520f", deepseek: "#4d6bfe", mock: "#7a7f8c",
  ollama: "#e0e0e0", lmstudio: "#6d5dfc", mlx: "#a2aaad", local: "#22c58b",
};

/** Group models by vendor, keeping the order they arrive in. */
export function byVendor<T extends ModelInfo>(models: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const m of models) groups.set(m.provider_name, [...(groups.get(m.provider_name) ?? []), m]);
  return [...groups.entries()];
}

/** How a model is named in pickers: defaults by their own name (which says what they point at). */
export const modelLabel = (m: Json, withId = false) =>
  m.tier ? m.name : `${m.provider_name}: ${m.name}${withId && m.config?.model_id ? ` (${m.config.model_id})` : ""}`;
