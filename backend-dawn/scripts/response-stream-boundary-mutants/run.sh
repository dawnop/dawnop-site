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
shard_begin response-stream-boundary-mutants

# The mutators only write into a directory carrying a .mutant-workdir sentinel,
# and this proves that gate can still go red -- for every harness, not just this
# one. Without it a mutate.py that lost the guard would look exactly like one
# that has it, right up until somebody aimed it at a checkout.
python3 "$BACKEND/scripts/mutant_workdir.py" --self-test

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-response-stream-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

python3 "$FFI_CHECK" --root "$BACKEND"
printf 'PASS  base response stream assertions\n'

expected_rows=(
  'make-response-stream-transparent|ffi.response-stream-opaque'
  'leak-java-input-stream|ffi.java-input-stream-explicit-token-confined'
  'leak-raw-input-stream|ffi.response-stream-raw-confined'
  'break-owner-adapter|ffi.response-stream-owner-adapter'
  'bypass-owner-adapter|ffi.web3-response-body-seam-confined'
  'bypass-streaming-symbol|ffi.web3-streaming-symbol-confined'
  'bypass-interpolated-streaming|ffi.web3-streaming-symbol-confined'
)
declare -a rows=()
declare -a mutants=()
declare -a owners=()
declare -A seen_mutants=()

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
  if [[ -n "${seen_mutants[$mutant]:-}" ]]; then
    printf 'FAIL  duplicate matrix mutant: %s\n' "$mutant" >&2
    exit 1
  fi
  seen_mutants[$mutant]=1
  rows+=("$line")
  mutants+=("$mutant")
  owners+=("$owner")
done <"$MATRIX"

if [[ ${#rows[@]} -ne ${#expected_rows[@]} ]]; then
  printf 'FAIL  matrix has %s rows, expected %s\n' \
    "${#rows[@]}" "${#expected_rows[@]}" >&2
  exit 1
fi
for index in "${!expected_rows[@]}"; do
  if [[ "${rows[$index]}" != "${expected_rows[$index]}" ]]; then
    printf 'FAIL  matrix row %s changed: %s\n' \
      "$((index + 1))" "${rows[$index]}" >&2
    exit 1
  fi
done
printf 'PASS  response stream matrix is complete and hand-owned\n'

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  shard_record "$mutant"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$BACKEND/dawn.lock" "$project/"
  cp -R "$BACKEND/src" "$project/src"
  # mutate.py refuses a directory without this; see scripts/mutant_workdir.py
  : >"$project/.mutant-workdir"
  python3 "$MUTATOR" "$mutant" "$project"

  build_log="$work/$mutant.build.log"
  if ! "$DAWN" build "$project" -o "$work/$mutant.jar" >"$build_log" 2>&1; then
    cat "$build_log" >&2
    exit 1
  fi
  if [[ ! -s "$work/$mutant.jar" ]]; then
    printf 'FAIL  %s produced no jar\n' "$mutant" >&2
    exit 1
  fi
  printf 'PASS  %s fully builds\n' "$mutant"

  version="$("$DAWN" --version)"
  if [[ ! "$version" =~ ^dawn\ [0-9]+\.[0-9]+\.[0-9]+\ \(selfhost\)$ ]]; then
    printf 'FAIL  %s compiler returned an invalid version: %s\n' \
      "$mutant" "$version" >&2
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
    printf 'FAIL  %s turned the wrong assertion red: %s\n' \
      "$mutant" "$actual" >&2
    cat "$ffi_log" >&2
    exit 1
  fi
  printf 'PASS  %s uniquely turns red: %s\n' "$mutant" "$owner"
done

shard_report "${#mutants[@]}"
