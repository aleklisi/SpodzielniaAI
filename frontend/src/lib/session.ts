"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { unwrap } from "@/lib/query";

export type Me = components["schemas"]["MeResponse"];
export type Role = "MEMBER" | "EXPERT" | "ADMIN";

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    queryFn: async () => unwrap(await api.GET("/me")),
    staleTime: 60_000,
    meta: { optionalAuth: true },
  });
}

export function hasRole(me: Me | undefined, role: Role): boolean {
  return !!me && (me.roles as string[]).includes(role);
}
