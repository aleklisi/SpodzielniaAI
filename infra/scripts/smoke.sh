#!/usr/bin/env bash
# Stack smoke test (chunks 1.2b, 1.3; Docker gate). Run against a running stack: `make smoke`.
#   COMPOSE      compose command (default: dev file set)
#   SITE_DOMAIN  public hostname (default: spoldzielnia.localhost)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
read -r -a COMPOSE <<< "${COMPOSE:-docker compose -f compose.yaml -f compose.dev.yaml}"
DOMAIN="${SITE_DOMAIN:-spoldzielnia.localhost}"
PROJECT="$("${COMPOSE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["name"])')"

pass=0 fail=0
ok()   { echo "  ✔ $*"; pass=$((pass + 1)); }
bad()  { echo "  ✘ $*"; fail=$((fail + 1)); }
check() { local desc="$1"; shift; if "$@" >/dev/null 2>&1; then ok "$desc"; else bad "$desc"; fi; }
check_not() { local desc="$1"; shift; if "$@" >/dev/null 2>&1; then bad "$desc"; else ok "$desc"; fi; }

# Caddy's local root CA (dev). In prod, public certificates are used and CA_ARGS stays empty.
CA_ARGS=()
if [[ "${CADDY_TLS:-internal}" == "internal" ]]; then
  CA_FILE="$(mktemp)"
  trap 'rm -f "$CA_FILE"' EXIT
  "${COMPOSE[@]}" cp proxy:/data/caddy/pki/authorities/local/root.crt "$CA_FILE" >/dev/null
  CA_ARGS=(--cacert "$CA_FILE")
fi
status() { curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${CA_ARGS[@]}" "$1"; }
expect() { local desc="$1" url="$2" code="$3" got; got="$(status "$url" || true)";
  if [[ "$got" == "$code" ]]; then ok "$desc ($got)"; else bad "$desc (expected $code, got $got)"; fi; }

echo "1. HTTPS single origin"
expect "GET /api/healthz" "https://$DOMAIN/api/healthz" 200
expect "GET /api/readyz"  "https://$DOMAIN/api/readyz" 200
expect "GET / (placeholder)" "https://$DOMAIN/" 200
# Alembic head of this checkout: the revision no other migration names as its down_revision.
# Plain python3 (no venv), so the script also runs on servers.
HEAD="$(python3 - <<'PY'
import pathlib, re
revs, downs = set(), set()
for f in pathlib.Path("src/spoldzielnia_ai/db/migrations/versions").glob("*.py"):
    src = f.read_text()
    revs.add(re.search(r'^revision: str = "([^"]+)"', src, re.M).group(1))
    down = re.search(r'^down_revision: str \| None = "([^"]+)"', src, re.M)
    if down:
        downs.add(down.group(1))
(head,) = revs - downs
print(head)
PY
)"
readyz="$(curl -sS --max-time 10 "${CA_ARGS[@]}" "https://$DOMAIN/api/readyz" || true)"
check "readyz: schema at Alembic head ($HEAD)" python3 -c '
import json, sys
body = json.loads(sys.argv[1])
assert body["checks"]["migrations"] is True and body["schema_revision"] == sys.argv[2]
' "$readyz" "$HEAD"
check "HSTS header on /api" bash -c "curl -sSI ${CA_ARGS[*]} https://$DOMAIN/api/healthz | grep -qi '^strict-transport-security:'"
expect "HTTP redirects to HTTPS" "http://$DOMAIN/" 308

echo "2. Identity provider"
expect "OIDC discovery" "https://auth.$DOMAIN/realms/spoldzielnia/.well-known/openid-configuration" 200
check "issuer matches public URL" bash -c \
  "curl -sS ${CA_ARGS[*]} https://auth.$DOMAIN/realms/spoldzielnia/.well-known/openid-configuration \
   | grep -q '\"issuer\":\"https://auth.$DOMAIN/realms/spoldzielnia\"'"
# The api fetches discovery itself (through the proxy alias, trusting the dev CA) to build this.
login="$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 15 "${CA_ARGS[@]}" \
  "https://$DOMAIN/api/auth/login" || true)"
check "login: 302 to IdP authorize with PKCE S256" bash -c \
  "[[ '$login' == '302 https://auth.$DOMAIN/realms/spoldzielnia/protocol/openid-connect/auth?'* \
     && '$login' == *code_challenge_method=S256* ]]"

expect "me: 401 without a session" "https://$DOMAIN/api/me" 401
xorigin="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "${CA_ARGS[@]}" -X POST \
  -H 'Origin: https://evil.example' "https://$DOMAIN/api/auth/logout" || true)"
check "CSRF: cross-origin POST refused (403)" test "$xorigin" = 403

echo "3. API container hardening"
uid="$("${COMPOSE[@]}" exec -T api id -u | tr -d '\r')"
if [[ "$uid" != "0" ]]; then ok "api runs as uid $uid"; else bad "api runs as root"; fi
# The image's libmagic (Debian) must classify like the tested host build.
MAGIC_PY='
import pathlib, tempfile
from spoldzielnia_ai.files.validation import FileRejected, validate_file
d = pathlib.Path(tempfile.mkdtemp())
exe = d / "a"; exe.write_bytes(b"MZ" + b"\x00" * 58 + b"\x40\x00\x00\x00" + b"PE\x00\x00" + b"\x00" * 300)
try:
    validate_file(exe, "invoice.pdf", "application/pdf"); raise SystemExit(1)
except FileRejected as e:
    assert e.code == "executable", e.code
pdf = d / "b"; pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")
assert validate_file(pdf, "raport.pdf", "application/pdf").mime == "application/pdf"
'
check "api: libmagic rejects exe-as-pdf, accepts pdf" "${COMPOSE[@]}" exec -T api python -c "$MAGIC_PY"
check_not "api rootfs is read-only" "${COMPOSE[@]}" exec -T api sh -c 'touch /app/.probe'

echo "4. Published ports"
published="$("${COMPOSE[@]}" ps --format json | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
rows = json.loads(raw) if raw.startswith("[") else [json.loads(l) for l in raw.splitlines() if l]
dev_debug = {"postgres", "redis", "qdrant", "minio", "clamav", "mailpit"}
offenders = sorted({r["Service"] for r in rows for p in (r.get("Publishers") or [])
                    if p.get("PublishedPort") and r["Service"] != "proxy"
                    and not (r["Service"] in dev_debug and p.get("URL") == "127.0.0.1")})
print(" ".join(offenders))')"
if [[ -z "$published" ]]; then ok "only proxy (+ loopback dev debug ports) publish ports"
else bad "unexpected published ports: $published"; fi

echo "5. Network isolation"
NOINET='import socket; socket.create_connection(("1.1.1.1", 443), 3)'
check_not "api has no internet access" "${COMPOSE[@]}" exec -T api python -c "$NOINET"
API_IMAGE="$("${COMPOSE[@]}" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["api"]["image"])')"
check_not "backend network has no internet access" \
  docker run --rm --network "${PROJECT}_backend" --entrypoint python "$API_IMAGE" -c "$NOINET"
check "api reaches IdP via proxy alias" "${COMPOSE[@]}" exec -T api python -c \
  "import socket; socket.create_connection(('auth.$DOMAIN', 443), 3)"
OUT='import socket; socket.create_connection(("1.1.1.1", 443), 5)'
check_not "worker-media has no internet access" "${COMPOSE[@]}" exec -T worker-media python -c "$OUT"
check "worker-egress reaches the internet" "${COMPOSE[@]}" exec -T worker-egress python -c "$OUT"

echo "6. Media pipeline"
check "worker-media: model volume matches its manifest" \
  "${COMPOSE[@]}" exec -T worker-media spoldzielnia models verify --root /models
PII_PY='
from spoldzielnia_ai.privacy.presidio_adapter import PresidioAnalyzer
from spoldzielnia_ai.privacy.scrubber import PiiScrubber
out = PiiScrubber(PresidioAnalyzer.shared()).scrub("Jan Kowalski, PESEL 44051401359, NIP 526-018-15-96").text
assert out == "<OSOBA_1>, PESEL <PESEL>, NIP <NIP>", out
'
check "worker-core: Polish PII scrubbing works offline" \
  "${COMPOSE[@]}" exec -T worker-core python -c "$PII_PY"
check_not "worker-core has no internet access" "${COMPOSE[@]}" exec -T worker-core python -c "$OUT"
check "worker-media: model volume is read-only" bash -c \
  "! ${COMPOSE[*]} exec -T worker-media sh -c 'touch /models/.probe' 2>/dev/null"


echo
echo "smoke: $pass passed, $fail failed"
[[ "$fail" -eq 0 ]]
