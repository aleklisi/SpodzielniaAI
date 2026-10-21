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
import { unwrap } from "@/lib/query";

export type UserSummary = components["schemas"]["UserOut"];
export type UserDetail = components["schemas"]["UserDetailOut"];
export type UserPage = components["schemas"]["UserPageOut"];
export type CreateUserInput = components["schemas"]["CreateUserIn"];
export type Role = UserSummary["roles"][number];
export type UserStatus = UserSummary["status"];
export type BlockReason = components["schemas"]["BlockIn"]["reason"];
export type PasswordMode = components["schemas"]["PasswordResetIn"]["mode"];

export const ROLES: { role: Role; label: string; hint: string }[] = [
  { role: "MEMBER", label: "Członek", hint: "Notatnik, Wiki, czat spółdzielni. Każde konto ma tę rolę." },
  { role: "EXPERT", label: "Ekspert", hint: "Recenzuje artykuły Wiki. Wymaga MFA." },
  { role: "ADMIN", label: "Administrator", hint: "Panel bezpieczeństwa, modele AI, użytkownicy. Wymaga MFA." },
];

export const ROLE_LABEL: Record<Role, string> = { MEMBER: "Członek", EXPERT: "Ekspert", ADMIN: "Administrator" };

export const STATUS: Record<UserStatus, { label: string; tone: "ok" | "danger" | "warn" | "primary" }> = {
  active: { label: "Aktywny", tone: "ok" },
  blocked: { label: "Zablokowany", tone: "danger" },
  shield_locked: { label: "Zablokowany przez system ochrony", tone: "warn" },
  pending_setup: { label: "Oczekuje na konfigurację", tone: "primary" },
};

export const BLOCK_REASONS: { reason: BlockReason; label: string }[] = [
  { reason: "left_cooperative", label: "Odejście ze spółdzielni" },
  { reason: "security", label: "Bezpieczeństwo konta" },
  { reason: "policy_violation", label: "Naruszenie zasad" },
  { reason: "other", label: "Inny powód" },
];

const BLOCK_REASON_LABEL: Record<string, string> = {
  ...Object.fromEntries(BLOCK_REASONS.map((r) => [r.reason, r.label])),
  idp_disabled: "Wyłączony w konsoli Keycloak",
};

export function blockReasonLabel(reason: string | null | undefined): string {
  return reason ? (BLOCK_REASON_LABEL[reason] ?? reason) : "—";
}

const ACTION_LABEL: Record<string, string> = {
  UPDATE_PASSWORD: "ustawienie hasła",
  CONFIGURE_TOTP: "konfiguracja MFA",
  VERIFY_EMAIL: "potwierdzenie e-maila",
};

export function actionsLabel(actions: string[]): string {
  return actions.map((a) => ACTION_LABEL[a] ?? a).join(", ");
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

const ERRORS: Record<string, string> = {
  user_exists: "Użytkownik o tej nazwie lub adresie e-mail już istnieje.",
  invalid_username: "Nazwa użytkownika: 3–64 znaki, małe litery, cyfry, kropka, myślnik lub podkreślenie.",
  email_required: "Do wysłania linku potrzebny jest adres e-mail.",
  email_failed: "Nie udało się wysłać e-maila (sprawdź pocztę w Keycloak). Wybierz hasło tymczasowe.",
  password_policy: "Keycloak odrzucił wygenerowane hasło (polityka haseł). Spróbuj ponownie.",
  cannot_demote_self: "Nie możesz odebrać sobie roli administratora.",
  cannot_block_self: "Nie możesz zablokować własnego konta.",
  last_admin: "To ostatni aktywny administrator. Najpierw nadaj tę rolę komuś innemu.",
  already_blocked: "To konto jest już zablokowane.",
  not_blocked: "To konto nie jest zablokowane.",
  external_identity: "To konto loguje się przez zewnętrznego dostawcę; hasłem zarządza tamten serwis.",
  foreign_identity: "To konto pochodzi z innego dostawcy tożsamości.",
  user_not_found: "Nie znaleziono użytkownika.",
  user_missing_in_idp: "Tego konta nie ma w Keycloak (usunięte w konsoli?). Uruchom synchronizację.",
  user_admin_unavailable:
    "Zarządzanie użytkownikami nie jest skonfigurowane (brak klienta Keycloak). Administrator serwera: make keycloak-setup.",
  idp_unavailable: "Keycloak jest chwilowo niedostępny. Spróbuj ponownie za chwilę.",
  idp_error: "Keycloak odrzucił operację.",
  rate_limited: "Zbyt wiele operacji. Odczekaj chwilę.",
};

export function usersErrorCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const detail = (error.detail as { detail?: unknown } | null)?.detail ?? error.detail;
  if (detail && typeof detail === "object" && "error" in detail && typeof detail.error === "string") return detail.error;
  return error.status === 429 ? "rate_limited" : null;
}

export function usersError(error: unknown, fallback = "Operacja nie powiodła się."): string {
  const code = usersErrorCode(error);
  if (code && ERRORS[code]) return ERRORS[code];
  if (error instanceof ApiError && error.status === 422) return "Sprawdź wprowadzone wartości.";
  return fallback;
}

// --- Guardrails (the backend enforces the same) --------------------------------------------------

/** Why the admin role can't be removed / the account can't be blocked, or null if it can. */
export function lockoutReason(user: UserDetail): string | null {
  if (user.is_self) return "To Twoje konto.";
  if (user.roles.includes("ADMIN") && user.status !== "blocked" && user.status !== "shield_locked" && user.other_active_admins === 0) {
    return "To ostatni aktywny administrator.";
  }
  return null;
}

export function grantsElevated(before: Role[], after: Role[]): Role[] {
  return after.filter((r) => (r === "EXPERT" || r === "ADMIN") && !before.includes(r));
}
