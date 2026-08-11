#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
MATRIX_CHECK="$HERE/matrix_check.py"
DAWN="${DAWN_BIN:-}"

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-webdav-destination-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT

python3 "$MATRIX_CHECK" --self-test
python3 "$MUTATOR" --list >"$work/mutants.txt"
python3 "$MATRIX_CHECK" --matrix "$MATRIX" --mutants "$work/mutants.txt"
if python3 "$MUTATOR" unknown-mutant "$BACKEND" >"$work/unknown.log" 2>&1; then
  printf 'FAIL  mutate.py accepted an unknown mutant\n' >&2
  exit 1
fi
printf 'PASS  mutate.py rejects unknown mutants\n'

base_log="$work/base.log"
if ! "$DAWN" test "$BACKEND" >"$base_log" 2>&1; then
  cat "$base_log" >&2
  exit 1
fi
printf 'PASS  base WebDAV Destination assertions\n'

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

if printf '%s\n' "${roles[@]}" | grep -qx qiniu; then
  base_build_log="$work/base.build.log"
  if ! "$DAWN" build "$BACKEND" -o "$work/base.jar" >"$base_build_log" 2>&1; then
    cat "$base_build_log" >&2
    exit 1
  fi
  base_qiniu_log="$work/base.qiniu.log"
  if ! python3 "$BACKEND/scripts/contract_run.py" \
    --jar "$work/base.jar" --only qiniu >"$base_qiniu_log" 2>&1; then
    cat "$base_qiniu_log" >&2
    exit 1
  fi
  printf 'PASS  base qiniu Destination contract\n'
fi

for index in "${!mutants[@]}"; do
  mutant="${mutants[$index]}"
  role="${roles[$index]}"
  owner="${owners[$index]}"
  project="$work/$mutant/backend-dawn"
  mkdir -p "$project"
  cp "$BACKEND/dawn.toml" "$BACKEND/dawn.lock" "$project/"
  cp -R "$BACKEND/src" "$project/src"
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

  test_log="$work/$mutant.test.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  status=$?
  set -e
  if [[ "$role" == test ]]; then
    if [[ $status -eq 0 ]]; then
      printf 'FAIL  %s did not turn its owner red\n' "$mutant" >&2
      cat "$test_log" >&2
      exit 1
    fi

    mapfile -t failures < <(sed -n 's/^FAIL  //p' "$test_log")
    if [[ ${#failures[@]} -ne 1 ]]; then
      printf 'FAIL  %s produced %s failing assertions, expected one\n' \
        "$mutant" "${#failures[@]}" >&2
      cat "$test_log" >&2
      exit 1
    fi

    actual="${failures[0]}"
    if [[ "$actual" != "$owner" && "$actual" != *" :: $owner" ]]; then
      printf 'FAIL  %s turned the wrong assertion red: %s\n' "$mutant" "$actual" >&2
      cat "$test_log" >&2
      exit 1
    fi
  elif [[ "$role" == qiniu ]]; then
    if [[ $status -ne 0 ]]; then
      printf 'FAIL  %s unexpectedly failed Dawn assertions\n' "$mutant" >&2
      cat "$test_log" >&2
      exit 1
    fi

    contract_log="$work/$mutant.qiniu.log"
    set +e
    python3 "$BACKEND/scripts/contract_run.py" \
      --jar "$work/$mutant.jar" --only qiniu >"$contract_log" 2>&1
    contract_status=$?
    set -e
    if [[ $contract_status -eq 0 ]]; then
      printf 'FAIL  %s did not turn its qiniu owner red\n' "$mutant" >&2
      cat "$contract_log" >&2
      exit 1
    fi
    mapfile -t failures < <(
      python3 - "$contract_log" <<'PY'
import pathlib
import re
import sys

ansi = re.compile(r"\x1b\[[0-9;]*m")
for raw in pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    line = ansi.sub("", raw)
    match = re.match(r"\s*\[DIFF\]\s+(.+)$", line)
    if match:
        print(match.group(1))
PY
    )
    if [[ ${#failures[@]} -ne 1 || "${failures[0]}" != "$owner" ]]; then
      printf 'FAIL  %s produced the wrong qiniu red set\n' "$mutant" >&2
      cat "$contract_log" >&2
      exit 1
    fi
  else
    printf 'FAIL  matrix checker let unknown role through: %s\n' "$role" >&2
    exit 1
  fi
  printf 'PASS  %s (%s) uniquely turns red: %s\n' "$mutant" "$role" "$owner"
done
