#!/usr/bin/env bash
set -euo pipefail

# Negative control for the "updated_at only moves on a real edit" assertions
# (#264, #266). Those assertions are green every single day, and a green that
# has never been able to go red carries no information — the whole family sits
# on three one-line guards that a refactor could delete without any other test
# noticing. Each mutant below deletes or weakens one of them and must turn red
# exactly the assertions that claim to own it: no fewer (the guard was
# unguarded) and no more (the assertion is red for someone else's reason).
#
# Usage:
#   run.sh                       # the whole matrix
#   run.sh --shard 0/2           # CI's slice
#   run.sh --list
#   run.sh --only article-never-touches [--only ...]
#   run.sh --preflight-only      # registry + anchors + base green, no mutants

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
DAWN="${DAWN_BIN:-}"

# shellcheck source=backend-dawn/scripts/mutant-shard.sh
source "$BACKEND/scripts/mutant-shard.sh"
shard_parse "$@"

list_only=false
preflight_only=false
declare -a only=()
index=0
while [[ $index -lt ${#shard_rest[@]} ]]; do
  case "${shard_rest[$index]}" in
    --list) list_only=true ;;
    --preflight-only) preflight_only=true ;;
    --only)
      index=$((index + 1))
      if [[ $index -ge ${#shard_rest[@]} ]]; then
        printf 'FAIL  --only needs a mutant name\n' >&2
        exit 2
      fi
      only+=("${shard_rest[$index]}")
      ;;
    --only=*) only+=("${shard_rest[$index]#--only=}") ;;
    *)
      printf 'usage: %s [--shard I/N] [--list] [--only NAME] [--preflight-only]\n' "$0" >&2
      exit 2
      ;;
  esac
  index=$((index + 1))
done

shard_begin updated-at-boundary-mutants

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-updated-at-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

read_dawn_test_report() {
  python3 - "$1" <<'PY'
import pathlib
import re
import sys

lines = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8", errors="replace").splitlines()
passed = sum(line.startswith("PASS  ") for line in lines)
failed = sum(line.startswith("FAIL  ") for line in lines)
green = re.compile(r"^(\d+) test\(s\) passed$")
red = re.compile(r"^(\d+) of (\d+) test\(s\) failed$")
summaries = [line for line in lines if green.fullmatch(line) or red.fullmatch(line)]
if len(summaries) != 1:
    raise SystemExit(f"expected one Dawn test summary, found {len(summaries)}")
match = green.fullmatch(summaries[0])
if match:
    total = int(match.group(1))
    summary_failed = 0
else:
    match = red.fullmatch(summaries[0])
    summary_failed = int(match.group(1))
    total = int(match.group(2))
if total == 0:
    raise SystemExit("Dawn test summary claims zero tests")
if passed + failed != total:
    raise SystemExit(
        f"Dawn test log has {passed} PASS + {failed} FAIL lines, summary says {total}"
    )
if failed != summary_failed:
    raise SystemExit(
        f"Dawn test log has {failed} FAIL lines, summary says {summary_failed}"
    )
print(total, failed)
PY
}

# ---- the matrix, and the mutator's own list held against it ----

declare -a mutants=()
declare -a owners=()
declare -A seen=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* ]] && continue
  if [[ "$line" != *'|'* || "${line#*|}" == *'|'* ]]; then
    printf 'FAIL  malformed matrix row: %s\n' "$line" >&2
    exit 1
  fi
  mutant="${line%%|*}"
  owner="${line#*|}"
  if [[ -z "$mutant" || -z "$owner" ]]; then
    printf 'FAIL  incomplete matrix row: %s\n' "$line" >&2
    exit 1
  fi
  if [[ -n "${seen[$mutant]:-}" ]]; then
    printf 'FAIL  duplicate matrix mutant: %s\n' "$mutant" >&2
    exit 1
  fi
  seen[$mutant]=1
  mutants+=("$mutant")
  owners+=("$owner")
done <"$MATRIX"

if [[ ${#mutants[@]} -eq 0 ]]; then
  printf 'FAIL  matrix lists no mutants\n' >&2
  exit 1
fi

if [[ "$list_only" == true ]]; then
  for i in "${!mutants[@]}"; do
    printf '%s\n' "${mutants[$i]}"
  done
  exit 0
fi

# The matrix is what this runner iterates and what mutants_coverage.py reads;
# the mutator is what actually edits source. A mutant present in one and not the
# other is a hole, so neither gets to be the sole register.
mapfile -t mutator_list < <(python3 "$MUTATOR" --list)
matrix_sorted="$(printf '%s\n' "${mutants[@]}" | LC_ALL=C sort)"
mutator_sorted="$(printf '%s\n' "${mutator_list[@]}" | LC_ALL=C sort)"
if [[ "$matrix_sorted" != "$mutator_sorted" ]]; then
  printf 'FAIL  matrix and mutate.py disagree on the mutant set\n' >&2
  diff <(printf '%s\n' "$matrix_sorted") <(printf '%s\n' "$mutator_sorted") >&2 || true
  exit 1
fi
printf 'PASS  matrix and mutate.py list the same %s mutant(s)\n' "${#mutants[@]}"

# ---- anchors ----
# A mutant whose anchor drifted would otherwise fail to apply only when its
# shard happened to run it. Every anchor is checked on every run, sharded or not.
anchor_probe="$work/anchors/backend-dawn"
for mutant in "${mutants[@]}"; do
  rm -rf "$work/anchors"
  mkdir -p "$anchor_probe"
  cp -R "$BACKEND/src" "$anchor_probe/src"
  if ! python3 "$MUTATOR" "$mutant" "$anchor_probe" >"$work/$mutant.anchor.log" 2>&1; then
    printf 'FAIL  %s no longer finds its anchor\n' "$mutant" >&2
    cat "$work/$mutant.anchor.log" >&2
    exit 1
  fi
done
rm -rf "$work/anchors"
printf 'PASS  all %s mutants still find their anchors\n' "${#mutants[@]}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

# ---- base ----

base_test_log="$work/base.test.log"
if ! "$DAWN" test "$BACKEND" >"$base_test_log" 2>&1; then
  cat "$base_test_log" >&2
  exit 1
fi
if ! base_report="$(read_dawn_test_report "$base_test_log")"; then
  cat "$base_test_log" >&2
  exit 1
fi
read -r base_total base_failed <<<"$base_report"
if [[ "$base_failed" != 0 ]]; then
  printf 'FAIL  base Dawn test report is not green\n' >&2
  cat "$base_test_log" >&2
  exit 1
fi
printf 'PASS  base Dawn assertions (%s tests)\n' "$base_total"

# Every owner named in the matrix must exist in the base run, or a typo would
# read as "this assertion never goes red" and quietly shrink the expected set.
mapfile -t base_names < <(sed -n 's/^PASS  //p' "$base_test_log")

# matrix.txt separates a mutant's owners with commas, so an owner whose title
# contains one would split into two names that match nothing. The existence
# check below would already fail, but on two halves of a name — say so plainly
# instead. Only titles this matrix actually names are the harness's business;
# the wider suite is free to use commas.
all_owners="$(printf '%s|' "${owners[@]}")"
for have in "${base_names[@]}"; do
  if [[ "$have" == *,* && "$all_owners" == *"$have"* ]]; then
    printf 'FAIL  this matrix names an assertion whose title contains a comma, which is its separator: %s\n' \
      "$have" >&2
    printf '      rename the assertion, or give matrix.txt a separator it cannot contain\n' >&2
    exit 1
  fi
done
for i in "${!owners[@]}"; do
  IFS=',' read -r -a want <<<"${owners[$i]}"
  for name in "${want[@]}"; do
    found=false
    for have in "${base_names[@]}"; do
      if [[ "$have" == "$name" ]]; then found=true; break; fi
    done
    if [[ "$found" != true ]]; then
      printf 'FAIL  %s names an assertion the base run does not have: %s\n' \
        "${mutants[$i]}" "$name" >&2
      exit 1
    fi
  done
done
printf 'PASS  every matrix owner exists in the base run\n'

if [[ "$preflight_only" == true ]]; then
  shard_report "${#mutants[@]}"
  exit 0
fi

# ---- the mutants ----

for i in "${!mutants[@]}"; do
  mutant="${mutants[$i]}"
  if [[ ${#only[@]} -gt 0 ]]; then
    wanted=false
    for pick in "${only[@]}"; do
      if [[ "$pick" == "$mutant" ]]; then wanted=true; break; fi
    done
    [[ "$wanted" == true ]] || continue
  elif shard_skips "$i"; then
    continue
  fi
  shard_record "$mutant"
  owner="${owners[$i]}"

  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$BACKEND/dawn.lock" "$project/"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    printf 'FAIL  %s does not build; a mutant that cannot compile tests nothing\n' \
      "$mutant" >&2
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  test_log="$work/$mutant.test.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  test_status=$?
  set -e

  if ! report="$(read_dawn_test_report "$test_log")"; then
    printf 'FAIL  %s produced an incomplete Dawn test report\n' "$mutant" >&2
    cat "$test_log" >&2
    exit 1
  fi
  read -r total failed <<<"$report"
  if [[ "$total" != "$base_total" ]]; then
    printf 'FAIL  %s ran %s Dawn tests, base ran %s\n' "$mutant" "$total" "$base_total" >&2
    exit 1
  fi
  if [[ $test_status -eq 0 || "$failed" == 0 ]]; then
    printf 'FAIL  %s turned nothing red: the guard it removes is unguarded\n' "$mutant" >&2
    exit 1
  fi

  actual="$(sed -n 's/^FAIL  //p' "$test_log" | LC_ALL=C sort | paste -sd, -)"
  expected="$(printf '%s' "$owner" | tr ',' '\n' | LC_ALL=C sort | paste -sd, -)"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL  %s produced the wrong red set\n' "$mutant" >&2
    printf '      expected: %s\n' "$expected" >&2
    printf '      actual:   %s\n' "$actual" >&2
    exit 1
  fi
  printf 'PASS  %s turns red exactly: %s\n' "$mutant" "$expected"
done

shard_report "${#mutants[@]}"
