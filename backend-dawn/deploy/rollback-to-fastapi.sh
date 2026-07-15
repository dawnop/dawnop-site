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

CONF=/etc/nginx/sites-enabled/dawnop
STAMP=$(date +%s)

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
mkdir -p /etc/nginx/backups
cp "$CONF" "/etc/nginx/backups/dawnop.pre-rollback.$STAMP"
sed -i 's#proxy_pass http://127.0.0.1:8001;#proxy_pass http://127.0.0.1:8000;#' "$CONF"          # /api/
sed -i 's#proxy_pass http://127.0.0.1:8001/dav/;#proxy_pass http://127.0.0.1:8000/dav/;#' "$CONF" # dav vhost
sed -i 's/client_max_body_size 64m;/client_max_body_size 0;/' "$CONF"                            # dav: FastAPI streams, no cap

echo "==> 3/3 validating and reloading nginx"
nginx -t
systemctl reload nginx

echo
echo "Rolled back to FastAPI :8000.  Dawn :8001 still running (untouched)."
echo "  nginx backup: /etc/nginx/backups/dawnop.pre-rollback.$STAMP"
echo "  to return to Dawn: flip the three sed patterns back (:8000 -> :8001, 0 -> 64m), nginx -t, reload."
