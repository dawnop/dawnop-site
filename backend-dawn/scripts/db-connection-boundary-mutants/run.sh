#!/usr/bin/env bash
# Negative control for src/db/sql.dawn's two boundaries.
#
# The FFI half is checked statically (java.sql confined to this module, DbConn
# opaque); the transaction half is checked by running the suite, because what it
# claims cannot be read off the source. Both entries used to open with
# `if not conn.getAutoCommit()`, which looks exactly like a guard against a
# transaction left open on the connection and is blind to every transaction this
# backend opens -- they all come from a raw `begin immediate`, which sqlite-jdbc
# does not route through that flag. A mutant that puts the flag back has to be
# run against a connection that really carries one before it says anything.
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
FFI_CHECK="$BACKEND/scripts/check-ffi-boundaries.py"
DAWN="${DAWN_BIN:-}"

# shellcheck source=backend-dawn/scripts/mutant-shard.sh
source "$BACKEND/scripts/mutant-shard.sh"
shard_parse "$@"
if [[ ${#shard_rest[@]} -ne 0 ]]; then
  printf 'usage: %s [--shard I/N]\n' "$0" >&2
  exit 2
fi
shard_begin db-connection-boundary-mutants

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-db-conn-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

base_test="$work/base.test.log"
if ! "$DAWN" test "$BACKEND" >"$base_test" 2>&1; then
  cat "$base_test" >&2
  exit 1
fi
python3 "$FFI_CHECK" --root "$BACKEND"
printf 'PASS  base database connection and transaction assertions\n'

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
  make-dbconn-transparent
  leak-java-sql
  leak-raw-connection
  guard-tx-by-autocommit
  rollback-open-tx-on-entry
  unnamed-nested-refusal
)
if [[ "${mutants[*]}" != "${expected_mutants[*]}" ]]; then
  printf 'FAIL  matrix mutant order or membership changed\n' >&2
  exit 1
fi

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  owners="${owner_sets[$index]}"
  shard_record "$mutant"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$project/dawn.toml"
  cp -R "$BACKEND/src" "$project/src"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  version="$("$DAWN" --version)"
  if [[ "$version" != dawn\ * ]]; then
    printf 'FAIL  compiler did not answer --version: %s\n' "$version" >&2
    exit 1
  fi
  printf 'PASS  %s compiler answers version: %s\n' "$mutant" "$version"

  test_log="$work/$mutant.test.log"
  ffi_log="$work/$mutant.ffi.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  test_status=$?
  python3 "$FFI_CHECK" --root "$project" >"$ffi_log" 2>&1
  ffi_status=$?
  set -e

  if [[ $test_status -eq 0 && $ffi_status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owners red\n' "$mutant" >&2
    exit 1
  fi

  # test reds arrive as `FAIL  <module> :: <name>`, static ones as `FAIL  <name>`
  expected="$(printf '%s\n' "$owners" | sed 's/;;/\n/g' | LC_ALL=C sort)"
  actual="$( { sed -n 's/^FAIL  //p' "$test_log" | sed 's/^.* :: //'
               sed -n 's/^FAIL  //p' "$ffi_log"; } | LC_ALL=C sort)"
  if [[ "$actual" != "$expected" ]]; then
    printf 'FAIL  %s turned the wrong assertions red\n' "$mutant" >&2
    printf '  expected:\n%s\n  actual:\n%s\n' "$expected" "$actual" >&2
    exit 1
  fi
  red_count="$(printf '%s\n' "$actual" | grep -c .)"
  printf 'PASS  %s turns red exactly its %s owner(s)\n' "$mutant" "$red_count"
done

shard_report "${#mutants[@]}"
