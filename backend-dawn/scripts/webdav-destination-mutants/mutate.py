#!/usr/bin/env python3
"""Apply one compiling WebDAV Destination mutant in place."""

import argparse
from pathlib import Path

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
    "accept-non-simple-reference",
    "uncatch-destination-uri",
    "unwrap-opaque-raw-path",
    "ignore-destination-query",
    "ignore-destination-fragment",
    "destination-prefix-fail-open",
    "skip-unreserved-prefix-normalization",
    "restore-replacement-utf8",
    "purge-before-parent-check",
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

    source = args.project / "src/api/webdav.dawn"
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
    elif args.mutant == "purge-before-parent-check":
        replace_once(
            source,
            "        } else {\n"
            "          let _v = as_http_with(with_db(a.db.path, a.db.ext, c => validate_webdav_subtree_destination(c, rel, dst)), conflict_or(500))?\n"
            "          mc_require_parent(a, rel, dst, is_move, true)\n"
            "        }\n",
            "        } else {\n"
            "          mc_require_parent(a, rel, dst, is_move, true)\n"
            "        }\n",
        )
        replace_once(
            source,
            "fn mc_require_parent(a: Auth, rel: String, dst: String, is_move: Bool, dst_existed: Bool) -> Result[Response, HttpError] !io =\n"
            "  {\n"
            "    let ok = as_http(parent_exists(a, dst), 500)?\n"
            "    if not ok {\n"
            '      Err(http_error(409, "父目录不存在"))\n'
            "    } else if dst_existed {\n"
            "      mc_purge_then(a, rel, dst, is_move, true)\n"
            "    } else {\n"
            "      mc_apply(a, rel, dst, is_move, false)\n"
            "    }\n"
            "  }\n",
            "fn mc_require_parent(a: Auth, rel: String, dst: String, is_move: Bool, dst_existed: Bool) -> Result[Response, HttpError] !io =\n"
            "  {\n"
            "    let _p = if dst_existed { as_http(purge_silent(a, dst), 500)? } else { 0 }\n"
            "    let ok = as_http(parent_exists(a, dst), 500)?\n"
            '    if not ok { Err(http_error(409, "父目录不存在")) } else { mc_apply(a, rel, dst, is_move, dst_existed) }\n'
            "  }\n",
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
