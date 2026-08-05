#!/usr/bin/env bash
# Deploy the backend from the artifact CI built and tested.
#
# The jar is not in git (it is build output; a committed one drifts from src/
# silently). CI builds jar + lib/ on every push to main and uploads them as
# `backend-dawn-<sha>`. This script pulls that exact artifact, so what runs in
# production is what CI tested, from a known commit.
#
# Run as root from the laptop (same convention as rollback-to-fastapi.sh) — it
# installs code the service must not be able to rewrite, and restarts the unit:
#   ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh        # latest green main
#   ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh <sha>  # a specific commit
#
# Needs a GitHub token at /opt/dawnop-dawn/.github-token (root, chmod 600) —
# artifact downloads require auth even for a public repo. A fine-grained PAT
# scoped to this repo with Actions: read-only is enough; nothing here writes to
# GitHub.
#
# The artifact download goes through a local forward proxy configured in
# $APP/.deploy-proxy; see PROXY below for why, why only that one call, and what
# happens without it. Do not set proxy variables on the invocation — the script
# handles it, and the comment there explains what that shortcut breaks.
#
# A bad deploy self-reverts: the running jar is kept and restored if the new one
# does not come up healthy. The exception is the very first deploy onto a fresh
# machine, where there is nothing to revert *to* — the script says so up front
# rather than discovering it in the failure path.
set -euo pipefail

REPO="dawnop/dawnop-site"
APP="/opt/dawnop-dawn"
SERVICE="dawnop-dawn"
TOKEN_FILE="$APP/.github-token"
HEALTH="http://127.0.0.1:8001/api/health"
WANT_SHA="${1:-}"

# Artifact *bytes* do not come from api.github.com — it 302s to Azure blob
# storage, and that leg is throttled to uselessness from this host. Measured on
# the same 10MB artifact, minutes apart: 11.7 KB/s direct (~15 min for 10MB, if
# it finishes at all) vs 290 KB/s through the host's local forward proxy (35s).
# The GitHub *API* is fine direct (~1.8s round trip) and is deliberately left
# unproxied — only the download needs this.
#
# The endpoint is read from a file rather than written here, for the same reason
# .github-token is: it is a fact about this particular host, not about the app,
# and the machine's bypass plumbing is kept out of the public repo (the private
# ops notes own it). One line, `host:port`:
#     install -m 600 /dev/stdin $PROXY_FILE
# Absent, the download still works — it just crawls, and says so.
PROXY_FILE="$APP/.deploy-proxy"
PROXY="${DEPLOY_PROXY-$(cat "$PROXY_FILE" 2>/dev/null || true)}"
PROXY=$(printf '%s' "$PROXY" | tr -d ' \t\n\r')

# Two things this deliberately does NOT do, both trapdoors:
#
# 1. It does not export http_proxy/https_proxy for the whole script. The step-6
#    health check is http://127.0.0.1:8001, so an exported http_proxy captures
#    it too; the proxy accepts the connection and then hangs, every one of the 40
#    attempts times out, and the script rolls back a deploy that was perfectly
#    healthy. Instead the proxy is passed as `curl --proxy` on the single call
#    that needs it, so there is no no_proxy incantation left to forget.
#    (Exporting only https_proxy does not trip this — curl selects the proxy
#    variable by URL scheme and the health check is http. Both measured.)
# 2. It does not honour an inherited proxy env, which would re-arm exactly that
#    trap. The advice this replaces was "prefix the ssh command with the proxy
#    variables", and someone will do it again from muscle memory or an old note.
unset http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY

if [ "$(id -u)" -ne 0 ]; then
  # Not "$0": the intended invocation pipes this file over ssh, so $0 is "bash"
  # and the message would tell you to redirect from a file called `bash`.
  echo "!!! run as root: ssh <user>@<server> 'sudo bash -s' < backend-dawn/deploy/deploy.sh" >&2
  exit 1
fi
# Deployed code is root-owned and world-readable: the service account only ever
# needs to *read* its jar. Giving it ownership would make "compromise the
# service" and "rewrite the code it runs next boot" the same step. The unit's
# ProtectSystem=strict blocks that today, but ownership should not be the only
# thing standing between an RCE and persistence — /usr/bin is not owned by the
# daemons that run from it either.
OWNER="root:root"

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
DL=()
if [ -z "$PROXY" ]; then
  echo "    no proxy configured ($PROXY_FILE) — direct download, expect this to crawl" >&2
elif timeout 2 bash -c "exec 3<>/dev/tcp/${PROXY%:*}/${PROXY##*:}" 2>/dev/null; then
  # Probe rather than assume: if the proxy is down, `--proxy` fails the download
  # outright, and "proxy missing" should degrade to slow, not to broken.
  DL=(--proxy "$PROXY")
  echo "    via proxy $PROXY"
else
  echo "    proxy $PROXY unreachable — direct download, expect this to crawl" >&2
fi
# Give up rather than hang: under 1 KB/s for 60s straight is the direct-download
# failure mode, and an unattended deploy that stalls forever at 3am is worse than
# one that stops with an error.
api "${DL[@]}" --speed-limit 1024 --speed-time 60 -L -o "$TMP/b.zip" "$ART_URL"
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
echo "    jar + $(find "$SRC/lib" -name '*.jar' -type f | wc -l) lib jar(s), manifest Class-Path satisfied"

echo "==> 4/6 backing up what is running now"
rm -rf "${PREV:?}" && mkdir -p "$PREV"
cp -a "$APP/backend-dawn.jar" "$PREV/" 2>/dev/null || true
cp -a "$APP/lib" "$PREV/" 2>/dev/null || true
# Whether the backup is complete decides whether the health check below may roll
# back at all. On a first deploy there is nothing running to save, and the two
# `|| true` above quietly leave $PREV empty — so the rollback path would die on
# its first `cp` under `set -e`, having restored nothing and leaving the bad jar
# installed. Deciding it here, before anything is installed, keeps the failure
# path honest about what it can actually do.
CAN_ROLLBACK=""
if [ -f "$PREV/backend-dawn.jar" ] && [ -d "$PREV/lib" ]; then
  CAN_ROLLBACK=1
  echo "    saved to $PREV ($CURRENT)"
else
  echo "    nothing running to back up — first deploy; health check cannot roll back"
fi

echo "==> 5/6 installing and restarting"
cp -a "$JAR" "$APP/backend-dawn.jar"
rm -rf "${APP:?}/lib" && cp -a "$SRC/lib" "$APP/lib"
printf '%s' "$RUN_SHA" > "$APP/.deployed-sha"
chown -R "$OWNER" "$APP/backend-dawn.jar" "$APP/lib" "$APP/.deployed-sha"
chmod 644 "$APP/backend-dawn.jar" "$APP/.deployed-sha"
chmod 755 "$APP/lib" && chmod 644 "$APP/lib"/*.jar
# The directory too, not just its contents: write permission on the dir lets you
# unlink a file you cannot write and drop a replacement in its place, which is
# the same attack one step removed.
chown "$OWNER" "$APP" && chmod 755 "$APP"
systemctl restart "$SERVICE"

echo "==> 6/6 health check"
ok=""
for _ in $(seq 1 40); do
  if curl -sf -o /dev/null --max-time 3 "$HEALTH"; then ok=1; break; fi
  sleep 0.5
done

if [ -z "$ok" ]; then
  if [ -z "$CAN_ROLLBACK" ]; then
    echo "!!! did not come healthy, and there is no previous build to roll back to." >&2
    echo "    the new jar is installed and the service is down." >&2
    echo "    logs: journalctl -u $SERVICE -n 50 --no-pager" >&2
    exit 1
  fi
  echo "!!! did not come healthy — rolling back to $CURRENT" >&2
  cp -a "$PREV/backend-dawn.jar" "$APP/backend-dawn.jar"
  rm -rf "${APP:?}/lib" && cp -a "$PREV/lib" "$APP/lib"
  # "unknown" is not a sha. Recording it would make .deployed-sha lie about what
  # is installed; absent is the honest state, and step 1 already treats a missing
  # file as "deploy whatever is green".
  if [ "$CURRENT" = "unknown" ]; then
    rm -f "$APP/.deployed-sha"
  else
    printf '%s' "$CURRENT" > "$APP/.deployed-sha"
  fi
  # .deployed-sha is named separately because the branch above may have removed it.
  chown -R "$OWNER" "$APP/backend-dawn.jar" "$APP/lib"
  chmod 644 "$APP/backend-dawn.jar"
  if [ -f "$APP/.deployed-sha" ]; then
    chown "$OWNER" "$APP/.deployed-sha" && chmod 644 "$APP/.deployed-sha"
  fi
  chmod 755 "$APP/lib" && chmod 644 "$APP/lib"/*.jar
  systemctl restart "$SERVICE"
  back=""
  for _ in $(seq 1 40); do
    if curl -sf -o /dev/null --max-time 3 "$HEALTH"; then back=1; break; fi
    sleep 0.5
  done
  # Say which of the two happened. "rolled back" on its own was true about the
  # files and silent about the service, so a rollback that also failed to come up
  # printed the same line as one that worked — the site is down either way, but
  # only one of them means the previous build is the problem too.
  if [ -n "$back" ]; then
    echo "!!! rolled back to $CURRENT and healthy again on :8001." >&2
  else
    echo "!!! rolled back to $CURRENT, but it is NOT healthy either — site is down." >&2
  fi
  echo "    logs: journalctl -u $SERVICE -n 50 --no-pager" >&2
  exit 1
fi

echo
echo "Deployed ${RUN_SHA:0:7} (run $RUN_ID) at $STAMP. Healthy on :8001."

# .prev holds the jar and lib/ but not the sha that goes with them — $CURRENT is
# the only record of it, and it dies with this shell. So bake it into the printed
# command rather than leaving the reader to reconstruct it.
#
# Restoring .deployed-sha is not cosmetic. Undoing without it leaves the file
# claiming $RUN_SHA while $CURRENT's jar is running, and step 1 compares exactly
# that file: the next deploy of $RUN_SHA says "already deployed — nothing to do"
# and refuses to reinstall it. That is a stuck deploy pipeline, discovered by
# someone who is already dealing with whatever made them undo.
if [ "$CURRENT" = "unknown" ]; then
  # Nothing legitimate to restore it to; absent is the honest state, same as the
  # automatic rollback path above.
  UNDO_SHA="rm -f $APP/.deployed-sha && chown -R root:root $APP/backend-dawn.jar $APP/lib"
else
  UNDO_SHA="printf %s $CURRENT > $APP/.deployed-sha && chown -R root:root $APP/backend-dawn.jar $APP/lib $APP/.deployed-sha"
fi
echo "  previous build kept at $PREV — to undo:"
echo "    cp -a $PREV/backend-dawn.jar $APP/ && rm -rf $APP/lib && cp -a $PREV/lib $APP/ && $UNDO_SHA && systemctl restart $SERVICE"
