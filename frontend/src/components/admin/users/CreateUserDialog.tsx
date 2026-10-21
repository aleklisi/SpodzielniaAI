"use client";

import { useState, type FormEvent } from "react";

import { OneTimePassword } from "@/components/admin/users/OneTimePassword";
import { StepUpNotice } from "@/components/auth/RequireRole";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { useToast } from "@/components/ui/Toast";
import { StepUpRequired } from "@/lib/api";
import { ROLES, useCreateUser, usersError, type PasswordMode, type Role } from "@/lib/users-admin";

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

/** "Dodaj użytkownika". The new user sets their own password at first login. */
export function CreateUserDialog({ open, onClose, onCreated }: { open: boolean; onClose: () => void; onCreated: (id: string) => void }) {
  const [draft, setDraft] = useState<Draft>(EMPTY);
  const [error, setError] = useState<string | null>(null);
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
        toast("Konto utworzone. Wysłaliśmy link do ustawienia hasła.", "ok");
        close();
      }
    } catch (e) {
      if (e instanceof StepUpRequired) setStepUp(e.stepUp);
      else setError(usersError(e));
    }
  }

  const elevated = draft.roles.some((r) => r === "EXPERT" || r === "ADMIN");

  return (
    <Dialog open={open} onClose={close} title={issued ? "Konto utworzone" : "Dodaj użytkownika"}>
      {issued ? (
        <div className="flex flex-col gap-4">
          <OneTimePassword password={issued.password} username={issued.username} />
          <div className="flex justify-end">
            <Button onClick={close}>Gotowe</Button>
          </div>
        </div>
      ) : stepUp ? (
        <StepUpNotice
          stepUp={stepUp}
          reason="Utworzenie konta eksperta lub administratora wymaga świeżego logowania z drugim składnikiem."
        />
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-3 text-sm" aria-label="Nowe konto">
          <label className="flex flex-col gap-1">
            Nazwa użytkownika
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
            <span className="text-xs text-text-muted">Małe litery, cyfry, kropka, myślnik, podkreślenie.</span>
          </label>
          <label className="flex flex-col gap-1">
            E-mail
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
              Imię
              <input
                className={INPUT}
                required
                maxLength={100}
                value={draft.firstName}
                onChange={(e) => setDraft({ ...draft, firstName: e.target.value })}
              />
            </label>
            <label className="flex flex-col gap-1">
              Nazwisko
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
            <legend className="mb-1 font-medium">Role</legend>
            {ROLES.map((r) => (
              <label key={r.role} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={draft.roles.includes(r.role)}
                  disabled={r.role === "MEMBER"}
                  onChange={() => toggleRole(r.role)}
                />
                <span>
                  {r.label} <span className="text-xs text-text-muted">· {r.hint}</span>
                </span>
              </label>
            ))}
            {elevated && (
              <p className="text-xs text-warn">Przy pierwszym logowaniu użytkownik skonfiguruje aplikację do kodów (MFA).</p>
            )}
          </fieldset>
          <fieldset className="flex flex-col gap-1">
            <legend className="mb-1 font-medium">Pierwsze hasło</legend>
            <label className="flex items-center gap-2">
              <input type="radio" name="password-mode" checked={draft.mode === "email"} onChange={() => setDraft({ ...draft, mode: "email" })} />
              Link e-mailem: użytkownik sam ustawi hasło
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="password-mode"
                checked={draft.mode === "temporary"}
                onChange={() => setDraft({ ...draft, mode: "temporary" })}
              />
              Hasło tymczasowe: pokażemy je raz, przekażesz je osobiście
            </label>
          </fieldset>
          {error && (
            <p role="alert" className="text-danger">
              {error}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={close}>
              Anuluj
            </Button>
            <Button type="submit" busy={create.isPending}>
              Utwórz konto
            </Button>
          </div>
        </form>
      )}
    </Dialog>
  );
}
