#!/usr/bin/env python3
"""Apply one compiling response stream boundary mutant in place."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def append_once(path: Path, declaration: str) -> None:
    text = path.read_text(encoding="utf-8")
    if declaration in text:
        raise ValueError(f"declaration already present in {path}")
    path.write_text(f"{text.rstrip()}\n\n{declaration}\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mutant",
        choices=(
            "bypass-interpolated-streaming",
            "bypass-owner-adapter",
            "bypass-streaming-symbol",
            "break-owner-adapter",
            "leak-java-input-stream",
            "leak-raw-input-stream",
            "make-response-stream-transparent",
        ),
    )
    parser.add_argument("project", type=Path)
    args = parser.parse_args()

    project = require_workdir(args.project)

    http = project / "src/util/http.dawn"
    api_fm = project / "src/api/api_fm.dawn"
    if args.mutant == "make-response-stream-transparent":
        replace_once(
            http,
            "pub opaque type ResponseStream = InputStream\n",
            "pub alias ResponseStream = InputStream\n",
        )
    elif args.mutant == "leak-java-input-stream":
        replace_once(
            api_fm,
            "use std/map\n",
            'use std/map\nuse java "java.io.InputStream"\n',
        )
        append_once(
            api_fm,
            "pub fn leaked_java_input_stream(s: InputStream) -> InputStream = s",
        )
    elif args.mutant == "leak-raw-input-stream":
        replace_once(
            http,
            "fn raw_response_stream(s: ResponseStream) -> InputStream = s\n",
            "fn raw_response_stream(s: ResponseStream) -> InputStream = s\n\n"
            "pub fn leaked_raw_response_stream(s: ResponseStream) -> InputStream = "
            "raw_response_stream(s)\n",
        )
    elif args.mutant == "break-owner-adapter":
        # web6 gave `streaming` a defaulted `length`; a function value does not
        # carry defaults, so calls through `forward` pass `None` explicitly.
        replace_once(
            http,
            "pub fn stream_response(status: Int, content_type: String, "
            "stream: ResponseStream) -> Response =\n"
            "  streaming(status, content_type, raw_response_stream(stream))\n",
            "pub fn stream_response(status: Int, content_type: String, "
            "stream: ResponseStream) -> Response = {\n"
            "  let forward = streaming\n"
            "  forward(status, content_type, raw_response_stream(stream), None)\n"
            "}\n",
        )
    elif args.mutant == "bypass-owner-adapter":
        replace_once(
            api_fm,
            "use db/sql.{DbConn}\n",
            "use db/sql.{DbConn}\nuse web/types as bypass_types # owner bypass alias\n",
        )
        # web5 made Response opaque, so the bypass reads the body seam through
        # the public accessor. web6 removed the sized constructor the web5 port
        # re-wrapped the raw stream with, which leaves `streaming` as the only
        # way to build a stream body, and another assertion owns that symbol.
        # So the bypass acts on what it read from the seam without rebuilding:
        # it lifts the sized stream's length into a Content-Length header,
        # framing the body behind the owner adapter's back.
        append_once(
            api_fm,
            "pub fn bypassed_owner_adapter(response: Response) -> Response =\n"
            "  match bypass_types.response_body(response) {\n"
            "    bypass_types.Stream(_, Some(length)) ->\n"
            '      bypass_types.with_header(response, "Content-Length", "${length}")\n'
            "    _ -> response\n"
            "  }",
        )
    elif args.mutant == "bypass-streaming-symbol":
        replace_once(
            api_fm,
            "use std/map\n",
            'use std/map\nuse java "java.net.URL"\n',
        )
        replace_once(
            api_fm,
            "use web/types.{Request, Response, HttpError, Handler, http_error, "
            "as_http, as_http_with, json_ok, query, redirect, header, body_text}\n",
            "use web/types.{Request, Response, HttpError, Handler, http_error, "
            "as_http, as_http_with, json_ok, query, redirect,\n"
            "  streaming, header, body_text}\n",
        )
        append_once(
            api_fm,
            "pub fn bypassed_owner_forward() -> Response !io = {\n"
            "  let forward = streaming\n"
            '  let raw_stream = URL.new("http://127.0.0.1").openStream()!\n'
            '  forward(200, "application/octet-stream", raw_stream, None)\n'
            "}",
        )
    else:
        replace_once(
            api_fm,
            "use std/map\n",
            'use std/map\nuse java "java.net.URL"\n',
        )
        replace_once(
            api_fm,
            "use db/sql.{DbConn}\n",
            "use db/sql.{DbConn}\nuse web/types as bypass_types\n",
        )
        append_once(
            api_fm,
            "pub fn interpolated_streaming_bypass() -> Response !io = {\n"
            '  let raw_stream = URL.new("http://127.0.0.1").openStream()!\n'
            '  let mime = "application/octet-stream"\n'
            '  let _rendered = "${return '
            'bypass_types.streaming(200, mime, raw_stream)}"\n'
            '  panic("interpolation did not return")\n'
            "}",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
