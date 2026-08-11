#!/usr/bin/env python3
"""Enforce selected Dawn Java owners and web response stream seams."""

import argparse
import re
from pathlib import Path

HTTP_ASSERTION = "ffi.java-net-http-confined"
HTTP_OWNER = Path("src/util/http.dawn")
JAVA_NET_HTTP = re.compile(r'^\s*use java "java\.net\.http(?:\.|\")')
SQL_ASSERTION = "ffi.java-sql-confined"
SQL_OWNER = Path("src/db/sql.dawn")
JAVA_SQL = re.compile(r'^\s*use java "java\.sql(?:\.|\")')
OPAQUE_ASSERTION = "ffi.db-conn-opaque"
OPAQUE_DECLARATION = "pub opaque type DbConn = Connection"
RAW_ASSERTION = "ffi.db-conn-raw-confined"
CONNECTION_TOKEN = re.compile(r"\bConnection\b")
CONNECTION_IMPORT = 'use java "java.sql.Connection"'
DB_CONN_REPRESENTATION = re.compile(
    r"^(?:pub opaque type|pub alias) DbConn = Connection$"
)
RAW_DECLARATION = "fn raw(c: DbConn) -> Connection = c"
INPUT_STREAM_TOKEN_ASSERTION = "ffi.java-input-stream-explicit-token-confined"
INPUT_STREAM_OWNER = Path("src/util/http.dawn")
INPUT_STREAM_IDENTIFIER = "InputStream"
INPUT_STREAM_IMPORT_PATTERN = re.compile(
    r'^\s*use\s+java\s+"java\.io\.InputStream"\s*(?:#.*)?$'
)
RESPONSE_STREAM_OPAQUE_ASSERTION = "ffi.response-stream-opaque"
RESPONSE_STREAM_OPAQUE_DECLARATION = "pub opaque type ResponseStream = InputStream"
RESPONSE_STREAM_RAW_ASSERTION = "ffi.response-stream-raw-confined"
INPUT_STREAM_IMPORT = 'use java "java.io.InputStream"'
RESPONSE_STREAM_REPRESENTATION = re.compile(
    r"^(?:pub opaque type|pub alias) ResponseStream = InputStream$"
)
RESPONSE_STREAM_CAST = "let got: Result[InputStream, ForeignError] = cast(resp.body()!)"
RAW_RESPONSE_STREAM_DECLARATION = (
    "fn raw_response_stream(s: ResponseStream) -> InputStream = s"
)
RESPONSE_STREAM_ADAPTER_ASSERTION = "ffi.response-stream-owner-adapter"
WEB_STREAM_IMPORT = "use web/types.{Response, streaming}"
STREAM_RESPONSE_DECLARATION = (
    "pub fn stream_response(status: Int, content_type: String, "
    "stream: ResponseStream) -> Response ="
)
STREAM_RESPONSE_FORWARD = "streaming(status, content_type, raw_response_stream(stream))"
RESPONSE_BODY_SEAM_ASSERTION = "ffi.web3-response-body-seam-confined"
RESPONSE_BODY_SEAM_IDENTIFIERS = frozenset({"Stream", "ResponseBody"})
STREAMING_SYMBOL_ASSERTION = "ffi.web3-streaming-symbol-confined"
STREAMING_SYMBOL_IDENTIFIERS = frozenset({"streaming"})
TOKENIZER_SELF_TEST_ASSERTION = "ffi.dawn-code-tokenizer-self-test"


def import_violations(
    root: Path, owner: Path, pattern: re.Pattern[str]
) -> list[tuple[Path, int, str]]:
    found: list[tuple[Path, int, str]] = []
    for source in sorted((root / "src").rglob("*.dawn")):
        relative = source.relative_to(root)
        if relative == owner:
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.match(line):
                found.append((relative, line_number, line.strip()))
    return found


def is_identifier_start(character: str) -> bool:
    return character == "_" or character.isalpha()


def is_identifier_part(character: str) -> bool:
    return character == "_" or character.isalnum()


class DawnCodeTokenizer:
    """Tokenize Dawn code, including code inside string interpolation."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens: list[tuple[str, int]] = []
        self.line_numbers = self._build_line_numbers()

    def tokenize(self) -> list[tuple[str, int]]:
        self._scan_code(0, len(self.text))
        return self.tokens

    def _build_line_numbers(self) -> list[int]:
        line_numbers: list[int] = []
        line_number = 1
        for character in self.text:
            line_numbers.append(line_number)
            if character == "\n":
                line_number += 1
        line_numbers.append(line_number)
        return line_numbers

    def _line_at(self, index: int) -> int:
        return self.line_numbers[min(index, len(self.text))]

    def _scan_code(self, start: int, end: int) -> None:
        index = start
        while index < end:
            character = self.text[index]
            if character.isspace():
                index += 1
                continue
            if character == "#":
                while index < end and self.text[index] != "\n":
                    index += 1
                continue
            if character == '"':
                index = self._scan_string(index, end, emit_interpolations=True)
                continue
            if character == "`":
                index = self._skip_raw_string(index, end)
                continue
            if character == "'":
                index = self._skip_char_literal(index, end)
                continue
            if is_identifier_start(character):
                token_start = index
                index += 1
                while index < end and is_identifier_part(self.text[index]):
                    index += 1
                self.tokens.append(
                    (self.text[token_start:index], self._line_at(token_start))
                )
                continue
            self.tokens.append((character, self._line_at(index)))
            index += 1

    def _scan_string(self, start: int, end: int, *, emit_interpolations: bool) -> int:
        triple = self.text.startswith('"""', start)
        index = start + (3 if triple else 1)
        while index < end:
            if triple and self.text.startswith('"""', index):
                return index + 3
            character = self.text[index]
            if not triple and character == '"':
                return index + 1
            if not triple and character == "\n":
                raise ValueError(
                    f"unterminated ordinary string at line {self._line_at(start)}"
                )
            if character == "\\":
                index = self._skip_escape(index, end)
                continue
            if character != "$":
                index += 1
                continue

            next_index = index + 1
            if next_index < end and self.text[next_index] == "{":
                close = self._find_interpolation_end(index, end)
                if emit_interpolations:
                    self._scan_code(index + 2, close)
                index = close + 1
                continue
            if next_index < end and is_identifier_start(self.text[next_index]):
                name_end = next_index + 1
                while name_end < end and is_identifier_part(self.text[name_end]):
                    name_end += 1
                if emit_interpolations:
                    self.tokens.append(
                        (
                            self.text[next_index:name_end],
                            self._line_at(next_index),
                        )
                    )
                index = name_end
                continue
            index += 1

        kind = "triple" if triple else "ordinary"
        raise ValueError(f"unterminated {kind} string at line {self._line_at(start)}")

    def _find_interpolation_end(self, dollar: int, end: int) -> int:
        index = dollar + 2
        depth = 1
        while index < end:
            character = self.text[index]
            if character == "{":
                depth += 1
                index += 1
                continue
            if character == "}":
                depth -= 1
                if depth == 0:
                    return index
                index += 1
                continue
            if character == '"':
                index = self._scan_string(index, end, emit_interpolations=False)
                continue
            if character == "`":
                index = self._skip_raw_string(index, end)
                continue
            if character == "'":
                index = self._skip_char_literal(index, end)
                continue
            if character == "\n":
                raise ValueError(
                    f"interpolation cannot span lines at line {self._line_at(dollar)}"
                )
            index += 1
        raise ValueError(f"unmatched interpolation at line {self._line_at(dollar)}")

    def _skip_escape(self, start: int, end: int) -> int:
        escaped = start + 1
        if escaped >= end:
            raise ValueError(f"unterminated escape at line {self._line_at(start)}")
        if (
            self.text[escaped] == "u"
            and escaped + 1 < end
            and self.text[escaped + 1] == "{"
        ):
            close = self.text.find("}", escaped + 2, end)
            if close == -1:
                raise ValueError(
                    f"unterminated unicode escape at line {self._line_at(start)}"
                )
            return close + 1
        return escaped + 1

    def _skip_raw_string(self, start: int, end: int) -> int:
        close = self.text.find("`", start + 1, end)
        if close == -1:
            raise ValueError(f"unterminated raw string at line {self._line_at(start)}")
        return close + 1

    def _skip_char_literal(self, start: int, end: int) -> int:
        index = start + 1
        if index >= end or self.text[index] == "\n":
            raise ValueError(
                f"unterminated char literal at line {self._line_at(start)}"
            )
        if self.text[index] == "\\":
            index = self._skip_escape(index, end)
        else:
            index += 1
        if index >= end or self.text[index] != "'":
            raise ValueError(f"invalid char literal at line {self._line_at(start)}")
        return index + 1


def dawn_code_tokens(text: str) -> list[tuple[str, int]]:
    """Ignore string text and recursively tokenize `$name` and `${...}` code."""
    return DawnCodeTokenizer(text).tokenize()


def token_values(text: str) -> list[str]:
    return [token for token, _ in dawn_code_tokens(text)]


def token_sequence_count(tokens: list[str], snippet: str) -> int:
    expected = token_values(snippet)
    width = len(expected)
    if width == 0:
        return 0
    return sum(
        tokens[index : index + width] == expected
        for index in range(len(tokens) - width + 1)
    )


def code_token_violations(
    root: Path, owner: Path, identifiers: frozenset[str]
) -> list[tuple[Path, int, str, str]]:
    found: list[tuple[Path, int, str, str]] = []
    for source in sorted((root / "src").rglob("*.dawn")):
        relative = source.relative_to(root)
        if relative == owner:
            continue
        text = source.read_text(encoding="utf-8")
        lines = text.splitlines()
        seen: set[tuple[int, str]] = set()
        for token, line_number in dawn_code_tokens(text):
            key = (line_number, token)
            if token not in identifiers or key in seen:
                continue
            seen.add(key)
            line = lines[line_number - 1].strip() if line_number <= len(lines) else ""
            found.append((relative, line_number, token, line))
    return found


def explicit_input_stream_violations(
    root: Path, owner: Path
) -> list[tuple[Path, int, str, str]]:
    found = code_token_violations(root, owner, frozenset({INPUT_STREAM_IDENTIFIER}))
    for source in sorted((root / "src").rglob("*.dawn")):
        relative = source.relative_to(root)
        if relative == owner:
            continue
        for line_number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if INPUT_STREAM_IMPORT_PATTERN.fullmatch(line):
                found.append(
                    (relative, line_number, INPUT_STREAM_IDENTIFIER, line.strip())
                )
    return sorted(found, key=lambda item: (str(item[0]), item[1], item[2]))


def report_import_assertion(
    assertion: str, violations: list[tuple[Path, int, str]]
) -> bool:
    if not violations:
        print(f"PASS  {assertion}")
        return True

    print(f"FAIL  {assertion}")
    for path, line_number, line in violations:
        print(f"  {path}:{line_number}: {line}")
    return False


def report_named_source_assertion(
    assertion: str,
    violations: list[tuple[Path, int, str, str]],
    description: str,
) -> bool:
    if not violations:
        print(f"PASS  {assertion}")
        return True

    print(f"FAIL  {assertion}")
    for path, line_number, token, line in violations:
        print(f"  {path}:{line_number}: unapproved {description} `{token}`: {line}")
    return False


def report_opaque_assertion(root: Path) -> bool:
    owner = root / SQL_OWNER
    matches = [
        line
        for line in owner.read_text(encoding="utf-8").splitlines()
        if line.strip() == OPAQUE_DECLARATION
    ]
    if len(matches) == 1:
        print(f"PASS  {OPAQUE_ASSERTION}")
        return True

    print(f"FAIL  {OPAQUE_ASSERTION}")
    print(
        f"  {SQL_OWNER}: expected exactly `{OPAQUE_DECLARATION}`, found {len(matches)}"
    )
    return False


def connection_seam(line: str) -> str | None:
    stripped = line.strip()
    if stripped == CONNECTION_IMPORT:
        return "java.sql import"
    if DB_CONN_REPRESENTATION.fullmatch(stripped):
        return "DbConn representation"
    if stripped == RAW_DECLARATION:
        return "private raw accessor"
    return None


def report_raw_connection_assertion(root: Path) -> bool:
    owner = root / SQL_OWNER
    seen = {
        "java.sql import": 0,
        "DbConn representation": 0,
        "private raw accessor": 0,
    }
    violations: list[tuple[int, str]] = []
    for line_number, line in enumerate(
        owner.read_text(encoding="utf-8").splitlines(), start=1
    ):
        matches = list(CONNECTION_TOKEN.finditer(line))
        if not matches:
            continue
        seam = connection_seam(line)
        if seam is None or len(matches) != 1:
            violations.append((line_number, line.strip()))
            continue
        seen[seam] += 1

    missing_or_duplicate = [name for name, count in seen.items() if count != 1]
    if not violations and not missing_or_duplicate:
        print(f"PASS  {RAW_ASSERTION}")
        return True

    print(f"FAIL  {RAW_ASSERTION}")
    for line_number, line in violations:
        print(f"  {SQL_OWNER}:{line_number}: unapproved Connection token: {line}")
    for name in missing_or_duplicate:
        print(f"  {SQL_OWNER}: expected exactly one {name}, found {seen[name]}")
    return False


def report_response_stream_opaque_assertion(root: Path) -> bool:
    owner = root / INPUT_STREAM_OWNER
    matches = [
        line
        for line in owner.read_text(encoding="utf-8").splitlines()
        if line.strip() == RESPONSE_STREAM_OPAQUE_DECLARATION
    ]
    if len(matches) == 1:
        print(f"PASS  {RESPONSE_STREAM_OPAQUE_ASSERTION}")
        return True

    print(f"FAIL  {RESPONSE_STREAM_OPAQUE_ASSERTION}")
    print(
        f"  {INPUT_STREAM_OWNER}: expected exactly "
        f"`{RESPONSE_STREAM_OPAQUE_DECLARATION}`, found {len(matches)}"
    )
    return False


def response_stream_seam(tokens: list[str]) -> str | None:
    representation = " ".join(tokens)
    if RESPONSE_STREAM_REPRESENTATION.fullmatch(representation):
        return "ResponseStream representation"
    if tokens == token_values(RESPONSE_STREAM_CAST):
        return "response body cast"
    if tokens == token_values(RAW_RESPONSE_STREAM_DECLARATION):
        return "private raw accessor"
    return None


def report_raw_response_stream_assertion(root: Path) -> bool:
    owner = root / INPUT_STREAM_OWNER
    text = owner.read_text(encoding="utf-8")
    lines = text.splitlines()
    seen = {
        "java.io import": sum(
            INPUT_STREAM_IMPORT_PATTERN.fullmatch(line) is not None for line in lines
        ),
        "ResponseStream representation": 0,
        "response body cast": 0,
        "private raw accessor": 0,
    }
    violations: list[tuple[int, str]] = []
    tokens_by_line: dict[int, list[str]] = {}
    for token, line_number in dawn_code_tokens(text):
        tokens_by_line.setdefault(line_number, []).append(token)
    for line_number, tokens in tokens_by_line.items():
        input_stream_count = tokens.count(INPUT_STREAM_IDENTIFIER)
        if input_stream_count == 0:
            continue
        seam = response_stream_seam(tokens)
        if seam is None or input_stream_count != 1:
            violations.append((line_number, lines[line_number - 1].strip()))
            continue
        seen[seam] += 1

    missing_or_duplicate = [name for name, count in seen.items() if count != 1]
    if not violations and not missing_or_duplicate:
        print(f"PASS  {RESPONSE_STREAM_RAW_ASSERTION}")
        return True

    print(f"FAIL  {RESPONSE_STREAM_RAW_ASSERTION}")
    for line_number, line in violations:
        print(
            f"  {INPUT_STREAM_OWNER}:{line_number}: "
            f"unapproved InputStream token: {line}"
        )
    for name in missing_or_duplicate:
        print(
            f"  {INPUT_STREAM_OWNER}: expected exactly one {name}, found {seen[name]}"
        )
    return False


def report_response_stream_adapter_assertion(root: Path) -> bool:
    owner = root / INPUT_STREAM_OWNER
    owner_tokens = token_values(owner.read_text(encoding="utf-8"))
    required = {
        "web3 streaming import": WEB_STREAM_IMPORT,
        "owner adapter declaration": STREAM_RESPONSE_DECLARATION,
        "owner adapter forward": STREAM_RESPONSE_FORWARD,
    }
    bad_counts = {
        name: token_sequence_count(owner_tokens, expected)
        for name, expected in required.items()
    }
    bad_counts = {name: count for name, count in bad_counts.items() if count != 1}

    if not bad_counts:
        print(f"PASS  {RESPONSE_STREAM_ADAPTER_ASSERTION}")
        return True

    print(f"FAIL  {RESPONSE_STREAM_ADAPTER_ASSERTION}")
    for name, count in bad_counts.items():
        print(f"  {INPUT_STREAM_OWNER}: expected exactly one {name}, found {count}")
    return False


def report_tokenizer_self_test(root: Path) -> bool:
    failures: list[str] = []

    def check_probe(
        label: str,
        source: str,
        identifiers: frozenset[str],
        expected: list[tuple[str, int]],
    ) -> None:
        try:
            actual = [
                (token, line_number)
                for token, line_number in dawn_code_tokens(source)
                if token in identifiers
            ]
        except ValueError as error:
            failures.append(f"{label} raised: {error}")
            return
        if actual != expected:
            failures.append(f"{label}: expected {expected}, found {actual}")

    all_seams = RESPONSE_BODY_SEAM_IDENTIFIERS | STREAMING_SYMBOL_IDENTIFIERS
    check_probe(
        "multiline selective import",
        "use web/types.{\n  Response,\n  Stream,\n  ResponseBody,\n}\n",
        RESPONSE_BODY_SEAM_IDENTIFIERS,
        [("Stream", 3), ("ResponseBody", 4)],
    )
    check_probe(
        "trailing comment alias",
        "use web/types as bypass_types # Stream ResponseBody streaming\n"
        "fn rebuild(raw) = bypass_types.Stream(raw)\n",
        all_seams,
        [("Stream", 2)],
    )
    check_probe(
        "first class streaming symbol",
        "use web/types.{Response,\n"
        "  streaming}\n"
        "fn forwarder() = {\n"
        "  let forward = streaming\n"
        "  forward\n"
        "}\n",
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 2), ("streaming", 4)],
    )
    check_probe(
        "plain text comments and literal forms",
        "# streaming Stream ResponseBody\n"
        'const NOTE = "streaming Stream ResponseBody \\"quoted\\""\n'
        'const BLOCK = """streaming\nStream ResponseBody"""\n'
        "const RAW = `streaming $streaming ${alias.streaming(raw)}`\n"
        "const CHAR: Char = 's'\n",
        all_seams,
        [],
    )
    check_probe(
        "simple identifier interpolation",
        'const VALUE = "prefix $streaming suffix"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "brace return interpolation",
        'const VALUE = "${return alias.streaming(200, mime, raw_stream)}"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "comment inside interpolation",
        'const VALUE = "${safe # streaming}"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [],
    )
    check_probe(
        "ordinary string inside interpolation",
        r'const VALUE = "${pick("streaming \" }", alias.streaming(raw))}"'
        "\n",
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "triple string inside interpolation",
        'const VALUE = "${pick("""streaming }""", alias.streaming(raw))}"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "raw string inside interpolation",
        'const VALUE = "${pick(`streaming }`, alias.streaming(raw))}"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "char literal inside interpolation",
        "const VALUE = \"${pick('}', alias.streaming(raw))}\"\n",
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "nested braces inside interpolation",
        'const VALUE = "${pick({ outer: { label: "streaming }" } }, '
        'alias.streaming(raw))}"\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 1)],
    )
    check_probe(
        "triple string interpolation line number",
        'const VALUE = """\nplain\n$streaming\n"""\n',
        STREAMING_SYMBOL_IDENTIFIERS,
        [("streaming", 3)],
    )
    check_probe(
        "response body interpolation identifiers",
        'const VALUE = "$Stream and $ResponseBody"\n',
        RESPONSE_BODY_SEAM_IDENTIFIERS,
        [("Stream", 1), ("ResponseBody", 1)],
    )

    try:
        auth_text = (root / "src/svc/auth.dawn").read_text(encoding="utf-8")
        auth_tokens = token_values(auth_text)
        auth_dangerous = [token for token in auth_tokens if token in all_seams]
        if (
            token_sequence_count(auth_tokens, "use web/types as wtypes") != 1
            or token_sequence_count(auth_tokens, "wtypes.query") < 1
            or auth_dangerous
        ):
            failures.append("svc/auth web/types alias probe was not accepted")
    except (OSError, ValueError) as error:
        failures.append(f"svc/auth web/types alias probe raised: {error}")

    if not failures:
        print(f"PASS  {TOKENIZER_SELF_TEST_ASSERTION}")
        return True

    print(f"FAIL  {TOKENIZER_SELF_TEST_ASSERTION}")
    for failure in failures:
        print(f"  {failure}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="backend-dawn project root",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    results = [
        report_import_assertion(
            HTTP_ASSERTION,
            import_violations(root, HTTP_OWNER, JAVA_NET_HTTP),
        ),
        report_import_assertion(
            SQL_ASSERTION,
            import_violations(root, SQL_OWNER, JAVA_SQL),
        ),
        report_opaque_assertion(root),
        report_raw_connection_assertion(root),
        report_named_source_assertion(
            INPUT_STREAM_TOKEN_ASSERTION,
            explicit_input_stream_violations(root, INPUT_STREAM_OWNER),
            "explicit InputStream source name",
        ),
        report_response_stream_opaque_assertion(root),
        report_raw_response_stream_assertion(root),
        report_response_stream_adapter_assertion(root),
        report_named_source_assertion(
            RESPONSE_BODY_SEAM_ASSERTION,
            code_token_violations(
                root, INPUT_STREAM_OWNER, RESPONSE_BODY_SEAM_IDENTIFIERS
            ),
            "web3 response body seam code token including string interpolation",
        ),
        report_named_source_assertion(
            STREAMING_SYMBOL_ASSERTION,
            code_token_violations(
                root, INPUT_STREAM_OWNER, STREAMING_SYMBOL_IDENTIFIERS
            ),
            "web3 streaming symbol code token including string interpolation",
        ),
        report_tokenizer_self_test(root),
    ]
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
