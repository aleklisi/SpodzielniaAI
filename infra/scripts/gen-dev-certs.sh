#!/usr/bin/env bash
# Internal CA + Qdrant server certificate for TLS on the backend network.
# Output: secrets/tls/{ca.pem,ca.key,qdrant.pem,qdrant.key}. Idempotent.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DIR="${SECRETS_DIR:-$ROOT/secrets}/tls"
mkdir -p "$DIR"
chmod 700 "$DIR"
cd "$DIR"

# P-256 key as PKCS#8 with a *named* curve. `req -newkey ec` under LibreSSL (macOS) writes explicit
# curve parameters, which rustls (Qdrant) rejects.
genkey() {
  openssl ecparam -name prime256v1 -genkey -noout | openssl pkcs8 -topk8 -nocrypt -out "$1"
}

if [[ ! -s ca.pem ]]; then
  genkey ca.key
  openssl req -new -x509 -sha256 -key ca.key -days 3650 \
    -subj "/CN=Spoldzielnia Internal CA" -out ca.pem \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    2>/dev/null
  echo "  created  tls/ca.pem"
fi

if [[ ! -s qdrant.pem ]]; then
  genkey qdrant.key
  openssl req -new -key qdrant.key -subj "/CN=qdrant" -out qdrant.csr 2>/dev/null
  openssl x509 -req -sha256 -in qdrant.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 825 \
    -out qdrant.pem -extfile <(printf '%s\n' \
      "subjectAltName=DNS:qdrant,DNS:localhost,IP:127.0.0.1" \
      "extendedKeyUsage=serverAuth" "keyUsage=critical,digitalSignature") 2>/dev/null
  rm -f qdrant.csr ca.srl
  echo "  created  tls/qdrant.pem"
fi

chmod 600 ca.key
chmod 644 ca.pem qdrant.pem qdrant.key
