#!/usr/bin/env bash
# Security scanning (6.7, gap G11): `make audit`. Fails on any finding not documented in
# docs/SECURITY_REVIEW.md § Accepted risks.
#   pip-audit   locked Python dependencies (all groups)
#   bandit      Python source, medium severity and up (ruff's S rules run on every commit)
#   npm audit   frontend runtime dependencies, high and up (dev-only findings are reported)
#   gitleaks    secrets in the git history (the gitignored ./secrets is out of scope by design)
#   trivy       built images (make build first), HIGH/CRITICAL with a fix available
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
REGISTRY="${REGISTRY:-spoldzielnia}" VERSION="${VERSION:-dev}"
GITLEAKS_IMAGE="zricethezav/gitleaks:v8.28.0@sha256:cdbb7c955abce02001a9f6c9f602fb195b7fadc1e812065883f695d1eeaba854"
TRIVY_IMAGE="aquasec/trivy:0.67.2@sha256:e2b22eac59c02003d8749f5b8d9bd073b62e30fefaef5b7c8371204e0a4b0c08"
IMAGES=(api worker-core worker-media frontend)
failed=()
step() { echo; echo "== $1"; }

step "pip-audit (locked dependencies)"
REQS="$(mktemp)"; trap 'rm -f "$REQS"' EXIT
uv export --format requirements-txt --no-hashes --no-emit-project --frozen > "$REQS"
# pl-core-news-lg is a spaCy model wheel from GitHub releases: not on PyPI, so not auditable.
uvx pip-audit -r "$REQS" --progress-spinner off || failed+=(pip-audit)

step "bandit (src, medium+)"
uvx bandit -r src -ll -q || failed+=(bandit)

step "npm audit (frontend runtime, high+)"
(cd frontend && npm audit --omit=dev --audit-level=high) || failed+=(npm-audit)
echo "-- dev dependencies (informational):"
(cd frontend && npm audit --audit-level=high | tail -3) || true

step "gitleaks (git history)"
docker run --rm -v "$ROOT:/repo:ro" "$GITLEAKS_IMAGE" git /repo --redact --no-banner || failed+=(gitleaks)

step "trivy (images: ${IMAGES[*]})"
for image in "${IMAGES[@]}"; do
  docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v trivy-cache:/root/.cache/ \
    -v "$ROOT/.trivyignore:/.trivyignore:ro" "$TRIVY_IMAGE" image --quiet --exit-code 1 \
    --ignorefile /.trivyignore --severity HIGH,CRITICAL --ignore-unfixed --scanners vuln \
    "$REGISTRY/$image:$VERSION" || failed+=("trivy:$image")
done

echo
if ((${#failed[@]})); then
  echo "audit: FAILED (${failed[*]})"
  exit 1
fi
echo "audit: clean"
