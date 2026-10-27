"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useFormatter, useTranslations } from "next-intl";
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
  BLOCK_REASONS,
  blockReasonKey,
  displayName,
  grantsElevated,
  lockoutReason,
  requiredActionKey,
  ROLES,
  STATUS_TONE,
  useBlock,
  usePasswordReset,
  useRequirePasswordChange,
  useSetRoles,
  useSignOut,
  useUnblock,
  useUser,
  usersErrorKey,
  type BlockReason,
  type Lockout,
  type PasswordMode,
  type Role,
  type UserDetail,
} from "@/lib/users-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";
function useWhen() {
  const format = useFormatter();
  return (value: string | null | undefined) => (value ? format.dateTime(new Date(value), "dateTime") : "—");
}

type Open = "roles" | "block" | "unblock" | "reset" | "force" | "signout" | "unlock" | null;

/** Detail of one user and every action on them. The backend re-checks every rule. The parent
 * keys it by user id, so switching users starts with a clean state. */
export function UserPanel({ userId, onClose }: { userId: string; onClose: () => void }) {
  const t = useTranslations("adminUsers");
  const when = useWhen();
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
      else toast(t(`errors.${usersErrorKey(error)}`), "error");
      return false;
    }
  }

  if (user.isPending) return <PanelShell onClose={onClose}>{t("panel.loading")}</PanelShell>;
  if (!user.data) {
    return (
      <PanelShell onClose={onClose}>
        <p role="alert">{t(`errors.${usersErrorKey(user.error, "loadUser")}`)}</p>
      </PanelShell>
    );
  }
  const u = user.data;
  const lockout = lockoutReason(u);
  const lockoutText = lockout ? t(`lockout.${lockout}`) : null;
  const reasonKey = blockReasonKey(u.blocked_reason);
  const actions = (u.idp?.required_actions ?? u.required_actions).map((a) => {
    const key = requiredActionKey(a);
    return key ? t(`actions.${key}`) : a;
  });
  const external = (u.idp?.identity_providers.length ?? 0) > 0;

  return (
    <PanelShell onClose={onClose} title={displayName(u)}>
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-text-muted">{u.username ?? "—"}</span>
        <Badge tone={STATUS_TONE[u.status]}>{t(`statuses.${u.status}`)}</Badge>
        {u.is_self && <Badge>{t("panel.you")}</Badge>}
      </div>

      {stepUp && (
        <StepUpNotice
          stepUp={stepUp}
          reason={t("panel.stepUp")}
        />
      )}
      {u.idp === null && (
        <Card role="status" className="border-warn p-3 text-sm">
          {t("panel.idpDown")}
        </Card>
      )}

      <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1 text-sm break-words">
        <dt className="text-text-muted">{t("panel.email")}</dt>
        <dd>
          {u.idp?.email ?? u.email ?? "—"}
          {u.idp?.email && !u.idp.email_verified && <span className="text-text-muted">{t("panel.unverified")}</span>}
        </dd>
        <dt className="text-text-muted">{t("panel.lastLogin")}</dt>
        <dd>{when(u.last_login_at)}</dd>
        <dt className="text-text-muted">{t("panel.since")}</dt>
        <dd>{when(u.created_at)}</dd>
        <dt className="text-text-muted">{t("panel.mfa")}</dt>
        <dd>{u.idp ? (u.idp.totp ? t("panel.totp") : t("panel.noTotp")) : "—"}</dd>
        {actions.length > 0 && (
          <>
            <dt className="text-text-muted">{t("panel.atLogin")}</dt>
            <dd>{actions.join(", ")}</dd>
          </>
        )}
        {external && (
          <>
            <dt className="text-text-muted">{t("panel.via")}</dt>
            <dd>{u.idp?.identity_providers.join(", ")}</dd>
          </>
        )}
      </dl>

      {u.status === "blocked" && (
        <Card className="border-danger p-3 text-sm">
          <p className="font-medium">{t("panel.blocked", { reason: reasonKey ? t(`blockReasons.${reasonKey}`) : (u.blocked_reason ?? "—") })}</p>
          <p className="text-text-muted">{when(u.blocked_at)}</p>
          {u.blocked_note && <p className="mt-1">{u.blocked_note}</p>}
        </Card>
      )}
      {u.locked_reason && (
        <Card className="border-warn p-3 text-sm">
          <p className="font-medium">{t("panel.shieldLocked", { reason: u.locked_reason })}</p>
          <p className="text-text-muted">{when(u.locked_at)}</p>
          <Button variant="secondary" className="mt-2" onClick={() => setOpen("unlock")}>
            {t("panel.shieldUnlock")}
          </Button>
        </Card>
      )}

      {/* Keyed by the saved roles: a successful save or a reload resets the checkboxes. */}
      <RolesEditor key={u.roles.join()} user={u} lockout={lockout} run={run} />

      <section aria-labelledby="user-actions" className="flex flex-col gap-2">
        <h3 id="user-actions" className="font-medium">
          {t("panel.access")}
        </h3>
        <div className="flex flex-wrap gap-2">
          {u.status === "blocked" ? (
            <Button variant="secondary" onClick={() => setOpen("unblock")}>
              {t("panel.unblock")}
            </Button>
          ) : (
            <Button variant="danger" disabled={lockout !== null} title={lockoutText ?? undefined} onClick={() => setOpen("block")}>
              {t("panel.block")}
            </Button>
          )}
          <Button variant="secondary" onClick={() => setOpen("signout")}>
            {t("panel.signOutAll")}
          </Button>
        </div>
        {lockoutText && u.status !== "blocked" && <p className="text-xs text-text-muted">{t("panel.cannotBlock", { reason: lockoutText })}</p>}
      </section>

      <section aria-labelledby="user-password" className="flex flex-col gap-2">
        <h3 id="user-password" className="font-medium">
          {t("panel.password")}
        </h3>
        {external ? (
          <p className="text-sm text-text-muted">{t("panel.external")}</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="secondary" onClick={() => setOpen("reset")}>
              {t("panel.reset")}
            </Button>
            <Button variant="secondary" onClick={() => setOpen("force")}>
              {t("panel.force")}
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
  const t = useTranslations("adminUsers.panel");
  const common = useTranslations("common");
  return (
    <section aria-label={t("region")} className="flex flex-col gap-4 rounded-xl border border-border bg-surface p-4">
      <div className="flex items-start justify-between gap-2">
        <h2 className="font-display text-lg font-semibold">{title ?? t("userTitle")}</h2>
        <Button variant="ghost" onClick={onClose}>
          {common("close")}
        </Button>
      </div>
      {children}
    </section>
  );
}

type Run = (work: () => Promise<unknown>, ok?: string) => Promise<boolean>;

function RolesEditor({ user, lockout, run }: { user: UserDetail; lockout: Lockout | null; run: Run }) {
  const t = useTranslations("adminUsers");
  const common = useTranslations("common");
  const [roles, setRoles] = useState<Role[]>(user.roles);
  const [confirming, setConfirming] = useState(false);
  const setRolesMutation = useSetRoles();

  const changed = ROLES.some((r) => roles.includes(r) !== user.roles.includes(r));
  const granted = grantsElevated(user.roles, roles);

  function toggle(role: Role) {
    setRoles((current) => (current.includes(role) ? current.filter((r) => r !== role) : [...current, role]));
  }

  async function save() {
    const ok = await run(() => setRolesMutation.mutateAsync({ id: user.id, roles }), t("rolesEditor.saved"));
    setConfirming(false);
    if (!ok) setRoles(user.roles);
  }

  return (
    <section aria-labelledby="user-roles" className="flex flex-col gap-2">
      <h3 id="user-roles" className="font-medium">
        {t("rolesEditor.title")}
      </h3>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="sr-only">{t("rolesEditor.legend")}</legend>
        {ROLES.map((r) => {
          const lockedAdmin = r === "ADMIN" && user.roles.includes("ADMIN") && lockout !== null;
          return (
            <label key={r} className="flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-1"
                checked={roles.includes(r)}
                disabled={r === "MEMBER" || lockedAdmin}
                onChange={() => toggle(r)}
              />
              <span>
                {t(`roleNames.${r}`)}
                <span className="text-xs text-text-muted">
                  {" · "}
                  {lockedAdmin && lockout ? t("rolesEditor.cannotRemove", { reason: t(`lockout.${lockout}`) }) : t(`roleHints.${r}`)}
                </span>
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
          {t("rolesEditor.save")}
        </Button>
      </div>
      <Dialog open={confirming} onClose={() => setConfirming(false)} title={t("rolesEditor.confirmTitle")}>
        <div className="flex flex-col gap-3 text-sm">
          <p>
            {t.rich("rolesEditor.granting", {
              roles: granted.map((r) => t(`roleNames.${r}`)).join(", "),
              name: displayName(user),
              strong: (chunks) => <strong>{chunks}</strong>,
            })}
          </p>
          {user.idp && !user.idp.totp && <p className="text-warn">{t("rolesEditor.mfaNext")}</p>}
          <p className="text-text-muted">{t("rolesEditor.audited")}</p>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirming(false)}>
              {common("cancel")}
            </Button>
            <Button busy={setRolesMutation.isPending} onClick={() => void save()}>
              {t("rolesEditor.grant")}
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
  const t = useTranslations("adminUsers");
  const common = useTranslations("common");
  const [reason, setReason] = useState<BlockReason>("left_cooperative");
  const [note, setNote] = useState("");
  const block = useBlock();

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (await run(() => block.mutateAsync({ id: user.id, reason, note }), t("blockDialog.done"))) {
      setNote("");
      onClose();
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title={t("blockDialog.title")}>
      <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
        <p>{t("blockDialog.body", { name: displayName(user) })}</p>
        <label className="flex flex-col gap-1">
          {t("blockDialog.reason")}
          <select className={INPUT} value={reason} onChange={(e) => setReason(e.target.value as BlockReason)}>
            {BLOCK_REASONS.map((r) => (
              <option key={r} value={r}>
                {t(`blockReasons.${r}`)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-1">
          {t("blockDialog.note")}
          <textarea className={INPUT} rows={2} maxLength={200} value={note} onChange={(e) => setNote(e.target.value)} />
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button type="submit" variant="danger" busy={block.isPending}>
            {t("blockDialog.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}

function UnblockDialog({ user, open, onClose, run }: ActionDialogProps) {
  const t = useTranslations("adminUsers.unblockDialog");
  const common = useTranslations("common");
  const unblock = useUnblock();
  return (
    <Dialog open={open} onClose={onClose} title={t("title")}>
      <div className="flex flex-col gap-3 text-sm">
        <p>{t("body", { name: displayName(user) })}</p>
        {user.locked_reason && <p className="text-warn">{t("shieldStays")}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button
            busy={unblock.isPending}
            onClick={() => void run(() => unblock.mutateAsync(user.id), t("done")).then((ok) => ok && onClose())}
          >
            {t("submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function ResetDialog({ user, open, onClose, run }: ActionDialogProps) {
  const t = useTranslations("adminUsers.resetDialog");
  const common = useTranslations("common");
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
    }, mode === "email" ? t("linkSent") : undefined);
  }

  return (
    <Dialog open={open} onClose={close} title={issued ? t("issuedTitle") : t("title")}>
      {issued ? (
        <div className="flex flex-col gap-4">
          <OneTimePassword password={issued} username={user.username} />
          <div className="flex justify-end">
            <Button onClick={close}>{t("done")}</Button>
          </div>
        </div>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm">
          <p>{t("body", { name: displayName(user) })}</p>
          <fieldset className="flex flex-col gap-1">
            <legend className="sr-only">{t("method")}</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="reset-mode" checked={mode === "email"} disabled={!email} onChange={() => setMode("email")} />
              {email ? t("byEmail", { email }) : t("byEmailNone")}
            </label>
            <label className="flex items-center gap-2">
              <input type="radio" name="reset-mode" checked={mode === "temporary"} onChange={() => setMode("temporary")} />
              {t("temporary")}
            </label>
          </fieldset>
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {common("cancel")}
            </Button>
            <Button type="submit" busy={reset.isPending}>
              {t("submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

function ForceChangeDialog({ user, open, onClose, run }: ActionDialogProps) {
  const t = useTranslations("adminUsers.forceDialog");
  const common = useTranslations("common");
  const [signOut, setSignOut] = useState(false);
  const force = useRequirePasswordChange();
  return (
    <Dialog open={open} onClose={onClose} title={t("title")}>
      <div className="flex flex-col gap-3 text-sm">
        <p>{t("body", { name: displayName(user) })}</p>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={signOut} onChange={(e) => setSignOut(e.target.checked)} />
          {t("signOut")}
        </label>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button
            busy={force.isPending}
            onClick={() =>
              void run(() => force.mutateAsync({ id: user.id, signOut }), t("done")).then(
                (ok) => ok && onClose(),
              )
            }
          >
            {t("submit")}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

function SignOutDialog({ user, open, onClose, run }: ActionDialogProps) {
  const t = useTranslations("adminUsers.signOutDialog");
  const common = useTranslations("common");
  const signOut = useSignOut();
  return (
    <Dialog open={open} onClose={onClose} title={t("title")}>
      <div className="flex flex-col gap-3 text-sm">
        <p>{t("body", { name: displayName(user) })}</p>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose}>
            {common("cancel")}
          </Button>
          <Button
            busy={signOut.isPending}
            onClick={() => void run(() => signOut.mutateAsync(user.id), t("done")).then((ok) => ok && onClose())}
          >
            {t("submit")}
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
