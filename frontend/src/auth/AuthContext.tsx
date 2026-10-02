import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";

import { ApiError, SESSION_EXPIRED_EVENT } from "../services/api";
import { getMe, logout as apiLogout, queryKeys } from "../services/endpoints";
import type { Me } from "../services/schemas";

interface AuthState {
  me: Me | null;
  /** True until the first /auth/me answer arrives. */
  checking: boolean;
  /** A non-401 failure of /auth/me (for example, the API is down). */
  error: unknown;
  isAdmin: boolean;
  signOut: () => Promise<void>;
  /** Re-read the profile stats (after an analysis is saved or deleted). */
  refresh: () => Promise<unknown>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.me,
    queryFn: ({ signal }) => getMe(signal),
    retry: false,
    staleTime: 60_000,
  });

  const signedOut = query.error instanceof ApiError && query.error.status === 401;
  const me = signedOut ? null : (query.data ?? null);

  useEffect(() => {
    // Any signed-in request answered 401: the session ended. Forget everything cached.
    const onExpired = () => {
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      void queryClient.invalidateQueries({ queryKey: queryKeys.me });
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onExpired);
  }, [queryClient]);

  const signOut = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      // Drop everything fetched as this user, then mark them signed out. (clear() would
      // also detach the mounted /auth/me observer, leaving the page signed in on screen.)
      queryClient.removeQueries({ predicate: (q) => q.queryKey[0] !== "auth" });
      queryClient.setQueryData(queryKeys.me, null);
    }
  }, [queryClient]);

  const refresh = useCallback(
    () => queryClient.invalidateQueries({ queryKey: queryKeys.me }),
    [queryClient],
  );

  const value = useMemo<AuthState>(
    () => ({
      me,
      checking: query.isPending && !signedOut,
      error: signedOut ? null : query.error,
      isAdmin: me?.user.role === "admin",
      signOut,
      refresh,
    }),
    [me, query.isPending, query.error, signedOut, signOut, refresh],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const value = useContext(AuthContext);
  if (!value) throw new Error("useAuth must be used inside <AuthProvider>");
  return value;
}
