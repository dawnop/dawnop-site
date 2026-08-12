#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
MATRIX_CHECK="$HERE/matrix_check.py"
CONTRACT="$HERE/run.py"
DAWN="${DAWN_BIN:-}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-fm-ancestor-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT
started="$(date +%s)"

python3 "$MATRIX_CHECK" --self-test
python3 "$MUTATOR" --list >"$work/mutants.txt"
python3 "$MATRIX_CHECK" --matrix "$MATRIX" --mutants "$work/mutants.txt"
if python3 "$MUTATOR" unknown-mutant "$BACKEND" >"$work/unknown.log" 2>&1; then
  printf 'FAIL  mutate.py accepted an unknown mutant\n' >&2
  exit 1
fi
printf 'PASS  mutate.py rejects unknown mutants\n'

base_test_log="$work/base.test.log"
if ! "$DAWN" test "$BACKEND" >"$base_test_log" 2>&1; then
  cat "$base_test_log" >&2
  exit 1
fi
printf 'PASS  base Dawn assertions\n'

base_jar="$work/base/backend-dawn.jar"
mkdir -p "$(dirname -- "$base_jar")"
base_build_log="$work/base.build.log"
if ! "$DAWN" build "$BACKEND" -o "$base_jar" >"$base_build_log" 2>&1; then
  cat "$base_build_log" >&2
  exit 1
fi
base_contract_log="$work/base.contract.log"
if ! python3 "$CONTRACT" --jar "$base_jar" --port 18320 >"$base_contract_log" 2>&1; then
  cat "$base_contract_log" >&2
  exit 1
fi
printf 'PASS  base FM ancestor contract\n'

declare -a mutants=()
declare -a roles=()
declare -a owners=()
while IFS= read -r line || [[ -n "$line" ]]; do
  [[ -z "$line" || "$line" == \#* || "$line" == version=* ]] && continue
  IFS='|' read -r mutant role owner <<<"$line"
  mutants+=("$mutant")
  roles+=("$role")
  owners+=("$owner")
done <"$MATRIX"

for index in "${!mutants[@]}"; do
  mutant="${mutants[$index]}"
  role="${roles[$index]}"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$BACKEND/dawn.lock" "$project/"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  jar="$project/backend-dawn.jar"
  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  [[ -s "$jar" ]] || { printf 'FAIL  %s produced no jar\n' "$mutant" >&2; exit 1; }
  printf 'PASS  %s fully builds\n' "$mutant"

  test_log="$work/$mutant.test.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  test_status=$?
  set -e

  contract_log="$work/$mutant.contract.log"
  port=$((18322 + index * 2))
  set +e
  python3 "$CONTRACT" --jar "$jar" --port "$port" >"$contract_log" 2>&1
  contract_status=$?
  set -e

  mapfile -t test_failures < <(
    sed -n 's/^FAIL  //p' "$test_log" | sed 's/^.* :: //'
  )
  mapfile -t contract_failures < <(
    sed -n 's/^FAIL  \([^:]*\):.*$/\1/p' "$contract_log"
  )
  failures=("${test_failures[@]}" "${contract_failures[@]}")
  if [[ ${#failures[@]} -ne 1 || "${failures[0]}" != "$owner" ]]; then
    printf 'FAIL  %s produced the wrong red set (role %s)\n' "$mutant" "$role" >&2
    cat "$test_log" >&2
    cat "$contract_log" >&2
    exit 1
  fi
  if [[ "$role" == test && ( $test_status -eq 0 || $contract_status -ne 0 ) ]]; then
    printf 'FAIL  %s did not isolate its test owner\n' "$mutant" >&2
    exit 1
  fi
  if [[ "$role" == contract && ( $test_status -ne 0 || $contract_status -eq 0 ) ]]; then
    printf 'FAIL  %s did not isolate its contract owner\n' "$mutant" >&2
    exit 1
  fi
  printf 'PASS  %s (%s) uniquely turns red: %s\n' "$mutant" "$role" "$owner"
done

elapsed=$(( $(date +%s) - started ))
printf 'PASS  FM ancestor mutation matrix: %s mutants in %ss\n' "${#mutants[@]}" "$elapsed"
