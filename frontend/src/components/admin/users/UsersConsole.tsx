"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";

import { CreateUserDialog } from "@/components/admin/users/CreateUserDialog";
import { UserPanel } from "@/components/admin/users/UserPanel";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { cn } from "@/components/ui/cn";
import { DataTable, TBody, Td, Th, THead, Tr } from "@/components/ui/DataTable";
import { PageHeader } from "@/components/ui/PageHeader";
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

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 @4xl:w-auto";
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
  const client = useQueryClient();

  /** Opening a user (again) always shows fresh data: logins and Keycloak change it meanwhile. */
  function show(id: string) {
    setSelected(id);
    void client.invalidateQueries({ queryKey: ["admin", "users", "detail", id] });
  }

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
      <PageHeader
        title="Użytkownicy"
        description="Konta, hasła, blokady i role. Każda zmiana trafia do dziennika audytu."
        actions={
          <>
            <Button variant="secondary" busy={sync.isPending} onClick={() => void runSync()} title="Pobierz konta utworzone w konsoli Keycloak">
              <RefreshCw aria-hidden className="size-4" />
              Synchronizuj z Keycloak
            </Button>
            <Button onClick={() => setCreating(true)}>
              <UserPlus aria-hidden className="size-4" />
              Dodaj użytkownika
            </Button>
          </>
        }
      />

      <div className="grid gap-3 text-sm @xl:grid-cols-3 @4xl:flex @4xl:flex-wrap @4xl:items-end" role="search">
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

      {/* List and account side by side when there is room; the account alone on a phone (12.7). */}
      <div className={cn("grid gap-6", selected && "@4xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]")}>
        <div className={cn("flex min-w-0 flex-col gap-3", selected && "@max-4xl:hidden")}>
          {users.isPending ? (
            <p className="text-text-muted">Ładowanie…</p>
          ) : !page ? (
            <Card role="alert">{usersError(users.error, "Nie udało się wczytać użytkowników.")}</Card>
          ) : page.items.length === 0 ? (
            <p className="text-text-muted">Brak użytkowników spełniających kryteria.</p>
          ) : (
            <DataTable label="Użytkownicy">
              <THead>
                <Tr>
                  <Th>Użytkownik</Th>
                  <Th>Role</Th>
                  <Th>Status</Th>
                  <Th>Ostatnie logowanie</Th>
                </Tr>
              </THead>
              <TBody>
                {page.items.map((u) => (
                  <Tr key={u.id} selected={selected === u.id}>
                    <Td primary>
                      <button
                        type="button"
                        className="text-left font-medium text-primary-strong hover:underline"
                        aria-label={`Szczegóły: ${displayName(u)}`}
                        onClick={() => show(u.id)}
                      >
                        {displayName(u)}
                      </button>
                      <div className="text-xs text-text-muted">
                        {u.username ?? "—"}
                        {u.email ? ` · ${u.email}` : ""}
                      </div>
                    </Td>
                    <Td label="Role">
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
                    </Td>
                    <Td label="Status">
                      <Badge tone={STATUS[u.status].tone}>{STATUS[u.status].label}</Badge>
                    </Td>
                    <Td label="Ostatnie logowanie" className="text-text-muted">{u.last_login_at ? DATE.format(new Date(u.last_login_at)) : "nigdy"}</Td>
                  </Tr>
                ))}
              </TBody>
            </DataTable>
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
        {selected && (
          <div className="flex min-w-0 flex-col gap-3">
            <Button variant="ghost" className="self-start @4xl:hidden" onClick={() => setSelected(null)}>
              ← Lista użytkowników
            </Button>
            <UserPanel key={selected} userId={selected} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} onCreated={(id) => setSelected(id)} />
    </div>
  );
}
