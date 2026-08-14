#!/usr/bin/env bash
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

python3 "$FFI_CHECK" --root "$BACKEND"
printf 'PASS  base database connection assertions\n'

declare -a mutants=()
declare -a owners=()
while IFS='|' read -r mutant owner; do
  [[ -z "$mutant" || "$mutant" == \#* ]] && continue
  if [[ -z "$owner" ]]; then
    printf 'FAIL  matrix entry has no owner: %s\n' "$mutant" >&2
    exit 1
  fi
  mutants+=("$mutant")
  owners+=("$owner")
done <"$MATRIX"

expected_mutants=(make-dbconn-transparent leak-java-sql leak-raw-connection)
if [[ "${mutants[*]}" != "${expected_mutants[*]}" ]]; then
  printf 'FAIL  matrix mutant order or membership changed\n' >&2
  exit 1
fi

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  owner="${owners[$index]}"
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

  ffi_log="$work/$mutant.ffi.log"
  set +e
  python3 "$FFI_CHECK" --root "$project" >"$ffi_log" 2>&1
  ffi_status=$?
  set -e

  mapfile -t failures < <(sed -n 's/^FAIL  //p' "$ffi_log")
  if [[ $ffi_status -eq 0 ]]; then
    printf 'FAIL  %s did not turn its owner red\n' "$mutant" >&2
    exit 1
  fi
  if [[ ${#failures[@]} -ne 1 ]]; then
    printf 'FAIL  %s produced %s red assertions, expected one\n' \
      "$mutant" "${#failures[@]}" >&2
    cat "$ffi_log" >&2
    exit 1
  fi

  actual="${failures[0]}"
  if [[ "$actual" != "$owner" ]]; then
    printf 'FAIL  %s turned the wrong assertion red: %s\n' "$mutant" "$actual" >&2
    cat "$ffi_log" >&2
    exit 1
  fi
  printf 'PASS  %s uniquely turns red: %s\n' "$mutant" "$owner"
done

shard_report "${#mutants[@]}"
