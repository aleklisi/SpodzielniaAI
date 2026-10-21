"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent, type ReactNode } from "react";

import { UnlockDialog } from "@/components/admin/UnlockDialog";
import { OneTimePassword } from "@/components/admin/users/OneTimePassword";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import {
  actionsLabel,
  BLOCK_REASONS,
  blockReasonLabel,
  displayName,
  grantsElevated,
  lockoutReason,
  ROLE_LABEL,
  ROLES,
  STATUS,
  useBlock,
  usePasswordReset,
  useRequirePasswordChange,
  useSetRoles,
  useSignOut,
  useUnblock,
  useUser,
  usersError,
  type BlockReason,
  type PasswordMode,
  type Role,
  type UserDetail,
} from "@/lib/users-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";
const DATE = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium", timeStyle: "short" });

function when(value: string | null | undefined): string {
  return value ? DATE.format(new Date(value)) : "—";
}

type Open = "roles" | "block" | "unblock" | "reset" | "force" | "signout" | "unlock" | null;

/** Detail of one user and every action on them. The backend re-checks every rule. The parent
 * keys it by user id, so switching users starts with a clean state. */
export function UserPanel({ userId, onClose }: { userId: string; onClose: () => void }) {
  const user = useUser(userId);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const [open, setOpen] = useState<Open>(null);
  const toast = useToast();

  /** Run an action; a step-up turns into the notice, other errors into a toast. */
  async function run(work: () => Promise<unknown>, ok?: string): Promise<boolean> {
    try {
      await work();
      if (ok) toast(ok, "ok");
      return true;
    } catch (error) {
      if (error instanceof StepUpRequired) setStepUp(error.stepUp);
      else toast(usersError(error), "error");
      return false;
    }
  }

  if (user.isPending) return <PanelShell onClose={onClose}>Ładowanie…</PanelShell>;
  if (!user.data) {
    return (
      <PanelShell onClose={onClose}>
        <p role="alert">{usersError(user.error, "Nie udało się wczytać użytkownika.")}</p>
      </PanelShell>
    );
  }
  const u = user.data;
  const status = STATUS[u.status];
  const lockout = lockoutReason(u);
  const external = (u.idp?.identity_providers.length ?? 0) > 0;

  return (
    <PanelShell onClose={onClose} title={displayName(u)}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-text-muted">{u.username ?? "—"}</span>
        <Badge tone={status.tone}>{status.label}</Badge>
        {u.is_self && <Badge>To Ty</Badge>}
      </div>

      {stepUp && (
        <StepUpNotice
          stepUp={stepUp}
          reason="Nadanie roli eksperta lub administratora i odblokowanie konta wymagają świeżego logowania z drugim składnikiem."
        />
      )}
      {u.idp === null && (
        <Card role="status" className="border-warn p-3 text-sm">
          Keycloak jest niedostępny: pokazujemy zapisaną kopię danych. Akcje mogą się nie udać.
        </Card>
      )}

      <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
        <dt className="text-text-muted">E-mail</dt>
        <dd>
          {u.idp?.email ?? u.email ?? "—"}
          {u.idp?.email && !u.idp.email_verified && <span className="text-text-muted"> (niepotwierdzony)</span>}
        </dd>
        <dt className="text-text-muted">Ostatnie logowanie</dt>
        <dd>{when(u.last_login_at)}</dd>
        <dt className="text-text-muted">Konto od</dt>
        <dd>{when(u.created_at)}</dd>
        <dt className="text-text-muted">MFA</dt>
        <dd>{u.idp ? (u.idp.totp ? "aplikacja do kodów skonfigurowana" : "brak") : "—"}</dd>
        {(u.idp?.required_actions.length ?? u.required_actions.length) > 0 && (
          <>
            <dt className="text-text-muted">Przy logowaniu</dt>
            <dd>{actionsLabel(u.idp?.required_actions ?? u.required_actions)}</dd>
          </>
        )}
        {external && (
          <>
            <dt className="text-text-muted">Logowanie przez</dt>
            <dd>{u.idp?.identity_providers.join(", ")}</dd>
          </>
        )}
      </dl>

      {u.status === "blocked" && (
        <Card className="border-danger p-3 text-sm">
          <p className="font-medium">Zablokowane: {blockReasonLabel(u.blocked_reason)}</p>
          <p className="text-text-muted">{when(u.blocked_at)}</p>
          {u.blocked_note && <p className="mt-1">{u.blocked_note}</p>}
        </Card>
      )}
      {u.locked_reason && (
        <Card className="border-warn p-3 text-sm">
          <p className="font-medium">System ochrony zablokował konto: {u.locked_reason}</p>
          <p className="text-text-muted">{when(u.locked_at)}</p>
          <Button variant="secondary" className="mt-2" onClick={() => setOpen("unlock")}>
            Odblokuj (system ochrony)
          </Button>
        </Card>
      )}

      {/* Keyed by the saved roles: a successful save or a reload resets the checkboxes. */}
      <RolesEditor key={u.roles.join()} user={u} lockout={lockout} run={run} />

      <section aria-labelledby="user-actions" className="flex flex-col gap-2">
        <h3 id="user-actions" className="font-medium">
          Dostęp
        </h3>
        <div className="flex flex-wrap gap-2">
          {u.status === "blocked" ? (
            <Button variant="secondary" onClick={() => setOpen("unblock")}>
              Odblokuj
            </Button>
          ) : (
            <Button variant="danger" disabled={lockout !== null} title={lockout ?? undefined} onClick={() => setOpen("block")}>
              Zablokuj
            </Button>
          )}
          <Button variant="secondary" onClick={() => setOpen("signout")}>
            Wyloguj ze wszystkich urządzeń
          </Button>
        </div>
        {lockout && u.status !== "blocked" && <p className="text-xs text-text-muted">Nie można zablokować: {lockout}</p>}
      </section>

      <section aria-labelledby="user-password" className="flex flex-col gap-2">
        <h3 id="user-password" className="font-medium">
          Hasło
        </h3>
        {external ? (
          <p className="text-sm text-text-muted">Konto loguje się przez zewnętrznego dostawcę; tam zmienia się hasło.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setOpen("reset")}>
              Resetuj hasło
            </Button>
            <Button variant="secondary" onClick={() => setOpen("force")}>
              Wymuś zmianę hasła
            </Button>
          </div>
        )}
      </section>

      <BlockDialog user={u} open={open === "block"} onClose={() => setOpen(null)} run={run} />
      <UnblockDialog user={u} open={open === "unblock"} onClose={() => setOpen(null)} run={run} />
      <ResetDialog user={u} open={open === "reset"} onClose={() => setOpen(null)} run={run} />
      <ForceChangeDialog user={u} open={open === "force"} onClose={() => setOpen(null)} run={run} />
      <SignOutDialog user={u} open={open === "signout"} onClose={() => setOpen(null)} run={run} />
      <ShieldUnlock user={u} open={open === "unlock"} onClose={() => setOpen(null)} />
    </PanelShell>
  );
}

function PanelShell({ title, onClose, children }: { title?: string; onClose: () => void; children: ReactNode }) {
  return (
    <section aria-label="Szczegóły użytkownika" className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-display text-lg font-semibold">{title ?? "Użytkownik"}</h2>
        <Button variant="ghost" onClick={onClose}>
          Zamknij
        </Button>
      </div>
      {children}
    </section>
  );
}

type Run = (work: () => Promise<unknown>, ok?: string) => Promise<boolean>;

function RolesEditor({ user, lockout, run }: { user: UserDetail; lockout: string | null; run: Run }) {
  const [roles, setRoles] = useState<Role[]>(user.roles);
  const [confirming, setConfirming] = useState(false);
  const setRolesMutation = useSetRoles();

  const changed = ROLES.some((r) => roles.includes(r.role) !== user.roles.includes(r.role));
  const granted = grantsElevated(user.roles, roles);

  function toggle(role: Role) {
    setRoles((current) => (current.includes(role) ? current.filter((r) => r !== role) : [...current, role]));
  }

  async function save() {
    const ok = await run(() => setRolesMutation.mutateAsync({ id: user.id, roles }), "Role zapisane.");
    setConfirming(false);
    if (!ok) setRoles(user.roles);
  }

  return (
    <section aria-labelledby="user-roles" className="flex flex-col gap-2">
      <h3 id="user-roles" className="font-medium">
        Role
      </h3>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="sr-only">Role użytkownika</legend>
        {ROLES.map((r) => {
          const lockedAdmin = r.role === "ADMIN" && user.roles.includes("ADMIN") && lockout !== null;
          return (
            <label key={r.role} className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={roles.includes(r.role)}
                disabled={r.role === "MEMBER" || lockedAdmin}
                onChange={() => toggle(r.role)}
              />
              <span>
                {r.label}
                <span className="text-xs text-text-muted"> · {lockedAdmin ? `Nie można odebrać: ${lockout}` : r.hint}</span>
              </span>
            </label>
          );
        })}
      </fieldset>
      <div className="flex justify-end">
        <Button
          disabled={!changed}
          busy={setRolesMutation.isPending}
          onClick={() => (granted.length > 0 ? setConfirming(true) : void save())}
        >
          Zapisz role
        </Button>
      </div>
      <Dialog open={confirming} onClose={() => setConfirming(false)} title="Nadać uprawnienia?">
        <div className="flex flex-col gap-3 text-sm">
          <p>
            Nadajesz rolę: <strong>{granted.map((r) => ROLE_LABEL[r]).join(", ")}</strong> użytkownikowi {displayName(user)}.
          </p>
          {user.idp && !user.idp.totp && (
            <p className="text-warn">Przy następnym logowaniu użytkownik skonfiguruje aplikację do kodów (MFA).</p>
          )}
          <p className="text-text-muted">Zmiana zostanie zapisana w dzienniku audytu.</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              Anuluj
            </Button>
            <Button busy={setRolesMutation.isPending} onClick={() => void save()}>
              Nadaj
            </Button>
          </div>
        </div>
      </Dialog>
    </section>
  );
}

interface ActionDialogProps {
  user: UserDetail;
  open: boolean;
  onClose: () => void;
  run: Run;
}

function BlockDialog({ user, open, onClose, run }: ActionDialogProps) {
  const [reason, setReason] = useState<BlockReason>("left_cooperative");
  const [note, setNote] = useState("");
  const block = useBlock();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run(() => block.mutateAsync({ id: user.id, reason, note }), "Konto zablokowane, sesje zakończone.")) {
      setNote("");
      onClose();
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="Zablokować konto?">
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
        <p>
          {displayName(user)} nie zaloguje się, a wszystkie otwarte sesje zakończą się od razu. Treści i autorstwo zostają bez
          zmian.
        </p>
        <label className="flex flex-col gap-1">
          Powód
          <select className={INPUT} value={reason} onChange={(e) => setReason(e.target.value as BlockReason)}>
            {BLOCK_REASONS.map((r) => (
              <option key={r.reason} value={r.reason}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          Notatka (widoczna tylko dla administratorów)
          <textarea className={INPUT} rows={2} maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button type="submit" variant="danger" busy={block.isPending}>
            Zablokuj konto
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function UnblockDialog({ user, open, onClose, run }: ActionDialogProps) {
  const unblock = useUnblock();
  return (
    <Dialog open={open} onClose={onClose} title="Odblokować konto?">
      <div className="flex flex-col gap-3 text-sm">
        <p>{displayName(user)} znów będzie mógł się zalogować.</p>
        {user.locked_reason && (
          <p className="text-warn">Blokada systemu ochrony zostaje; zdejmiesz ją osobno, po przejrzeniu historii ryzyka.</p>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button
            busy={unblock.isPending}
            onClick={() => void run(() => unblock.mutateAsync(user.id), "Konto odblokowane.").then((ok) => ok && onClose())}
          >
            Odblokuj
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ResetDialog({ user, open, onClose, run }: ActionDialogProps) {
  const email = user.idp?.email ?? user.email;
  const [mode, setMode] = useState<PasswordMode>(email ? "email" : "temporary");
  const [issued, setIssued] = useState<string | null>(null);
  const reset = usePasswordReset();

  function close() {
    setIssued(null);
    onClose();
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    await run(async () => {
      const result = await reset.mutateAsync({ id: user.id, mode });
      if (result.temporary_password) setIssued(result.temporary_password);
      else close();
    }, mode === "email" ? "Wysłaliśmy link do ustawienia nowego hasła." : undefined);
  }

  return (
    <Dialog open={open} onClose={close} title={issued ? "Nowe hasło tymczasowe" : "Resetuj hasło"}>
      {issued ? (
        <div className="flex flex-col gap-4">
          <OneTimePassword password={issued} username={user.username} />
          <div className="flex justify-end">
            <Button onClick={close}>Gotowe</Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
          <p>Obecne hasło przestanie działać, a {displayName(user)} zostanie wylogowany ze wszystkich urządzeń.</p>
          <fieldset className="flex flex-col gap-1">
            <legend className="sr-only">Sposób</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="reset-mode" checked={mode === "email"} disabled={!email} onChange={() => setMode("email")} />
              Link e-mailem{email ? ` na ${email}` : " (brak adresu)"}
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="reset-mode" checked={mode === "temporary"} onChange={() => setMode("temporary")} />
              Hasło tymczasowe (pokażemy je raz)
            </label>
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              Anuluj
            </Button>
            <Button type="submit" busy={reset.isPending}>
              Resetuj
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function ForceChangeDialog({ user, open, onClose, run }: ActionDialogProps) {
  const [signOut, setSignOut] = useState(false);
  const force = useRequirePasswordChange();
  return (
    <Dialog open={open} onClose={onClose} title="Wymusić zmianę hasła?">
      <div className="flex flex-col gap-3 text-sm">
        <p>Przy następnym logowaniu {displayName(user)} musi ustawić nowe hasło.</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={signOut} onChange={(e) => setSignOut(e.target.checked)} />
          Wyloguj teraz ze wszystkich urządzeń
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button
            busy={force.isPending}
            onClick={() =>
              void run(() => force.mutateAsync({ id: user.id, signOut }), "Zmiana hasła będzie wymagana przy logowaniu.").then(
                (ok) => ok && onClose(),
              )
            }
          >
            Wymuś
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function SignOutDialog({ user, open, onClose, run }: ActionDialogProps) {
  const signOut = useSignOut();
  return (
    <Dialog open={open} onClose={onClose} title="Wylogować ze wszystkich urządzeń?">
      <div className="flex flex-col gap-3 text-sm">
        <p>Wszystkie sesje {displayName(user)} zakończą się od razu. Konto pozostaje aktywne.</p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            Anuluj
          </Button>
          <Button
            busy={signOut.isPending}
            onClick={() => void run(() => signOut.mutateAsync(user.id), "Sesje zakończone.").then((ok) => ok && onClose())}
          >
            Wyloguj
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ShieldUnlock({ user, open, onClose }: { user: UserDetail; open: boolean; onClose: () => void }) {
  const client = useQueryClient();
  return (
    <UnlockDialog
      user={open ? { user_id: user.id, display_name: user.display_name, email: user.email, locked_reason: user.locked_reason } : null}
      onClose={() => {
        void client.invalidateQueries({ queryKey: ["admin", "users"] });
        onClose();
      }}
    />
  );
}
