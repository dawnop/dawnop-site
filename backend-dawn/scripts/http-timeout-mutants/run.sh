#!/usr/bin/env bash
# Negative control for src/util/http.dawn's outbound contract: the request
# deadlines, the timeout-vs-refusal classification, and the shared client.
#
# Every rule here has the same way of going wrong -- the source reads as though
# it already holds. "the source contains .timeout(" is satisfied by a deadline
# of one day; "the message says timed out" is satisfied by a status code that
# says something else; "there is one HttpClient.newBuilder() in the file" is
# satisfied by a builder that runs per call. So every mutant is checked by
# running the suite against a real loopback peer, and the set deliberately
# includes the ones that leave the giveaway in the source untouched.
#
# Every dawn invocation is wrapped in `timeout`. A deadline assertion that
# regressed into a hang must come back as a red, not as a wedged pipeline.
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
DAWN="${DAWN_BIN:-}"

# shellcheck source=backend-dawn/scripts/mutant-shard.sh
source "$BACKEND/scripts/mutant-shard.sh"
shard_parse "$@"
if [[ ${#shard_rest[@]} -ne 0 ]]; then
  printf 'usage: %s [--shard I/N]\n' "$0" >&2
  exit 2
fi
shard_begin http-timeout-mutants

# Generous next to a ~40s suite, but finite: this is the wedge guard.
STEP_TIMEOUT="${STEP_TIMEOUT:-600}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-http-timeout-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

run_tests() {
  # $1 project dir, $2 log path; returns dawn's status, 124 on wedge
  local status=0
  timeout "$STEP_TIMEOUT" "$DAWN" test "$1" >"$2" 2>&1 || status=$?
  return "$status"
}

reds() {
  # normalise `FAIL  <module> :: <name>` down to `<name>`, sorted
  sed -n 's/^FAIL  //p' "$1" | sed 's/^.* :: //' | LC_ALL=C sort
}

base_log="$work/base.test.log"
base_status=0
run_tests "$BACKEND" "$base_log" || base_status=$?
if [[ $base_status -eq 124 ]]; then
  printf 'FAIL  base suite exceeded %ss -- a deadline assertion is hanging\n' \
    "$STEP_TIMEOUT" >&2
  exit 1
fi
if [[ $base_status -ne 0 ]]; then
  cat "$base_log" >&2
  exit 1
fi
printf 'PASS  base outbound HTTP deadline assertions\n'

declare -a mutants=()
declare -a owner_sets=()
while IFS='|' read -r mutant owners; do
  [[ -z "$mutant" || "$mutant" == \#* ]] && continue
  if [[ -z "$owners" ]]; then
    printf 'FAIL  matrix entry has no owner: %s\n' "$mutant" >&2
    exit 1
  fi
  mutants+=("$mutant")
  owner_sets+=("$owners")
done <"$MATRIX"

expected_mutants=(
  client-per-call
  collapse-transfer-tier
  drop-deadline
  edge-times-out-as-502
  inflate-deadline
  mute-timeout-text
  timeout-reads-as-upstream
  timeout-tag-second-opinion
  transfer-tier-on-management
)
if [[ "${mutants[*]}" != "${expected_mutants[*]}" ]]; then
  printf 'FAIL  matrix mutant order or membership changed\n' >&2
  exit 1
fi

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  shard_record "$mutant"
  owners="${owner_sets[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$project/dawn.toml"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! timeout "$STEP_TIMEOUT" "$DAWN" build "$project" -o "$work/$mutant.jar" \
    >"$build_log" 2>&1; then
    printf 'FAIL  %s does not build; a mutant must be live code\n' "$mutant" >&2
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  test_log="$work/$mutant.test.log"
  test_status=0
  run_tests "$project" "$test_log" || test_status=$?

  if [[ $test_status -eq 124 ]]; then
    printf 'FAIL  %s wedged the suite past %ss instead of turning red\n' \
      "$mutant" "$STEP_TIMEOUT" >&2
    exit 1
  fi
  if [[ $test_status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owners red\n' "$mutant" >&2
    exit 1
  fi

  expected="$(printf '%s\n' "$owners" | sed 's/;;/\n/g' | LC_ALL=C sort)"
  actual="$(reds "$test_log")"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL  %s turned the wrong assertions red\n' "$mutant" >&2
    printf '  expected:\n%s\n  actual:\n%s\n' "$expected" "$actual" >&2
    exit 1
  fi
  red_count="$(printf '%s\n' "$actual" | grep -c .)"
  printf 'PASS  %s turns red exactly its %s owner(s)\n' "$mutant" "$red_count"
done

shard_report "${#mutants[@]}"
