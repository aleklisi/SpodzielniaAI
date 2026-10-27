"use client";

import { useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { OneTimePassword } from "@/components/admin/users/OneTimePassword";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { ROLES, useCreateUser, usersErrorKey, type PasswordMode, type Role, type UsersErrorKey } from "@/lib/users-admin";

const INPUT = "rounded-lg border border-border bg-surface px-3 py-2";

interface Draft {
  username: string;
  email: string;
  firstName: string;
  lastName: string;
  roles: Role[];
  mode: PasswordMode;
}

const EMPTY: Draft = { username: "", email: "", firstName: "", lastName: "", roles: ["MEMBER"], mode: "email" };

/** "Add user". The new user sets their own password at first login. */
export function CreateUserDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const t = useTranslations("adminUsers");
  const common = useTranslations("common");
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<UsersErrorKey | null>(null);
  const [stepUp, setStepUp] = useState<string | null>(null);
  const [issued, setIssued] = useState<{ password: string; username: string | null } | null>(null);
  const create = useCreateUser();
  const toast = useToast();

  function close() {
    setDraft(EMPTY);
    setError(null);
    setStepUp(null);
    setIssued(null); // the temporary password is gone for good
    onClose();
  }

  function toggleRole(role: Role) {
    setDraft((d) => ({ ...d, roles: d.roles.includes(role) ? d.roles.filter((r) => r !== role) : [...d.roles, role] }));
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError(null);
    try {
      const created = await create.mutateAsync({
        username: draft.username.trim(),
        email: draft.email.trim(),
        first_name: draft.firstName.trim(),
        last_name: draft.lastName.trim(),
        roles: draft.roles,
        password_mode: draft.mode,
      });
      onCreated(created.user.id);
      if (created.temporary_password) {
        setIssued({ password: created.temporary_password, username: created.user.username });
      } else {
        toast(t("create.done"), "ok");
        close();
      }
    } catch (e) {
      if (e instanceof StepUpRequired) setStepUp(e.stepUp);
      else setError(usersErrorKey(e));
    }
  }

  const elevated = draft.roles.some((r) => r === "EXPERT" || r === "ADMIN");

  return (
    <Dialog open={open} onClose={close} title={issued ? t("create.createdTitle") : t("create.title")}>
      {issued ? (
        <div className="flex flex-col gap-4">
          <OneTimePassword password={issued.password} username={issued.username} />
          <div className="flex justify-end">
            <Button onClick={close}>{t("create.finish")}</Button>
          </div>
        </div>
      ) : stepUp ? (
        <StepUpNotice
          stepUp={stepUp}
          reason={t("create.stepUp")}
        />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label={t("create.form")}>
          <label className="flex flex-col gap-1">
            {t("create.username")}
            <input
              className={INPUT}
              required
              minLength={3}
              maxLength={64}
              pattern="[A-Za-z0-9][A-Za-z0-9._\-]{2,63}"
              autoComplete="off"
              value={draft.username}
              onChange={(e) => setDraft({ ...draft, username: e.target.value })}
            />
            <span className="text-xs text-text-muted">{t("create.usernameHint")}</span>
          </label>
          <label className="flex flex-col gap-1">
            {t("create.email")}
            <input
              className={INPUT}
              type="email"
              autoComplete="off"
              required
              value={draft.email}
              onChange={(e) => setDraft({ ...draft, email: e.target.value })}
            />
          </label>
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="flex flex-col gap-1">
              {t("create.firstName")}
              <input
                className={INPUT}
                required
                maxLength={100}
                value={draft.firstName}
                onChange={(e) => setDraft({ ...draft, firstName: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-1">
              {t("create.lastName")}
              <input
                className={INPUT}
                required
                maxLength={100}
                value={draft.lastName}
                onChange={(e) => setDraft({ ...draft, lastName: e.target.value })}
              />
            </label>
          </div>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 font-medium">{t("create.roles")}</legend>
            {ROLES.map((r) => (
              <label key={r} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={draft.roles.includes(r)}
                  disabled={r === "MEMBER"}
                  onChange={() => toggleRole(r)}
                />
                <span>
                  {t(`roleNames.${r}`)} <span className="text-xs text-text-muted">· {t(`roleHints.${r}`)}</span>
                </span>
              </label>
            ))}
            {elevated && <p className="text-xs text-warn">{t("create.mfaFirst")}</p>}
          </fieldset>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 font-medium">{t("create.firstPassword")}</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="password-mode" checked={draft.mode === "email"} onChange={() => setDraft({ ...draft, mode: "email" })} />
              {t("create.byEmail")}
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="password-mode"
                checked={draft.mode === "temporary"}
                onChange={() => setDraft({ ...draft, mode: "temporary" })}
              />
              {t("create.temporary")}
            </label>
          </fieldset>
          {error && (
            <p role="alert" className="text-danger">
              {t(`errors.${error}`)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              {common("cancel")}
            </Button>
            <Button type="submit" busy={create.isPending}>
              {t("create.submit")}
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
