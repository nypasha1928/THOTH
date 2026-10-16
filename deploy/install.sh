#!/usr/bin/env bash
# Run only on a newly provisioned Ubuntu/Debian VPS you control.
# No host provisioning, payments, DNS or exchange connection is performed here.
set -euo pipefail
umask 077
if [[ "${1:-}" == "--check" ]]; then
  command -v docker >/dev/null
  docker compose version >/dev/null
  echo 'Docker/Compose prerequisites present. Deployment was not started.'
  exit 0
fi
[[ ${EUID} -eq 0 ]] || { echo 'Run as root on your VPS.' >&2; exit 1; }
: "${THOTH_DOMAIN:?Set THOTH_DOMAIN to your domain hostname}"
[[ ${THOTH_DOMAIN} =~ ^([a-zA-Z0-9][a-zA-Z0-9-]*\.)+[a-zA-Z]{2,}$ ]] || { echo 'Invalid domain hostname.' >&2; exit 1; }
command -v docker >/dev/null || { echo 'Install Docker Engine and Compose using the official instructions first.' >&2; exit 1; }
docker compose version >/dev/null
THOTH_PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "${THOTH_PROJECT_DIR}/compose.yaml" ]] || { echo 'Run from the unpacked THOTH deployment bundle.' >&2; exit 1; }
THOTH_PRIVATE_DIR=/etc/thoth
install -d -m 0700 "${THOTH_PRIVATE_DIR}"
if [[ ! -f "${THOTH_PRIVATE_DIR}/dashboard-password" ]]; then
  # Generate directly into the private file: never print the value or put it in
  # environment configuration, shell history, build arguments, or logs.
  python3 - "${THOTH_PRIVATE_DIR}/dashboard-password" <<'PY'
import os,secrets,sys
fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o400)
with os.fdopen(fd,'w') as stream:stream.write(secrets.token_urlsafe(32)+'\n')
os.chown(sys.argv[1],10001,10001)
PY
fi
export THOTH_PUBLIC_HOST="${THOTH_DOMAIN}"
export THOTH_PASSWORD_PATH="${THOTH_PRIVATE_DIR}/dashboard-password"
cd -- "${THOTH_PROJECT_DIR}"
docker compose config --quiet
docker compose up --build -d --wait --wait-timeout 90
printf '%s\n' 'THOTH deployment started. Verify DNS, HTTPS and login before using it.'
printf '%s\n' 'The dashboard password is stored privately at /etc/thoth/dashboard-password.'
printf '%s\n' 'No exchange credentials or testnet execution were enabled.'
