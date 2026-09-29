import { z } from "zod";

import { apiRequest } from "./api";

export const HealthSchema = z.object({ status: z.literal("ok") });
export type Health = z.infer<typeof HealthSchema>;

export const VersionSchema = z.object({
  name: z.string(),
  version: z.string(),
  api_version: z.string(),
  environment: z.string(),
});
export type Version = z.infer<typeof VersionSchema>;

export const systemKeys = {
  health: ["system", "health"] as const,
  version: ["system", "version"] as const,
};

export const getHealth = (signal?: AbortSignal) =>
  apiRequest("/health", HealthSchema, { signal, timeoutMs: 5_000 });

export const getVersion = (signal?: AbortSignal) =>
  apiRequest("/version", VersionSchema, { signal });
