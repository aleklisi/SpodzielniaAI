# Spółdzielnia AI — Server operations and disaster recovery

The runbook for one Linux server (chunk 6.8): installing it from scratch, upgrading, rolling
back, the nightly encrypted backups, and the restore onto a new server after a loss. The design
behind it is in [DEPLOYMENT_DOCKER.md](../implementation/DEPLOYMENT_DOCKER.md) §9.

Every command runs on the server from the checkout, as a user in the `docker` group, unless
it says otherwise.

| What | Where |
|---|---|
| Checkout of the release tag | `/opt/spoldzielnia` (compose files, Caddyfile, init scripts, realm) |
| Settings (no secrets) | `/opt/spoldzielnia/.env`, from `.env.prod.example` |
| Secrets (Docker secrets) | `/opt/spoldzielnia/secrets/`. The `kek` file decrypts every notebook |
| Deployed version | `/opt/spoldzielnia/.deploy/current`, `previous`, `history` |
| Backup bundles | `BACKUP_DIR` (default `/var/backups/spoldzielnia`), plus an off-site copy |
| age public keys (recipients) | `BACKUP_RECIPIENTS`, `BACKUP_SECRETS_RECIPIENTS` |
| age private keys (identities) | **Never on the server.** Keep them offline (password manager or a hardware key), with two people |

## 1. What a backup holds and what it does not

| Data | In the backup | After a restore |
|---|---|---|
| PostgreSQL `spoldzielnia`: notes (encrypted), wiki, governance, consents, audit chain, model configuration | `pg_dump -Fc`, data bundle | `pg_restore`; row counts checked against the manifest |
| PostgreSQL `keycloak`: accounts, passwords, TOTP, roles, sessions | `pg_dump -Fc`, data bundle | restored with it |
| MinIO bucket `spoldzielnia-private`: attachments, photos, previews (envelope-encrypted with the KEK) | `mc mirror`, data bundle | mirrored back; object count checked |
| `./secrets` (KEK, database and service passwords, shield keys, internal CA) and `.env` | **secrets bundle**, encrypted to its own recipients | installed where missing |
| Qdrant: search and atlas indexes | no. They are derived data | rebuilt from Postgres (`reindex`) |
| Redis: sessions, Celery queues, shield coverage windows, proof-of-work challenges | no. Short-lived | members log in again. Coverage windows start empty, so the 24 h/7 d/30 d budgets reset once. Locked accounts stay locked (the lock is in Postgres) |
| Offline models (`models` volume) | no. Re-downloadable | `deploy.sh` downloads them (`make models`) |
| Caddy certificates | no | Let's Encrypt issues new ones on start |

Without the `kek` from the secrets bundle, the restored notebooks and attachments can't be read.
Keep the secrets bundle's key separate from the data bundle's key, and both away from the server.

## 2. New server (first installation)

**Prerequisites:**
- Linux with Docker Engine ≥ 24 and the compose plugin v2.
- `git`, `make`, `python3`, `age`, `jq`, `openssl`, `rsync`. On Debian/Ubuntu: `apt install git make python3 age jq openssl rsync`.
- DNS `A`/`AAAA` records for `<domain>` and `auth.<domain>`.
- Ports 80 and 443 open (Let's Encrypt HTTP-01).
- Optional: the NVIDIA driver and the NVIDIA Container Toolkit for a GPU.
- Small VPS caveats (LXC, shared IPv4): DEPLOYMENT_DOCKER §6.1.

```bash
sudo mkdir -p /opt/spoldzielnia && sudo chown "$USER" /opt/spoldzielnia
git clone <repository> /opt/spoldzielnia && cd /opt/spoldzielnia
git checkout v1.2.3                      # the release you deploy
cp .env.prod.example .env && $EDITOR .env  # SITE_DOMAIN, ACME_EMAIL, REGISTRY, BACKUP_*
make secrets                             # ./secrets: random passwords, KEK, shield keys, internal CA
docker login ghcr.io                     # if the images are private
make deploy VERSION=v1.2.3               # pull, models, migrate, up --wait, smoke
sudo infra/scripts/egress-firewall.sh    # after the first up: the egress network can't reach private ranges
make keycloak-setup                      # user-management service account (idempotent)
make create-admin EMAIL=admin@example.org NAME="Imię Nazwisko"
```

The firewall rules don't survive a reboot or a Docker restart: run the script again, or from a systemd unit ordered after `docker.service`. `create-admin` prints a one-time password. At the first login Keycloak asks for a new password
and an authenticator app (ADMIN requires MFA). After that, the admin creates every other account
in `/admin/users`. Next, set up the backups (§4), and configure the models in `/admin/llm` (none
is required; a feature without a model stays off).

On a server with no access to the registry, use `docker save` on a machine that has the images,
then `docker load` on the server, and deploy with `OFFLINE=1 make deploy VERSION=…`.

## 3. Upgrade and rollback

```bash
cd /opt/spoldzielnia && git fetch --tags && git checkout v1.3.0
make deploy VERSION=v1.3.0
```

`deploy.sh` checks `.env`. It refuses `MFA_REQUIRED=false`, a `localhost` domain and a missing
ACME e-mail. Then it pulls the images and fills the model volume if needed. It **backs up the
running installation** before migrating (`BACKUP=0` skips that). Then it migrates, restarts
with `--wait`, runs the smoke test against the public domain, and records the version.
Migrations follow expand/contract, so the previous version keeps working on the new schema
while the new one starts.

**Rollback:** `git checkout <previous tag> && make rollback`. This is allowed only when both
versions ship the same Alembic head, because the API reports ready only at its own head.
`deploy.sh` compares the heads from the two images and refuses otherwise. If the failed release
migrated the schema, you have two options:
- **Roll forward:** a fixed release, the usual path.
- **Restore the pre-deploy backup** (§5 with `FORCE=1`). Anything written since the deploy is lost.

## 4. Nightly backups

One-time setup:
1. Off the server, create two key pairs:
   ```bash
   age-keygen -o data-identity.txt
   age-keygen -o secrets-identity.txt
   ```
   Store both identity files offline. Only the public lines (`age1…`) go to the server.
2. On the server:
   ```bash
   sudo install -d -m 700 /etc/spoldzielnia /var/backups/spoldzielnia
   echo age1…data… | sudo tee /etc/spoldzielnia/backup-recipients.txt
   echo age1…secrets… | sudo tee /etc/spoldzielnia/secrets-recipients.txt
   ```
   Set `BACKUP_RECIPIENTS`, `BACKUP_SECRETS_RECIPIENTS` and `BACKUP_DIR` in `.env`.
3. Run `make backup` once by hand and check that two `.age` files and a `.sha256` file appear.
4. Install the timer:
   ```bash
   sudo cp infra/systemd/spoldzielnia-backup.{service,timer} /etc/systemd/system/
   sudo systemctl daemon-reload && sudo systemctl enable --now spoldzielnia-backup.timer
   ```
   It runs at 03:15 (±15 min). `systemctl list-timers` shows the next run, and `journalctl -u spoldzielnia-backup` shows the output.
5. Off-site copy: uncomment `ExecStartPost` in the service file (rclone to an S3 bucket or a
   NAS), or pull `BACKUP_DIR` from another machine. The bundles are encrypted, so the target
   only needs to be durable. `BACKUP_KEEP` (default 14) prunes the local copies only.

**Every month, and after every change to these scripts:** do a restore rehearsal. On a dev
machine, `make dr-rehearsal` restores the dev stack into a bare copy and checks it. For a real
bundle, restore it onto a scratch VM with §5.

## 5. Restore onto a new server (disaster)

You need:
- the newest data bundle and secrets bundle (from the off-site copy);
- both age identities;
- a server prepared as in §2 **up to `make secrets`, which you skip**: the secrets come from the bundle.

The bundle name tells you the version: `spoldzielnia-<UTC>-<version>.data.tar.age`.

```bash
cd /opt/spoldzielnia && git checkout <version from the bundle name>
# Both identities in one file, on a tmpfs, removed afterwards:
cat data-identity.txt secrets-identity.txt > /dev/shm/restore-identity.txt
docker login ghcr.io                     # if the images are private
make restore DATA=/path/spoldzielnia-….data.tar.age SECRETS=/path/spoldzielnia-….secrets.tar.age \
  BACKUP_IDENTITY=/dev/shm/restore-identity.txt
shred -u /dev/shm/restore-identity.txt
$EDITOR .env                             # the restored .env: SITE_DOMAIN if the domain changed, BACKUP_*
make deploy VERSION=<the same version> BACKUP=0   # smoke test, records the version, models
sudo infra/scripts/egress-firewall.sh
```

What `restore.sh` does, and stops on:
1. **Decrypts** both bundles. It installs `./secrets` and `.env` where they are missing. If a
   file exists and differs from the bundle, it stops (`FORCE_SECRETS=1` replaces it and keeps
   the old copies in `secrets.replaced-<UTC>/`).
2. **Checks** every file against the manifest's SHA-256.
3. **Starts only Postgres and MinIO.** On empty volumes, the init script creates the roles and
   databases from the restored secrets. If the database already holds tables, it stops
   (`FORCE=1` replaces them; use it only to roll back to a backup on the same server).
4. **Restores** both databases with `pg_restore --create --clean`, so owners and grants come
   back as saved. Then it compares the row counts with the manifest. A difference is reported,
   because the backup ran on a live system; `STRICT=1` makes it fatal.
5. **Migrates** to the image's head and runs `bootstrap` (the bucket, with server-side encryption).
6. **Mirrors the objects** back and checks their count.
7. **Starts the whole stack**, then rebuilds the search and atlas indexes from Postgres
   (`reindex`). If no embedding model is configured yet, it skips this; the index is built once
   an admin routes one.
8. **Verifies the audit hash chain** (`audit-verify`) and that the backup's last audit entry is
   unchanged.

**Restore on the same server after data loss** (volumes gone, `./secrets` intact): leave out
`SECRETS=`. If the volumes still exist and you are going back to a backup on purpose, run
`docker compose -f compose.yaml -f compose.prod.yaml down` first, then `FORCE=1 make restore …`.

## 6. Checks after a restore

- `make smoke` (part of `deploy.sh`) must report 0 failed. It covers HTTPS, the IdP, headers,
  network isolation, the models and the index generation (which also proves that the KEK
  decrypts the stored model credentials).
- Log in as an admin. `/admin/security` must show the audit chain as intact, and
  `/admin/llm` → index must show a SERVING generation with the expected item count.
- A member opens a notebook note with an attachment. This proves the KEK decrypts the notebooks.
- Expect each member to log in again (Redis was not restored).
- Write the restore into the operations log: which bundle, the time, and who did it.

## 7. Rehearsal (tested procedure)

`make dr-rehearsal` (CI job `dr-rehearsal`) runs §5 end to end against a running stack:
1. backs it up with a throwaway age key;
2. makes a bare copy of the checkout (no `./secrets`, `.env` or volumes);
3. runs `restore.sh` there with `STRICT=1`, in its own compose project;
4. runs the smoke test against the copy;
5. removes the copy and starts the source stack again.

The source stack's volumes are never touched.

The last run on the dev stack (2026-10-23) restored 102 objects and every table's row count.
It rebuilt 52 index items (0 failed), verified the audit chain through entry #2740, and the
smoke test passed 44/44.
