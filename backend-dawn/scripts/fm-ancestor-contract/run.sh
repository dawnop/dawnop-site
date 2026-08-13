#!/usr/bin/env bash
set -euo pipefail

HERE="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
ROOT="$(CDPATH='' cd -- "$HERE/../../.." && pwd)"
BACKEND="$ROOT/backend-dawn"
MUTATOR="$HERE/mutate.py"
MATRIX="$HERE/matrix.txt"
MATRIX_CHECK="$HERE/matrix_check.py"
CONTRACT="$HERE/run.py"
QINIU_CONTRACT="$BACKEND/scripts/contract_run.py"
QINIU_SOURCE="$BACKEND/scripts/contract_qiniu.py"
EXPECTED_MUTANTS=66
EXPECTED_TEST_MUTANTS=27
EXPECTED_CONTRACT_MUTANTS=36
EXPECTED_QINIU_MUTANTS=3
EXPECTED_CONTRACT_TOTAL=36
DAWN="${DAWN_BIN:-}"
preflight_only=false

if [[ ${1:-} == --preflight-only ]]; then
  preflight_only=true
  shift
fi
if [[ $# -ne 0 ]]; then
  printf 'usage: %s [--preflight-only]\n' "$0" >&2
  exit 2
fi

if [[ -z "$DAWN" ]]; then
  DAWN="$("$ROOT/scripts/fetch-dawn.sh")"
fi

work="$(mktemp -d "${TMPDIR:-/tmp}/dawnop-fm-ancestor-mutants.XXXXXX")"
trap 'rm -rf "$work"' EXIT
started="$(date +%s)"

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

validate_contract_report() {
  python3 - "$1" "$2" "$3" <<'PY'
import json
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
owner = sys.argv[2]
expected_total = int(sys.argv[3])
if not path.is_file():
    raise SystemExit(f"missing FM contract machine report: {path}")
report = json.loads(path.read_text(encoding="utf-8"))
expected_failures = [owner] if owner else []
total = report.get("total")
checks = {
    "schema": report.get("schema") == "dawnop.fm-ancestor-contract.v1",
    "complete": report.get("complete") is True,
    "total": isinstance(total, int) and total > 0,
    "failures": report.get("failures") == expected_failures,
}
if expected_total:
    checks["base total"] = total == expected_total
wrong = [name for name, ok in checks.items() if not ok]
if wrong:
    raise SystemExit(f"invalid FM contract machine report fields: {', '.join(wrong)}")
print(total)
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

cat >"$work/contract-report.valid" <<'EOF'
{"complete":true,"failures":["owner"],"schema":"dawnop.fm-ancestor-contract.v1","total":2}
EOF
validate_contract_report "$work/contract-report.valid" owner 2 >/dev/null
cat >"$work/contract-report.collateral" <<'EOF'
{"complete":true,"failures":["owner","collateral"],"schema":"dawnop.fm-ancestor-contract.v1","total":2}
EOF
if validate_contract_report "$work/contract-report.collateral" owner 2 >/dev/null 2>&1; then
  printf 'FAIL  FM contract report parser accepted collateral failures\n' >&2
  exit 1
fi
printf 'PASS  FM contract report collateral negative control\n'

cat >"$work/qiniu-report.valid" <<'EOF'
{"bad":true,"complete":true,"diff":["owner"],"fatal":null,"missing":[],"name":"qiniu","new":[],"record":false,"schema":"dawnop.contract-golden.v1","skip_added":[],"skip_gone":[]}
EOF
validate_qiniu_report "$work/qiniu-report.valid" owner
cat >"$work/qiniu-report.collateral" <<'EOF'
{"bad":true,"complete":true,"diff":["owner"],"fatal":null,"missing":[],"name":"qiniu","new":["collateral"],"record":false,"schema":"dawnop.contract-golden.v1","skip_added":[],"skip_gone":[]}
EOF
if validate_qiniu_report "$work/qiniu-report.collateral" owner >/dev/null 2>&1; then
  printf 'FAIL  qiniu report parser accepted collateral cases\n' >&2
  exit 1
fi
cat >"$work/qiniu-report.incomplete" <<'EOF'
{"bad":true,"complete":false,"diff":["owner"],"fatal":null,"missing":[],"name":"qiniu","new":[],"record":false,"schema":"dawnop.contract-golden.v1","skip_added":[],"skip_gone":[]}
EOF
if validate_qiniu_report "$work/qiniu-report.incomplete" owner >/dev/null 2>&1; then
  printf 'FAIL  qiniu report parser accepted an incomplete run\n' >&2
  exit 1
fi
printf 'PASS  qiniu report completeness and collateral negative controls\n'

python3 "$MATRIX_CHECK" --self-test
python3 "$MUTATOR" --list >"$work/mutants.txt"
python3 "$CONTRACT" --list >"$work/assertions.txt"
python3 - "$QINIU_SOURCE" >"$work/qiniu-assertions.txt" <<'PY'
import ast
import pathlib
import sys

tree = ast.parse(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
owners = []
for node in ast.walk(tree):
    if not isinstance(node, ast.Call) or not node.args:
        continue
    if not (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "g"
        and node.func.attr == "case"
    ):
        continue
    owner = node.args[0]
    if isinstance(owner, ast.Constant) and isinstance(owner.value, str):
        owners.append(owner.value)
print("\n".join(sorted(owners)))
PY
python3 "$MATRIX_CHECK" \
  --matrix "$MATRIX" \
  --mutants "$work/mutants.txt" \
  --assertions "$work/assertions.txt" \
  --qiniu-assertions "$work/qiniu-assertions.txt"
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
if ! base_test_report="$(read_dawn_test_report "$base_test_log")"; then
  cat "$base_test_log" >&2
  exit 1
fi
read -r base_test_total base_test_failed <<<"$base_test_report"
if [[ "$base_test_failed" != 0 ]]; then
  printf 'FAIL  base Dawn test report is not green\n' >&2
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
base_contract_report="$work/base.contract.json"
if ! python3 "$CONTRACT" \
  --jar "$base_jar" \
  --port 18320 \
  --report "$base_contract_report" >"$base_contract_log" 2>&1; then
  cat "$base_contract_log" >&2
  exit 1
fi
if ! base_contract_total="$(validate_contract_report "$base_contract_report" "" "$EXPECTED_CONTRACT_TOTAL")"; then
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

test_mutants=0
contract_mutants=0
qiniu_mutants=0
for role in "${roles[@]}"; do
  case "$role" in
    test) test_mutants=$((test_mutants + 1)) ;;
    contract) contract_mutants=$((contract_mutants + 1)) ;;
    qiniu) qiniu_mutants=$((qiniu_mutants + 1)) ;;
    *)
      printf 'FAIL  matrix checker let unknown role through: %s\n' "$role" >&2
      exit 1
      ;;
  esac
done
if [[ ${#mutants[@]} -ne $EXPECTED_MUTANTS || \
      $test_mutants -ne $EXPECTED_TEST_MUTANTS || \
      $contract_mutants -ne $EXPECTED_CONTRACT_MUTANTS || \
      $qiniu_mutants -ne $EXPECTED_QINIU_MUTANTS ]]; then
  printf 'FAIL  matrix role counts drifted: %s total, %s test, %s contract, %s qiniu\n' \
    "${#mutants[@]}" "$test_mutants" "$contract_mutants" "$qiniu_mutants" >&2
  exit 1
fi
printf 'PASS  exact matrix role counts: %s total, %s test, %s contract, %s qiniu\n' \
  "${#mutants[@]}" "$test_mutants" "$contract_mutants" "$qiniu_mutants"

base_qiniu_log="$work/base.qiniu.log"
base_qiniu_report="$work/base.qiniu.json"
if ! CONTRACT_GOLDEN_REPORT="$base_qiniu_report" python3 "$QINIU_CONTRACT" \
  --jar "$base_jar" --only qiniu >"$base_qiniu_log" 2>&1; then
  cat "$base_qiniu_log" >&2
  exit 1
fi
if ! validate_qiniu_report "$base_qiniu_report" ""; then
  cat "$base_qiniu_log" >&2
  exit 1
fi
printf 'PASS  base qiniu contract\n'

if [[ "$preflight_only" == true ]]; then
  elapsed=$(( $(date +%s) - started ))
  printf 'PASS  FM ancestor mutation preflight in %ss\n' "$elapsed"
  exit 0
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

  mapfile -t test_failures < <(
    sed -n 's/^FAIL  //p' "$test_log" | sed 's/^.* :: //'
  )
  if [[ "$role" == test ]]; then
    if [[ $test_status -eq 0 || "$test_failed" != 1 || ${#test_failures[@]} -ne 1 || "${test_failures[0]}" != "$owner" ]]; then
      printf 'FAIL  %s produced the wrong Dawn red set\n' "$mutant" >&2
      cat "$test_log" >&2
      exit 1
    fi
    printf 'PASS  %s (%s) uniquely turns red: %s\n' "$mutant" "$role" "$owner"
    continue
  fi

  # HTTP and qiniu owners sit above the Dawn unit-test layer. They must leave
  # that lower layer green, then turn exactly one machine-reported case red.
  # Test-role mutants stop after their exact Dawn owner: a transaction primitive
  # deliberately affects its HTTP consumers, so requiring those higher-layer
  # contracts to stay green would confuse a real dependency with collateral.
  if [[ $test_status -ne 0 || "$test_failed" != 0 ]]; then
    printf 'FAIL  %s unexpectedly failed Dawn assertions\n' "$mutant" >&2
    cat "$test_log" >&2
    exit 1
  fi
  if [[ "$role" == qiniu ]]; then
    qiniu_log="$work/$mutant.qiniu.log"
    qiniu_report="$work/$mutant.qiniu.json"
    set +e
    CONTRACT_GOLDEN_REPORT="$qiniu_report" python3 "$QINIU_CONTRACT" \
      --jar "$jar" --only qiniu >"$qiniu_log" 2>&1
    qiniu_status=$?
    set -e
    if [[ $qiniu_status -eq 0 ]] || \
      ! validate_qiniu_report "$qiniu_report" "$owner"; then
      printf 'FAIL  %s produced the wrong qiniu red set\n' "$mutant" >&2
      cat "$test_log" >&2
      cat "$qiniu_log" >&2
      exit 1
    fi
    printf 'PASS  %s (%s) uniquely turns red: %s\n' "$mutant" "$role" "$owner"
    continue
  fi
  if [[ "$role" != contract ]]; then
    printf 'FAIL  matrix checker let unknown role through: %s\n' "$role" >&2
    exit 1
  fi
  contract_log="$work/$mutant.contract.log"
  contract_report="$work/$mutant.contract.json"
  port=$((18322 + index * 2))
  set +e
  python3 "$CONTRACT" \
    --jar "$jar" \
    --port "$port" \
    --report "$contract_report" >"$contract_log" 2>&1
  contract_status=$?
  set -e

  if [[ $contract_status -eq 0 ]] || \
    ! validate_contract_report "$contract_report" "$owner" "$base_contract_total" >/dev/null; then
    printf 'FAIL  %s produced the wrong HTTP red set\n' "$mutant" >&2
    cat "$test_log" >&2
    cat "$contract_log" >&2
    exit 1
  fi
  printf 'PASS  %s (%s) uniquely turns red: %s\n' "$mutant" "$role" "$owner"
done

elapsed=$(( $(date +%s) - started ))
printf 'PASS  FM ancestor mutation matrix: %s mutants in %ss\n' "${#mutants[@]}" "$elapsed"
