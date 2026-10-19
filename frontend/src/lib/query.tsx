"use client";

import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { ApiError, loginUrl, NotAuthenticated, SecurityLocked } from "@/lib/api";

/** Throw the client's `{error}` result (typed errors from the shield middleware pass through). */
export function unwrap<T>(result: { data?: T; error?: unknown; response: Response }): T {
  if (result.error !== undefined || result.data === undefined) {
    throw new ApiError(result.response.status, result.error);
  }
  return result.data;
}

function redirectToLogin(error: unknown) {
  if (error instanceof NotAuthenticated && typeof window !== "undefined") {
    window.location.assign(loginUrl(window.location.pathname + window.location.search));
  }
}

export function QueryProvider({ children }: { children: ReactNode }) {
  const [client] = useState(
    () =>
      new QueryClient({
        // A query with `meta.optionalAuth` (the navigation's /me) only reports "signed out".
        queryCache: new QueryCache({
          onError: (error, query) => {
            if (!query.meta?.optionalAuth) redirectToLogin(error);
          },
        }),
        mutationCache: new MutationCache({ onError: redirectToLogin }),
        defaultOptions: {
          queries: {
            staleTime: 10_000,
            refetchOnWindowFocus: false,
            // Auth, lock and 4xx answers won't change by retrying.
            retry: (count, error) =>
              !(error instanceof ApiError && error.status < 500) && !(error instanceof SecurityLocked) && count < 2,
          },
        },
      }),
  );
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
