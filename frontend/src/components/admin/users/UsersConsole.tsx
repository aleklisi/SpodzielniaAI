"use client";

import { useQueryClient } from "@tanstack/react-query";
import { RefreshCw, UserPlus } from "lucide-react";
import { useFormatter, useTranslations } from "next-intl";
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
  ROLES,
  STATUS_TONE,
  useSyncUsers,
  useUsers,
  usersErrorKey,
  type Role,
  type UserFilters,
  type UserStatus,
} from "@/lib/users-admin";

const INPUT = "w-full rounded-lg border border-border bg-surface px-3 py-2 @4xl:w-auto";

/**
 * Who can use the cooperative's system (Stage 8): accounts, passwords, blocks and roles.
 * Keycloak keeps passwords; this console never sees one except a temporary password, once.
 */
export function UsersConsole() {
  const t = useTranslations("adminUsers");
  const format = useFormatter();
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
      toast(t("synced", { seen: report.seen, created: report.created, updated: report.updated }), "ok");
    } catch (error) {
      toast(t(`errors.${usersErrorKey(error)}`), "error");
    }
  }

  const page = users.data;
  const from = page && page.total > 0 ? filters.offset + 1 : 0;
  const to = page ? Math.min(filters.offset + PAGE_SIZE, page.total) : 0;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={t("title")}
        description={t("description")}
        actions={
          <>
            <Button variant="secondary" busy={sync.isPending} onClick={() => void runSync()} title={t("syncHint")}>
              <RefreshCw aria-hidden className="size-4" />
              {t("sync")}
            </Button>
            <Button onClick={() => setCreating(true)}>
              <UserPlus aria-hidden className="size-4" />
              {t("add")}
            </Button>
          </>
        }
      />

      <div className="grid gap-3 text-sm @xl:grid-cols-3 @4xl:flex @4xl:flex-wrap @4xl:items-end" role="search">
        <label className="flex flex-col gap-1">
          {t("search")}
          <input
            type="search"
            className={INPUT}
            placeholder={t("searchPlaceholder")}
            value={search}
            maxLength={100}
            onChange={(e) => setSearch(e.target.value)}
          />
        </label>
        <label className="flex flex-col gap-1">
          {t("role")}
          <select
            className={INPUT}
            value={filters.role}
            onChange={(e) => setFilters({ ...filters, role: e.target.value as Role | "", offset: 0 })}
          >
            <option value="">{t("all")}</option>
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(`roleNames.${r}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("status")}
          <select
            className={INPUT}
            value={filters.status}
            onChange={(e) => setFilters({ ...filters, status: e.target.value as UserStatus | "", offset: 0 })}
          >
            <option value="">{t("all")}</option>
            {(Object.keys(STATUS_TONE) as UserStatus[]).map((s) => (
              <option key={s} value={s}>
                {t(`statuses.${s}`)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* List and account side by side when there is room; the account alone on a phone (12.7). */}
      <div className={cn("grid gap-6", selected && "@4xl:grid-cols-[minmax(0,1fr)_minmax(0,26rem)]")}>
        <div className={cn("flex min-w-0 flex-col gap-3", selected && "@max-4xl:hidden")}>
          {users.isPending ? (
            <p className="text-text-muted">{t("loading")}</p>
          ) : !page ? (
            <Card role="alert">{t(`errors.${usersErrorKey(users.error, "failed")}`)}</Card>
          ) : page.items.length === 0 ? (
            <p className="text-text-muted">{t("empty")}</p>
          ) : (
            <DataTable label={t("table")}>
              <THead>
                <Tr>
                  <Th>{t("user")}</Th>
                  <Th>{t("roles")}</Th>
                  <Th>{t("status")}</Th>
                  <Th>{t("lastLogin")}</Th>
                </Tr>
              </THead>
              <TBody>
                {page.items.map((u) => (
                  <Tr key={u.id} selected={selected === u.id}>
                    <Td primary>
                      <button
                        type="button"
                        className="text-left font-medium text-primary-strong hover:underline"
                        aria-label={t("details", { name: displayName(u) })}
                        onClick={() => show(u.id)}
                      >
                        {displayName(u)}
                      </button>
                      <div className="text-xs text-text-muted">
                        {u.username ?? "—"}
                        {u.email ? ` · ${u.email}` : ""}
                      </div>
                    </Td>
                    <Td label={t("roles")}>
                      <span className="flex flex-wrap gap-1">
                        {u.roles
                          .filter((r) => r !== "MEMBER")
                          .map((r) => (
                            <Badge key={r} tone={r === "ADMIN" ? "accent" : "primary"}>
                              {t(`roleNames.${r}`)}
                            </Badge>
                          ))}
                        {u.roles.every((r) => r === "MEMBER") && <span className="text-text-muted">{t("roleNames.MEMBER")}</span>}
                      </span>
                    </Td>
                    <Td label={t("status")}>
                      <Badge tone={STATUS_TONE[u.status]}>{t(`statuses.${u.status}`)}</Badge>
                    </Td>
                    <Td label={t("lastLogin")} className="text-text-muted">
                      {u.last_login_at ? format.dateTime(new Date(u.last_login_at), "short") : t("never")}
                    </Td>
                  </Tr>
                ))}
              </TBody>
            </DataTable>
          )}
          {page && page.total > PAGE_SIZE && (
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="text-text-muted">{t("range", { from, to, total: page.total })}</span>
              <div className="flex gap-2">
                <Button
                  variant="secondary"
                  disabled={filters.offset === 0}
                  onClick={() => setFilters({ ...filters, offset: Math.max(0, filters.offset - PAGE_SIZE) })}
                >
                  {t("previous")}
                </Button>
                <Button
                  variant="secondary"
                  disabled={to >= page.total}
                  onClick={() => setFilters({ ...filters, offset: filters.offset + PAGE_SIZE })}
                >
                  {t("next")}
                </Button>
              </div>
            </div>
          )}
        </div>
        {selected && (
          <div className="flex min-w-0 flex-col gap-3">
            <Button variant="ghost" className="self-start @4xl:hidden" onClick={() => setSelected(null)}>
              {t("backToList")}
            </Button>
            <UserPanel key={selected} userId={selected} onClose={() => setSelected(null)} />
          </div>
        )}
      </div>

      <CreateUserDialog open={creating} onClose={() => setCreating(false)} onCreated={(id) => setSelected(id)} />
    </div>
  );
}
