import { QueryClient } from "@tanstack/react-query";

import { ApiError } from "../services/api";

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        // Retry transient failures once; never retry client errors (4xx) or bad payloads.
        retry: (failureCount, error) =>
          failureCount < 1 && error instanceof ApiError && error.isDependencyUnavailable,
      },
      mutations: { retry: false },
    },
  });
}
