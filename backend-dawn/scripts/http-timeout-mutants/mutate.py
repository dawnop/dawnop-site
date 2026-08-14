#!/usr/bin/env python3
"""Apply one compiling src/util/http.dawn mutant in place.

The harness started as the outbound-deadline negative control and now covers the
rest of that module's outbound contract too: how a timeout is classified apart
from a refusal, and whether the client is really shared. All three are things
the source reads as already true -- there is a `.timeout(`, the message says
"timed out", the function is called shared -- and only running proves.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402

MUTANTS = (
    "client-per-call",
    "collapse-transfer-tier",
    "drop-deadline",
    "edge-times-out-as-502",
    "inflate-deadline",
    "mute-timeout-text",
    "timeout-reads-as-upstream",
    "timeout-tag-second-opinion",
    "transfer-tier-on-management",
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mutant", choices=MUTANTS)
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)

    http = project / "src/util/http.dawn"

    if args.mutant == "client-per-call":
        # "shared" goes back to being only a name: every call builds its own
        # client, so every call starts with an empty connection pool. Nothing
        # about the source gives this away -- there is still exactly one
        # builder expression in the file and main still calls new_client once.
        replace_once(
            http,
            "fn client_of(cell: HttpCell) -> HttpClient = cell\n",
            "fn client_of(_cell: HttpCell) -> HttpClient !io =\n"
            "  HttpClient.newBuilder()!.connectTimeout("
            "Duration.ofSeconds(CONNECT_TIMEOUT_S)!)!.build()!\n",
        )
    elif args.mutant == "timeout-reads-as-upstream":
        # The state this task found the code in: a peer that never answered and
        # a peer that refused us arrive at the edge as the same kind, so both
        # answer 502.
        replace_once(
            http,
            "  if timed_out(m) { timeout(m) } else { upstream(m) }\n",
            "  upstream(m)\n",
        )
    elif args.mutant == "timeout-tag-second-opinion":
        # The classifier stops reading deadline_text's verdict and forms its
        # own, off the exception class name -- which is exactly the plausible
        # second predicate that misses HttpConnectTimeoutException. The wording
        # and the status code then disagree for connect timeouts only.
        replace_once(
            http,
            "pub fn timed_out(m: String) -> Bool = str.starts_with(m, TIMEOUT_PREFIX)\n",
            'pub fn timed_out(m: String) -> Bool = str.contains(m, "HttpTimeoutException")\n',
        )
    elif args.mutant == "edge-times-out-as-502":
        # One rule, both edges: a timeout collapses back into 502 on the wire.
        replace_once(
            project / "src/api/api_fm.dawn",
            "    KTimeout -> 504\n",
            "    KTimeout -> 502\n",
        )
        replace_once(
            project / "src/api/webdav.dawn",
            "    KTimeout -> http_error(504, t)\n",
            "    KTimeout -> http_error(502, t)\n",
        )
    elif args.mutant == "drop-deadline":
        # No deadline at all: the state this task found the code in.
        replace_once(
            http,
            "  let timed = base.timeout(Duration.ofSeconds(budget_s)!)!\n",
            "  let timed = base\n",
        )
    elif args.mutant == "inflate-deadline":
        # A deadline that is present but useless. `.timeout(` still appears in
        # the source, so any assertion that greps for the call survives this;
        # only actually timing a stalled peer catches it.
        replace_once(
            http,
            "  let timed = base.timeout(Duration.ofSeconds(budget_s)!)!\n",
            "  let timed = base.timeout(Duration.ofSeconds(86400)!)!\n",
        )
    elif args.mutant == "transfer-tier-on-management":
        # Deadline present and sane, but the small management calls inherit the
        # transfer ceiling, so gc's delete_obj could hold the SQLite write lock
        # for an hour instead of ten seconds.
        replace_once(
            http,
            "  send_body(cl, method, url, headers, publisher, MANAGEMENT_TIMEOUT_S)\n",
            "  send_body(cl, method, url, headers, publisher, TRANSFER_TIMEOUT_S)\n",
        )
    elif args.mutant == "collapse-transfer-tier":
        # The two tiers stop being two tiers, capping every upload and download
        # at the management ceiling.
        replace_once(
            http,
            "const TRANSFER_TIMEOUT_S: Int = 3600\n",
            "const TRANSFER_TIMEOUT_S: Int = 10\n",
        )
    else:
        # A timeout stops announcing itself and reads as a generic fault again.
        # It also stops being classified as one: `timed_out` reads the verdict
        # off this text, so muting the text mutes the 504 as well -- which is
        # the single-judgement design working, not a second defect.
        replace_once(
            http,
            '    "$TIMEOUT_PREFIX${to_string(budget_s)}s: ${fe_text(e)}"\n',
            "    fe_text(e)\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
