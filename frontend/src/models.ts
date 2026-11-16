import { Json } from "./api";

/** A model from /api/models: a Tool of kind "model" plus vendor availability. */
export type ModelInfo = Json & {
  id: number; name: string; description: string; config: Json; enabled: boolean;
  provider: string; provider_name: string; key_env: string | null; available: boolean;
};

export const VENDOR_COLORS: Record<string, string> = {
  anthropic: "#d97757", openai: "#10a37f", google: "#4285f4", meta: "#0866ff",
  xai: "#8a8a8a", mistral: "#fa520f", deepseek: "#4d6bfe",
};

/** Group models by vendor, keeping the order they arrive in. */
export function byVendor<T extends ModelInfo>(models: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const m of models) groups.set(m.provider_name, [...(groups.get(m.provider_name) ?? []), m]);
  return [...groups.entries()];
}
