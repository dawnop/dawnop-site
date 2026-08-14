#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
MATRIX_CHECK="$HERE/matrix_check.py"
DAWN="${DAWN_BIN:-}"

# shellcheck source=backend-dawn/scripts/mutant-shard.sh
source "$BACKEND/scripts/mutant-shard.sh"
shard_parse "$@"
if [[ ${#shard_rest[@]} -ne 0 ]]; then
  printf 'usage: %s [--shard I/N]\n' "$0" >&2
  exit 2
fi
shard_begin webdav-destination-mutants

# The mutators only write into a directory carrying a .mutant-workdir sentinel,
# and this proves that gate can still go red -- for every harness, not just this
# one. Without it a mutate.py that lost the guard would look exactly like one
# that has it, right up until somebody aimed it at a checkout.
python3 "$BACKEND/scripts/mutant_workdir.py" --self-test

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-webdav-destination-mutants.XXXXXX")"
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

validate_qiniu_report() {
  python3 - "$1" "$2" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
owner = sys.argv[2]
if not path.is_file():
    raise SystemExit(f"missing qiniu machine report: {path}")
report = json.loads(path.read_text(encoding="utf-8"))
expected_diff = [owner] if owner else []
checks = {
    "schema": report.get("schema") == "dawnop.contract-golden.v1",
    "name": report.get("name") == "qiniu",
    "verify mode": report.get("record") is False,
    "complete": report.get("complete") is True,
    "fatal": report.get("fatal") is None,
    "diff": report.get("diff") == expected_diff,
    "new": report.get("new") == [],
    "missing": report.get("missing") == [],
    "skip_added": report.get("skip_added") == [],
    "skip_gone": report.get("skip_gone") == [],
    "bad": report.get("bad") is bool(owner),
}
wrong = [name for name, ok in checks.items() if not ok]
if wrong:
    raise SystemExit(f"invalid qiniu machine report fields: {', '.join(wrong)}")
PY
}

cat >"$work/test-report.complete" <<'EOF'
PASS  first
FAIL  second
1 of 2 test(s) failed
EOF
read -r report_total report_failed <<<"$(read_dawn_test_report "$work/test-report.complete")"
if [[ "$report_total" != 2 || "$report_failed" != 1 ]]; then
  printf 'FAIL  Dawn test report parser rejected a complete report\n' >&2
  exit 1
fi
cat >"$work/test-report.truncated" <<'EOF'
PASS  first
FAIL  second
EOF
if read_dawn_test_report "$work/test-report.truncated" >/dev/null 2>&1; then
  printf 'FAIL  Dawn test report parser accepted a truncated report\n' >&2
  exit 1
fi
printf 'PASS  Dawn test report completeness negative control\n'

cat >"$work/qiniu-report.valid" <<'EOF'
{"bad":true,"complete":true,"diff":["owner"],"fatal":null,"missing":[],"name":"qiniu","new":[],"record":false,"schema":"dawnop.contract-golden.v1","skip_added":[],"skip_gone":[]}
EOF
validate_qiniu_report "$work/qiniu-report.valid" owner
cat >"$work/qiniu-report.collateral" <<'EOF'
{"bad":true,"complete":true,"diff":["owner"],"fatal":null,"missing":[],"name":"qiniu","new":["collateral"],"record":false,"schema":"dawnop.contract-golden.v1","skip_added":[],"skip_gone":[]}
EOF
if validate_qiniu_report "$work/qiniu-report.collateral" owner >/dev/null 2>&1; then
  printf 'FAIL  qiniu report parser accepted NEW collateral\n' >&2
  exit 1
fi
printf 'PASS  qiniu report collateral negative control\n'

python3 "$MATRIX_CHECK" --self-test
python3 "$MUTATOR" --list >"$work/mutants.txt"
python3 "$MATRIX_CHECK" --matrix "$MATRIX" --mutants "$work/mutants.txt"
# Aimed at a scratch path, not at "$BACKEND": the name is rejected before the
# project argument is ever looked at, so pointing this at the live checkout only
# ever proved that argparse runs first. Nothing here should be able to write to
# a tree we keep, whichever check fires.
if python3 "$MUTATOR" unknown-mutant "$work/unknown-probe" >"$work/unknown.log" 2>&1; then
  printf 'FAIL  mutate.py accepted an unknown mutant\n' >&2
  exit 1
fi
printf 'PASS  mutate.py rejects unknown mutants\n'

base_log="$work/base.log"
if ! "$DAWN" test "$BACKEND" >"$base_log" 2>&1; then
  cat "$base_log" >&2
  exit 1
fi
if ! base_test_report="$(read_dawn_test_report "$base_log")"; then
  cat "$base_log" >&2
  exit 1
fi
read -r base_test_total base_test_failed <<<"$base_test_report"
if [[ "$base_test_failed" != 0 ]]; then
  printf 'FAIL  base Dawn test report is not green\n' >&2
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
  base_qiniu_report="$work/base.qiniu.json"
  if ! CONTRACT_GOLDEN_REPORT="$base_qiniu_report" python3 "$BACKEND/scripts/contract_run.py" \
    --jar "$work/base.jar" --only qiniu >"$base_qiniu_log" 2>&1; then
    cat "$base_qiniu_log" >&2
    exit 1
  fi
  if ! validate_qiniu_report "$base_qiniu_report" ""; then
    cat "$base_qiniu_log" >&2
    exit 1
  fi
  printf 'PASS  base qiniu Destination contract\n'
fi

for index in "${!mutants[@]}"; do
  if shard_skips "$index"; then continue; fi
  mutant="${mutants[$index]}"
  shard_record "$mutant"
  role="${roles[$index]}"
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

  test_log="$work/$mutant.test.log"
  set +e
  "$DAWN" test "$project" >"$test_log" 2>&1
  status=$?
  set -e
  if ! test_report="$(read_dawn_test_report "$test_log")"; then
    printf 'FAIL  %s produced an incomplete Dawn test report\n' "$mutant" >&2
    cat "$test_log" >&2
    exit 1
  fi
  read -r test_total test_failed <<<"$test_report"
  if [[ "$test_total" != "$base_test_total" ]]; then
    printf 'FAIL  %s ran %s Dawn tests, base ran %s\n' \
      "$mutant" "$test_total" "$base_test_total" >&2
    cat "$test_log" >&2
    exit 1
  fi
  if [[ "$role" == test ]]; then
    if [[ $status -eq 0 || "$test_failed" != 1 ]]; then
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
    if [[ $status -ne 0 || "$test_failed" != 0 ]]; then
      printf 'FAIL  %s unexpectedly failed Dawn assertions\n' "$mutant" >&2
      cat "$test_log" >&2
      exit 1
    fi

    contract_log="$work/$mutant.qiniu.log"
    contract_report="$work/$mutant.qiniu.json"
    set +e
    CONTRACT_GOLDEN_REPORT="$contract_report" python3 "$BACKEND/scripts/contract_run.py" \
      --jar "$work/$mutant.jar" --only qiniu >"$contract_log" 2>&1
    contract_status=$?
    set -e
    if [[ $contract_status -eq 0 ]]; then
      printf 'FAIL  %s did not turn its qiniu owner red\n' "$mutant" >&2
      cat "$contract_log" >&2
      exit 1
    fi
    if ! validate_qiniu_report "$contract_report" "$owner"; then
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

shard_report "${#mutants[@]}"
