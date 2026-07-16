#!/usr/bin/env bash
# Deploy the backend from the artifact CI built and tested.
#
# The jar is not in git (it is build output; a committed one drifts from src/
# silently). CI builds jar + lib/ on every push to main and uploads them as
# `backend-dawn-<sha>`. This script pulls that exact artifact, so what runs in
# production is what CI tested, from a known commit.
#
# Run from the laptop — no need to install it on the server:
#   ssh <user>@<server> 'bash -s' < backend-dawn/deploy/deploy.sh          # latest green main
#   ssh <user>@<server> 'bash -s' < backend-dawn/deploy/deploy.sh <sha>    # a specific commit
#
# Needs a GitHub token at /opt/dawnop-dawn/.github-token (chmod 600) — artifact
# downloads require auth even for a public repo. A fine-grained PAT scoped to
# this repo with Actions: read-only is enough; nothing here writes to GitHub.
#
# Safe by construction: the running jar is kept and restored if the new one does
# not come up healthy, so a bad deploy self-reverts instead of leaving the site
# down.
set -euo pipefail

REPO="dawnop/dawnop-site"
APP="/opt/dawnop-dawn"
SERVICE="dawnop-dawn"
TOKEN_FILE="$APP/.github-token"
HEALTH="http://127.0.0.1:8001/api/health"
WANT_SHA="${1:-}"

STAMP=$(date +%Y%m%d-%H%M%S)
PREV="$APP/.prev"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ ! -r "$TOKEN_FILE" ]; then
  echo "!!! no token at $TOKEN_FILE" >&2
  echo "    create a fine-grained PAT (repo $REPO, Actions: read-only), then:" >&2
  echo "      install -m 600 /dev/stdin $TOKEN_FILE   # paste token, Ctrl-D" >&2
  exit 1
fi
TOKEN=$(tr -d ' \t\n\r' < "$TOKEN_FILE")

api() { curl -fsS -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" "$@"; }

echo "==> 1/6 resolving the run to deploy"
# Deploying red code is the thing this script exists to prevent, so the run must
# be a *successful* one; ?status=success makes GitHub filter rather than us.
if [ -n "$WANT_SHA" ]; then
  RUNS_URL="https://api.github.com/repos/$REPO/actions/runs?head_sha=$WANT_SHA&status=success&per_page=1"
else
  RUNS_URL="https://api.github.com/repos/$REPO/actions/runs?branch=main&status=success&per_page=1"
fi
RUN_JSON=$(api "$RUNS_URL")
read -r RUN_ID RUN_SHA < <(printf '%s' "$RUN_JSON" | python3 -c '
import json, sys
runs = json.load(sys.stdin)["workflow_runs"]
if not runs:
    sys.exit("no successful run found")
print(runs[0]["id"], runs[0]["head_sha"])
')
echo "    run $RUN_ID  commit ${RUN_SHA:0:7}"

CURRENT=$(cat "$APP/.deployed-sha" 2>/dev/null || echo "unknown")
if [ "$RUN_SHA" = "$CURRENT" ]; then
  echo "    already deployed (${RUN_SHA:0:7}) — nothing to do"
  exit 0
fi

echo "==> 2/6 downloading artifact backend-dawn-$RUN_SHA"
ART_URL=$(api "https://api.github.com/repos/$REPO/actions/runs/$RUN_ID/artifacts" | python3 -c "
import json, sys
want = 'backend-dawn-$RUN_SHA'
for a in json.load(sys.stdin)['artifacts']:
    if a['name'] == want and not a['expired']:
        print(a['archive_download_url']); break
else:
    sys.exit(f'no live artifact named {want} (expired? CI changed?)')
")
api -L -o "$TMP/b.zip" "$ART_URL"
echo "    $(stat -c %s "$TMP/b.zip") bytes"

echo "==> 3/6 checking the artifact really contains a runnable app"
unzip -q "$TMP/b.zip" -d "$TMP/new"
# Locate the jar rather than assume where upload-artifact rooted the zip (it
# strips the least common ancestor of the uploaded paths, so the layout moves if
# the workflow's path list changes). lib/ must sit beside it, since the jar's
# manifest Class-Path is relative to the jar's own directory.
JAR=$(find "$TMP/new" -name backend-dawn.jar -type f | head -1)
if [ -z "$JAR" ]; then
  echo "!!! no backend-dawn.jar in the artifact; it contains:" >&2
  find "$TMP/new" -maxdepth 2 | sed 's/^/      /' >&2
  exit 1
fi
SRC=$(dirname "$JAR")
# A jar whose lib/ went missing starts, then dies on the first DB call — a far
# worse failure than refusing here.
[ -d "$SRC/lib" ] || { echo "!!! no lib/ beside the jar in the artifact" >&2; exit 1; }
for want in $(unzip -p "$JAR" META-INF/MANIFEST.MF | tr -d '\r' |
              sed -n 's/^Class-Path: //p' | tr ' ' '\n' | grep -v '^$'); do
  [ -f "$SRC/$want" ] || { echo "!!! manifest wants $want, not in artifact" >&2; exit 1; }
done
echo "    jar + $(ls "$SRC/lib" | wc -l) lib jar(s), manifest Class-Path satisfied"

echo "==> 4/6 backing up what is running now"
rm -rf "$PREV" && mkdir -p "$PREV"
cp -a "$APP/backend-dawn.jar" "$PREV/" 2>/dev/null || true
cp -a "$APP/lib" "$PREV/" 2>/dev/null || true
echo "    saved to $PREV ($CURRENT)"

echo "==> 5/6 installing and restarting"
cp -a "$JAR" "$APP/backend-dawn.jar"
rm -rf "$APP/lib" && cp -a "$SRC/lib" "$APP/lib"
printf '%s' "$RUN_SHA" > "$APP/.deployed-sha"
sudo systemctl restart "$SERVICE"

echo "==> 6/6 health check"
ok=""
for _ in $(seq 1 40); do
  if curl -sf -o /dev/null --max-time 3 "$HEALTH"; then ok=1; break; fi
  sleep 0.5
done

if [ -z "$ok" ]; then
  echo "!!! did not come healthy — rolling back to $CURRENT" >&2
  cp -a "$PREV/backend-dawn.jar" "$APP/backend-dawn.jar"
  rm -rf "$APP/lib" && cp -a "$PREV/lib" "$APP/lib"
  printf '%s' "$CURRENT" > "$APP/.deployed-sha"
  sudo systemctl restart "$SERVICE"
  for _ in $(seq 1 40); do
    curl -sf -o /dev/null --max-time 3 "$HEALTH" && break
    sleep 0.5
  done
  echo "!!! rolled back. logs: journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi

echo
echo "Deployed ${RUN_SHA:0:7} (run $RUN_ID) at $STAMP. Healthy on :8001."
echo "  previous build kept at $PREV — to undo:"
echo "    cp -a $PREV/backend-dawn.jar $APP/ && rm -rf $APP/lib && cp -a $PREV/lib $APP/ && sudo systemctl restart $SERVICE"
