"use client";

import { useEffect, useState } from "react";

import { CreateUserDialog } from "@/components/admin/users/CreateUserDialog";
import { UserPanel } from "@/components/admin/users/UserPanel";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  displayName,
  PAGE_SIZE,
  ROLE_LABEL,
  ROLES,
  STATUS,
  useSyncUsers,
  useUsers,
  usersError,
  type Role,
  type UserFilters,
  type UserStatus,
} from "@/lib/users-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";
const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short" });

/**
 * Who can use the cooperative's system (Stage 8): accounts, passwords, blocks and roles.
 * Keycloak keeps passwords; this console never sees one except a temporary password, once.
 */
export function UsersConsole() {
  const [filters, setFilters] = useState<UserFilters>({ q: "", role: "", status: "", offset: 0 });
  const [search, setSearch] = useState("");
  // `/admin/users?user=<id>` opens a user (links from the security dashboard). The console
  // renders only in the browser (after RequireRole has the session), so `window` is there.
  const [selected, setSelected] = useState<string | null>(() =>
    typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get("user"),
  );
  const [creating, setCreating] = useState(false);
  const users = useUsers(filters);
  const sync = useSyncUsers();
  const toast = useToast();

  // Search as you type, without a request per keystroke.
  useEffect(() => {
    const timer = setTimeout(() => setFilters((f) => (f.q === search.trim() ? f : { ...f, q: search.trim(), offset: 0 })), 300);
    return () => clearTimeout(timer);
  }, [search]);

  if (users.error instanceof StepUpRequired) return <StepUpNotice stepUp={users.error.stepUp} />;

  async function runSync() {
    try {
      const report = await sync.mutateAsync();
      toast(`Synchronizacja: ${report.seen} kont w Keycloak, nowych ${report.created}, zmienionych ${report.updated}.`, "ok");
    } catch (error) {
      toast(usersError(error), "error");
    }
  }

  const page = users.data;
  const from = page && page.total > 0 ? filters.offset + 1 : 0;
  const to = page ? Math.min(filters.offset + PAGE_SIZE, page.total) : 0;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="font-display text-2xl font-semibold">Użytkownicy</h1>
          <p className="text-sm text-text-muted">
            Konta, hasła, blokady i role. Każda zmiana trafia do dziennika audytu.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" busy={sync.isPending} onClick={() => void runSync()} title="Pobierz konta utworzone w konsoli Keycloak">
            Synchronizuj z Keycloak
          </Button>
          <Button onClick={() => setCreating(true)}>Dodaj użytkownika</Button>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-3 text-sm" role="search">
        <label className="flex flex-col gap-1">
          Szukaj
          <input
            type="search"
            className={INPUT}
            placeholder="nazwa, imię lub e-mail"
            value={search}
            maxLength={100}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          Rola
          <select
            className={INPUT}
            value={filters.role}
            onChange={(e) => setFilters({ ...filters, role: e.target.value as Role | "", offset: 0 })}
          >
            <option value="">wszystkie</option>
            {ROLES.map((r) => (
              <option key={r.role} value={r.role}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Status
          <select
            className={INPUT}
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value as UserStatus | "", offset: 0 })}
          >
            <option value="">wszystkie</option>
            {(Object.keys(STATUS) as UserStatus[]).map((s) => (
              <option key={s} value={s}>
                {STATUS[s].label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className={cn("grid gap-6", selected && "lg:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]")}>
        <div className="flex min-w-0 flex-col gap-3">
          {users.isPending ? (
            <p className="text-text-muted">Ładowanie…</p>
          ) : !page ? (
            <Card role="alert">{usersError(users.error, "Nie udało się wczytać użytkowników.")}</Card>
          ) : page.items.length === 0 ? (
            <p className="text-text-muted">Brak użytkowników spełniających kryteria.</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm" aria-label="Użytkownicy">
                <thead className="text-text-muted">
                  <tr>
                    <th className="py-2 pr-3">Użytkownik</th>
                    <th className="py-2 pr-3">Role</th>
                    <th className="py-2 pr-3">Status</th>
                    <th className="py-2">Ostatnie logowanie</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((u) => (
                    <tr key={u.id} className={cn("border-t border-border align-top", selected === u.id && "bg-surface-muted")}>
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          className="text-left font-medium text-primary-strong hover:underline"
                          aria-label={`Szczegóły: ${displayName(u)}`}
                          onClick={() => setSelected(u.id)}
                        >
                          {displayName(u)}
                        </button>
                        <div className="text-xs text-text-muted">
                          {u.username ?? "—"}
                          {u.email ? ` · ${u.email}` : ""}
                        </div>
                      </td>
                      <td className="py-2 pr-3">
                        <span className="flex flex-wrap gap-1">
                          {u.roles
                            .filter((r) => r !== "MEMBER")
                            .map((r) => (
                              <Badge key={r} tone={r === "ADMIN" ? "accent" : "primary"}>
                                {ROLE_LABEL[r]}
                              </Badge>
                            ))}
                          {u.roles.every((r) => r === "MEMBER") && <span className="text-text-muted">{ROLE_LABEL.MEMBER}</span>}
                        </span>
                      </td>
                      <td className="py-2 pr-3">
                        <Badge tone={STATUS[u.status].tone}>{STATUS[u.status].label}</Badge>
                      </td>
                      <td className="py-2 text-text-muted">{u.last_login_at ? DATE.format(new Date(u.last_login_at)) : "nigdy"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {page && page.total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="text-text-muted">
                {from}–{to} z {page.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  disabled={filters.offset === 0}
                  onClick={() => setFilters({ ...filters, offset: Math.max(0, filters.offset - PAGE_SIZE) })}
                >
                  Poprzednia
                </Button>
                <Button
                  variant="secondary"
                  disabled={to >= page.total}
                  onClick={() => setFilters({ ...filters, offset: filters.offset + PAGE_SIZE })}
                >
                  Następna
                </Button>
              </div>
            </div>
          )}
        </div>
        {selected && <UserPanel key={selected} userId={selected} onClose={() => setSelected(null)} />}
      </div>

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} onCreated={(id) => setSelected(id)} />
    </div>
  );
}
