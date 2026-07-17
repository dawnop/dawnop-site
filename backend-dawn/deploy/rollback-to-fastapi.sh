#!/usr/bin/env bash
# Emergency rollback: revert dawnop.com from the Dawn backend (:8001) back to
# the original FastAPI/uvicorn backend (:8000).
#
# Context: after the M6 strangler-fig migration completed, every /api endpoint
# and the dav.dawnop.com WebDAV are served by the Dawn backend on :8001, and the
# FastAPI service (dawnop-backend, uvicorn :8000) was retired
# (`systemctl disable --now dawnop-backend`). The FastAPI code, venv and DB
# access all remain in place at /opt/dawnop/backend, so a full rollback is:
#   1. bring uvicorn back up, and
#   2. point nginx /api + dav back at :8000.
# This script does both. The Dawn service (:8001) is left running and untouched,
# so re-applying Dawn is just the inverse (flip :8000 -> :8001, reload).
#
# Run as root on the production server:
#   sudo bash /opt/dawnop-dawn/rollback-to-fastapi.sh
set -euo pipefail

# Resolve the file nginx actually reads instead of assuming a layout — every
# hardcoded answer here has been wrong at least once:
#
#   - sites-enabled/dawnop, edited directly: correct today (it is a plain file),
#     but wrong the moment anyone restores the documented symlink layout, because
#     GNU `sed -i` does not follow symlinks — it writes a temp file and renames
#     over the path, replacing the link with a regular file and leaving the real
#     config untouched.
#   - sites-available/dawnop: what this script said until 2026-07-17, on the
#     strength of the deploy docs claiming a symlink. Production has no symlink:
#     sites-enabled/dawnop is a plain file and sites-available/dawnop is a stale
#     copy nobody reads (nginx only includes sites-enabled/*). So the "fix" aimed
#     the rollback at a file with no effect — a rollback that prints success and
#     changes nothing, which is exactly the failure it was meant to prevent.
#
# `readlink -f` answers the question for both layouts: a plain file resolves to
# itself, a symlink resolves to its target. Editing the resolved path is always
# the real config and can never eat a symlink.
ENABLED=/etc/nginx/sites-enabled/dawnop
[ -e "$ENABLED" ] || { echo "!!! $ENABLED does not exist — is this the right host?" >&2; exit 1; }
CONF=$(readlink -f "$ENABLED")
STAMP=$(date +%s)
BACKUP="/etc/nginx/backups/dawnop.pre-rollback.$STAMP"

echo "==> 1/3 bringing FastAPI (uvicorn :8000) back up"
systemctl enable --now dawnop-backend
for _ in $(seq 1 40); do
  if curl -sf -o /dev/null http://127.0.0.1:8000/api/health; then
    echo "    uvicorn :8000 healthy"
    break
  fi
  sleep 0.5
done
if ! curl -sf -o /dev/null http://127.0.0.1:8000/api/health; then
  echo "!!! uvicorn did not come healthy on :8000 — aborting before touching nginx" >&2
  exit 1
fi

echo "==> 2/3 pointing nginx /api and dav.dawnop.com back at :8000"
[ -f "$CONF" ] || { echo "!!! $CONF is not a regular file" >&2; exit 1; }
echo "    nginx reads: $ENABLED -> $CONF"
mkdir -p /etc/nginx/backups
cp -a "$CONF" "$BACKUP"

# Each substitution is checked rather than assumed. A sed that matches nothing
# exits 0, so an unchecked one turns a rollback into a no-op that still prints
# success — which is how the old `client_max_body_size 64m` line survived here
# long after nginx.conf had moved to `0`.
sub() {
  local pat=$1 desc=$2
  grep -qF -- "$pat" "$CONF" || {
    echo "!!! $CONF has no '$pat' ($desc) — config drifted; not touching nginx" >&2
    echo "    inspect it by hand; backup at $BACKUP" >&2
    exit 1
  }
}
sub 'proxy_pass http://127.0.0.1:8001;' '/api/ upstream'
sub 'proxy_pass http://127.0.0.1:8001/dav/;' 'dav vhost upstream'
sed -i 's#proxy_pass http://127.0.0.1:8001;#proxy_pass http://127.0.0.1:8000;#' "$CONF"
sed -i 's#proxy_pass http://127.0.0.1:8001/dav/;#proxy_pass http://127.0.0.1:8000/dav/;#' "$CONF"
# Nothing to do for client_max_body_size: the dav vhost is already `0` (unlimited)
# and both backends stream PUT bodies to disk, so the cap is backend-independent.

echo "==> 3/3 validating and reloading nginx"
# A failed `nginx -t` under `set -e` would otherwise exit leaving a broken config
# on disk: nginx keeps serving the old one, so nothing looks wrong until some
# unrelated reload or a reboot fails — and then the site is down for a reason
# that has nothing to do with whoever triggered it.
if ! nginx -t; then
  echo "!!! nginx -t failed — restoring $BACKUP and leaving nginx as it was" >&2
  cp -a "$BACKUP" "$CONF"
  exit 1
fi
systemctl reload nginx

echo
echo "Rolled back to FastAPI :8000.  Dawn :8001 still running (untouched)."
echo "  nginx backup: $BACKUP"
echo "  to return to Dawn: flip both proxy_pass lines in $CONF back to :8001"
echo "  (/api/ and the dav vhost), then: nginx -t && systemctl reload nginx"
