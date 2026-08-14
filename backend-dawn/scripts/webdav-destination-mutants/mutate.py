#!/usr/bin/env python3
"""Apply one compiling WebDAV Destination mutant in place."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mutant_workdir import require_workdir  # noqa: E402

MUTANTS = (
    "drop-dot-check",
    "drop-dotdot-check",
    "reject-dot-containing-names",
    "drop-https-scheme",
    "allow-unsupported-scheme",
    "compare-host-case-sensitively",
    "compare-ports-literally",
    "use-http-default-for-https",
    "allow-foreign-authority",
    "uncatch-host-uri",
    "host-absent-fail-open",
    "host-repeat-first-wins",
    "accept-non-simple-reference",
    "uncatch-destination-uri",
    "unwrap-opaque-raw-path",
    "ignore-destination-query",
    "ignore-destination-fragment",
    "destination-prefix-fail-open",
    "skip-unreserved-prefix-normalization",
    "restore-replacement-utf8",
    "accept-xml-forbidden-destination",
    "overwrite-invalid-fail-open",
    "move-depth-fail-open",
    "copy-invalid-depth-fail-open",
    "copy-collection-depth-zero-full-tree",
    "ignore-move-copy-body",
    "accept-file-parent",
)


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected one anchor in {path}, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    parser.add_argument("mutant", nargs="?")
    parser.add_argument("project", nargs="?", type=Path)
    args = parser.parse_args()

    if args.list:
        if args.mutant is not None or args.project is not None:
            parser.error("--list takes no positional arguments")
        print("\n".join(MUTANTS))
        return 0
    if args.mutant not in MUTANTS:
        parser.error(f"unknown mutant: {args.mutant}")
    if args.project is None:
        parser.error("project is required")

    project = require_workdir(args.project)

    source = project / "src/api/webdav.dawn"
    if args.mutant == "drop-dot-check":
        replace_once(source, '  s == "." || s == ".."\n', '  s == ".."\n')
    elif args.mutant == "drop-dotdot-check":
        replace_once(source, '  s == "." || s == ".."\n', '  s == "."\n')
    elif args.mutant == "reject-dot-containing-names":
        replace_once(
            source,
            '  s == "." || s == ".."\n',
            '  str.contains(s, ".")\n',
        )
    elif args.mutant == "drop-https-scheme":
        replace_once(
            source,
            '  scheme == "http" || scheme == "https"\n',
            '  scheme == "http"\n',
        )
    elif args.mutant == "allow-unsupported-scheme":
        replace_once(
            source,
            '  scheme == "http" || scheme == "https"\n',
            '  scheme == "http" || scheme == "https" || scheme == "ftp"\n',
        )
    elif args.mutant == "compare-host-case-sensitively":
        replace_once(
            source,
            "                str.to_lower(host_name) == str.to_lower(destination_host) &&\n",
            "                host_name == destination_host &&\n",
        )
    elif args.mutant == "compare-ports-literally":
        replace_once(
            source,
            "                effective_port(scheme, host_uri.getPort()) == effective_port(scheme, uri.getPort())\n",
            "                host_uri.getPort() == uri.getPort()\n",
        )
    elif args.mutant == "use-http-default-for-https":
        replace_once(
            source,
            '  if scheme == "http" { 80 } else if scheme == "https" { 443 } else { -1 }\n',
            '  if scheme == "http" { 80 } else if scheme == "https" { 80 } else { -1 }\n',
        )
    elif args.mutant == "allow-foreign-authority":
        replace_once(
            source,
            "                str.to_lower(host_name) == str.to_lower(destination_host) &&\n",
            "                true &&\n",
        )
    elif args.mutant == "uncatch-host-uri":
        replace_once(
            source,
            "fn parse_destination_host_uri(scheme: String, raw_host: String) -> Option[URI] !io =\n"
            '  match catch_text(() => URI.create("$scheme://$raw_host")!) {\n'
            "    Ok(uri) -> Some(uri)\n"
            "    Err(_) -> None\n"
            "  }\n",
            "fn parse_destination_host_uri(scheme: String, raw_host: String) -> Option[URI] !io =\n"
            '  Some(URI.create("$scheme://$raw_host")!)\n',
        )
    elif args.mutant == "host-absent-fail-open":
        replace_once(
            source,
            "  if len(given) == 0 {\n"
            '    Err(http_error(400, "缺少 Host 头，无法判断 Destination 是否属于当前服务器"))\n',
            "  if len(given) == 0 {\n    Ok(())\n",
        )
    elif args.mutant == "host-repeat-first-wins":
        replace_once(
            source,
            "  } else if len(given) > 1 {\n"
            '    Err(http_error(400, "Host 头只能出现一次"))\n',
            "  } else if len(given) > 2 {\n"
            '    Err(http_error(400, "Host 头只能出现一次"))\n',
        )
    elif args.mutant == "accept-non-simple-reference":
        replace_once(
            source,
            "fn is_path_absolute_reference(raw: String, uri: URI) -> Bool !io =\n"
            '  str.starts_with(raw, "/") &&\n'
            '  not str.starts_with(raw, "//") &&\n'
            "  no_text_part(uri.getRawAuthority())\n",
            "fn is_path_absolute_reference(raw: String, uri: URI) -> Bool !io =\n"
            "  true\n",
        )
    elif args.mutant == "uncatch-destination-uri":
        replace_once(
            source,
            "fn parse_destination_uri(raw: String) -> Result[URI, HttpError] !io =\n"
            "  match catch_text(() => URI.create(raw)!) {\n"
            "    Ok(uri) -> Ok(uri)\n"
            '    Err(_) -> Err(http_error(400, "非法目标"))\n'
            "  }\n",
            "fn parse_destination_uri(raw: String) -> Result[URI, HttpError] !io =\n"
            "  Ok(URI.create(raw)!)\n",
        )
    elif args.mutant == "unwrap-opaque-raw-path":
        replace_once(
            source,
            "fn require_hierarchical_destination(uri: URI) -> Result[Unit, HttpError] !io =\n"
            "  if uri.isOpaque() {\n"
            '    Err(http_error(400, "Destination 必须是分层 URI"))\n'
            "  } else {\n"
            "    Ok(())\n"
            "  }\n",
            "fn require_hierarchical_destination(uri: URI) -> Result[Unit, HttpError] !io = {\n"
            "  let _path = uri.getRawPath()!\n"
            "  Ok(())\n"
            "}\n",
        )
    elif args.mutant == "ignore-destination-query":
        replace_once(
            source,
            "fn reject_destination_query(uri: URI) -> Result[Unit, HttpError] !io =\n"
            "  match uri.getRawQuery() {\n"
            "    None -> Ok(())\n"
            '    Some(_) -> Err(http_error(400, "Destination 不支持查询参数"))\n'
            "  }\n",
            "fn reject_destination_query(uri: URI) -> Result[Unit, HttpError] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "ignore-destination-fragment":
        replace_once(
            source,
            "fn reject_destination_fragment(uri: URI) -> Result[Unit, HttpError] !io =\n"
            "  match uri.getRawFragment() {\n"
            "    None -> Ok(())\n"
            '    Some(_) -> Err(http_error(400, "Destination 不支持片段"))\n'
            "  }\n",
            "fn reject_destination_fragment(uri: URI) -> Result[Unit, HttpError] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "destination-prefix-fail-open":
        replace_once(
            source,
            '    Err(http_error(400, "Destination 不在 WebDAV 根下"))\n',
            "    Ok(path)\n",
        )
    elif args.mutant == "skip-unreserved-prefix-normalization":
        replace_once(
            source,
            "  let normalized_path = normalize_destination_unreserved(path)\n"
            "  let normalized_prefix = normalize_destination_unreserved(prefix)\n",
            "  let normalized_path = path\n  let normalized_prefix = prefix\n",
        )
    elif args.mutant == "restore-replacement-utf8":
        replace_once(
            source,
            "  if not valid_percent_utf8(s) {\n",
            "  if false {\n",
        )
    elif args.mutant == "accept-xml-forbidden-destination":
        replace_once(
            source,
            "        if xml10_text_allowed(decoded) {\n",
            "        if true {\n",
        )
    elif args.mutant == "overwrite-invalid-fail-open":
        replace_once(
            source,
            '        _ -> Err(http_error(400, "Overwrite 只支持 T 或 F"))\n',
            "        _ -> Ok(true)\n",
        )
    elif args.mutant == "move-depth-fail-open":
        replace_once(
            source,
            '      if str.to_lower(raw) == "infinity" {\n',
            "      if true {\n",
        )
    elif args.mutant == "copy-invalid-depth-fail-open":
        replace_once(
            source,
            '        Err(http_error(400, "COPY Depth 只支持 0 或 infinity"))\n',
            "        Ok(())\n",
        )
    elif args.mutant == "copy-collection-depth-zero-full-tree":
        replace_once(
            source,
            '        Err(http_error(501, "暂不支持集合的 Depth: 0 COPY"))\n',
            "        Ok(())\n",
        )
    elif args.mutant == "ignore-move-copy-body":
        replace_once(
            source,
            "  if bytes.len(req.raw) == 0 {\n",
            "  if true {\n",
        )
    elif args.mutant == "accept-file-parent":
        replace_once(
            source,
            "      Some(o) -> o.is_dir\n",
            "      Some(_) -> true\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
