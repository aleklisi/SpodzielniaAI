"use client";

/**
 * User management console (8.5): accounts, passwords, blocks, roles. ADMIN + MFA on every call
 * (the API enforces both, plus a fresh login for privilege grants and unblocks). The rules
 * (own account, last administrator, external identities) are the backend's; the UI explains
 * them before the admin tries.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "@/lib/api";
import type { components } from "@/lib/api-schema";
import { apiErrorCode, knownCode } from "@/lib/messages";
import { unwrap } from "@/lib/query";
import type { Messages } from "@/messages";

export type UserSummary = components["schemas"]["UserOut"];
export type UserDetail = components["schemas"]["UserDetailOut"];
export type UserPage = components["schemas"]["UserPageOut"];
export type CreateUserInput = components["schemas"]["CreateUserIn"];
export type Role = UserSummary["roles"][number];
export type UserStatus = UserSummary["status"];
export type BlockReason = components["schemas"]["BlockIn"]["reason"];
export type PasswordMode = components["schemas"]["PasswordResetIn"]["mode"];

/** The roles in their order; names in `adminUsers.roleNames`, hints in `adminUsers.roleHints`. */
export const ROLES: Role[] = ["MEMBER", "EXPERT", "ADMIN"];

/** Labels in `adminUsers.statuses`. */
export const STATUS_TONE: Record<UserStatus, "ok" | "danger" | "warn" | "primary"> = {
  active: "ok",
  blocked: "danger",
  shield_locked: "warn",
  pending_setup: "primary",
};

/** Labels in `adminUsers.blockReasons` (which also names `idp_disabled`, set by a sync). */
export const BLOCK_REASONS: BlockReason[] = ["left_cooperative", "security", "policy_violation", "other"];
export type BlockReasonKey = keyof Messages["adminUsers"]["blockReasons"];
const BLOCK_REASON_KEYS = [...BLOCK_REASONS, "idp_disabled"] as const satisfies readonly BlockReasonKey[];

export function blockReasonKey(reason: string | null | undefined): BlockReasonKey | null {
  return knownCode(BLOCK_REASON_KEYS, reason);
}

export type RequiredActionKey = keyof Messages["adminUsers"]["actions"];
const ACTION_KEYS = ["UPDATE_PASSWORD", "CONFIGURE_TOTP", "VERIFY_EMAIL"] as const satisfies readonly RequiredActionKey[];

export function requiredActionKey(action: string): RequiredActionKey | null {
  return knownCode(ACTION_KEYS, action);
}

export function displayName(user: Pick<UserSummary, "display_name" | "username" | "email">): string {
  return user.display_name ?? user.username ?? user.email ?? "—";
}

// --- Queries and mutations ---------------------------------------------------------------------

const KEY = ["admin", "users"] as const;

export interface UserFilters {
  q: string;
  role: Role | "";
  status: UserStatus | "";
  offset: number;
}

export const PAGE_SIZE = 25;

export function useUsers(filters: UserFilters) {
  return useQuery({
    queryKey: [...KEY, "list", filters],
    placeholderData: (previous) => previous,
    queryFn: async () =>
      unwrap(
        await api.GET("/admin/users", {
          params: {
            query: {
              q: filters.q || undefined,
              role: filters.role || undefined,
              status: filters.status || undefined,
              offset: filters.offset,
              limit: PAGE_SIZE,
            },
          },
        }),
      ),
  });
}

export function useUser(id: string | null) {
  return useQuery({
    queryKey: [...KEY, "detail", id],
    enabled: id !== null,
    queryFn: async () => unwrap(await api.GET("/admin/users/{user_id}", { params: { path: { user_id: id ?? "" } } })),
  });
}

/** Every write refreshes the list and the open detail panel. */
function useUsersMutation<I, O>(fn: (input: I) => Promise<O>) {
  const client = useQueryClient();
  return useMutation({ mutationFn: fn, onSettled: () => void client.invalidateQueries({ queryKey: KEY }) });
}

const path = (id: string) => ({ params: { path: { user_id: id } } });

export function useCreateUser() {
  return useUsersMutation(async (body: CreateUserInput) => unwrap(await api.POST("/admin/users", { body })));
}

export function useSyncUsers() {
  return useUsersMutation(async () => unwrap(await api.POST("/admin/users/sync")));
}

export function useSetRoles() {
  return useUsersMutation(async ({ id, roles }: { id: string; roles: Role[] }) =>
    unwrap(await api.PUT("/admin/users/{user_id}/roles", { ...path(id), body: { roles } })),
  );
}

export function useBlock() {
  return useUsersMutation(async ({ id, reason, note }: { id: string; reason: BlockReason; note: string }) =>
    unwrap(await api.POST("/admin/users/{user_id}/block", { ...path(id), body: { reason, note: note.trim() || null } })),
  );
}

export function useUnblock() {
  return useUsersMutation(async (id: string) => unwrap(await api.POST("/admin/users/{user_id}/unblock", path(id))));
}

export function usePasswordReset() {
  return useUsersMutation(async ({ id, mode }: { id: string; mode: PasswordMode }) =>
    unwrap(await api.POST("/admin/users/{user_id}/password-reset", { ...path(id), body: { mode } })),
  );
}

export function useRequirePasswordChange() {
  return useUsersMutation(async ({ id, signOut }: { id: string; signOut: boolean }) =>
    unwrap(await api.POST("/admin/users/{user_id}/require-password-change", { ...path(id), body: { sign_out: signOut } })),
  );
}

export function useSignOut() {
  return useUsersMutation(async (id: string) => unwrap(await api.POST("/admin/users/{user_id}/sign-out", path(id))));
}

// --- Errors -------------------------------------------------------------------------------------

export type UsersErrorKey = keyof Messages["adminUsers"]["errors"];

const CODES = [
  "user_exists",
  "invalid_username",
  "email_required",
  "profile_required",
  "email_failed",
  "password_policy",
  "cannot_demote_self",
  "cannot_block_self",
  "last_admin",
  "already_blocked",
  "not_blocked",
  "external_identity",
  "foreign_identity",
  "user_not_found",
  "user_missing_in_idp",
  "user_admin_unavailable",
  "idp_unavailable",
  "idp_error",
  "rate_limited",
] as const satisfies readonly UsersErrorKey[];

export function usersErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  return apiErrorCode(error) ?? (error.status === 429 ? "rate_limited" : null);
}

/** The key in `adminUsers.errors`; `fallback` when the code is unknown. */
export function usersErrorKey(error: unknown, fallback: UsersErrorKey = "failed"): UsersErrorKey {
  const code = knownCode(CODES, usersErrorCode(error));
  if (code) return code;
  if (error instanceof ApiError && error.status === 422) return "invalid";
  return fallback;
}

// --- Guardrails (the backend enforces the same) --------------------------------------------------

export type Lockout = keyof Messages["adminUsers"]["lockout"];

/** Why the admin role can't be removed / the account can't be blocked (`adminUsers.lockout`), or null if it can. */
export function lockoutReason(user: UserDetail): Lockout | null {
  if (user.is_self) return "self";
  if (user.roles.includes("ADMIN") && user.status !== "blocked" && user.status !== "shield_locked" && user.other_active_admins === 0) {
    return "lastAdmin";
  }
  return null;
}

export function grantsElevated(before: Role[], after: Role[]): Role[] {
  return after.filter((r) => (r === "EXPERT" || r === "ADMIN") && !before.includes(r));
}
