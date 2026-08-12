#!/usr/bin/env python3
"""Apply one compiling FM/WebDAV ancestor mutant in place."""

import argparse
from pathlib import Path

MUTANTS = (
    "repo-file-ancestor-fail-open",
    "repo-immediate-parent-only",
    "repo-directory-target-fail-open",
    "fm-reject-missing-ancestor",
    "fm-preflight-after-qiniu",
    "fm-skip-directory-target-preflight",
    "fm-skip-upload-token-preflight",
    "fm-register-gc-before-preflight",
    "fm-skip-reparent-validation",
    "fm-map-conflict-as-default",
    "repo-subtree-shape-fail-open",
    "webdav-skip-full-ancestor-validation",
    "webdav-missing-parent-fail-open",
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

    repo = args.project / "src/repo/repo_fm.dawn"
    service = args.project / "src/svc/files.dawn"
    fm_api = args.project / "src/api/api_fm.dawn"
    webdav = args.project / "src/api/webdav.dawn"

    if args.mutant == "repo-file-ancestor-fail-open":
        replace_once(
            repo,
            "fn validate_insert_folder_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn validate_insert_folder_ancestors(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "repo-immediate-parent-only":
        replace_once(
            repo,
            "fn validate_insert_file_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn validate_insert_file_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io = {\n"
            "  let parent = parent_rel(rel)\n"
            '  if parent == "" {\n'
            "    Ok(())\n"
            "  } else {\n"
            "    match get_row(c, parent)? {\n"
            '      Some(o) -> if o.is_dir { Ok(()) } else { Err(conflict("父项不是目录")) }\n'
            "      None -> Ok(())\n"
            "    }\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "repo-directory-target-fail-open":
        replace_once(
            repo,
            "fn validate_insert_file_target(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  reject_directory_target(c, rel)\n",
            "fn validate_insert_file_target(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            repo,
            ' where files.is_dir = 0"',
            '"',
        )
    elif args.mutant == "fm-reject-missing-ancestor":
        replace_once(
            service,
            "use repo/repo_fm.{FileRow, fs_data, taken, ensure_dirs, insert_folder, insert_file, reparent, get_row, delete_row, subtree_rows, entry_json, validate_fm_ancestors, validate_fm_file_target, validate_subtree_destination}\n",
            "use repo/repo_fm.{FileRow, fs_data, taken, ensure_dirs, insert_folder, insert_file, reparent, get_row, delete_row, subtree_rows, entry_json, validate_fm_ancestors, validate_webdav_ancestors, validate_fm_file_target, validate_subtree_destination}\n",
        )
        replace_once(
            service,
            "pub fn mk_folder(c: DbConn, rel: String, cur: String) -> Result[Option[Json], String] !io = {\n"
            "  let t = taken(c, rel)?\n"
            "  if t {\n"
            "    Ok(None)\n"
            "  } else {\n"
            "    let _e = ensure_dirs(c, rel)?\n"
            "    let _i = insert_folder(c, rel)?\n"
            "    let d = fs_data(c, cur)?\n"
            "    Ok(Some(d))\n"
            "  }\n"
            "}\n",
            "pub fn mk_folder(c: DbConn, rel: String, cur: String) -> Result[Option[Json], String] !io = {\n"
            "  let t = taken(c, rel)?\n"
            "  if t {\n"
            "    Ok(None)\n"
            "  } else {\n"
            "    let _e = validate_webdav_ancestors(c, rel)?\n"
            "    let _i = insert_folder(c, rel)?\n"
            "    let d = fs_data(c, cur)?\n"
            "    Ok(Some(d))\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "fm-preflight-after-qiniu":
        replace_once(
            service,
            "fn validate_effect_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn validate_effect_ancestors(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-skip-directory-target-preflight":
        replace_once(
            service,
            "fn validate_effect_target(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_file_target(c, rel)\n",
            "fn validate_effect_target(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            fm_api,
            "fn upload_token_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io = {\n"
            "  let _a = upload_token_ancestor_preflight(c, rel)?\n"
            "  validate_fm_file_target(c, rel)\n"
            "}\n",
            "fn upload_token_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io = {\n"
            "  let _a = upload_token_ancestor_preflight(c, rel)?\n"
            "  Ok(())\n"
            "}\n",
        )
        replace_once(
            fm_api,
            "    let _a = validate_fm_ancestors(c, rel)?\n"
            "    let _t = validate_fm_file_target(c, rel)?\n"
            "    get_row(c, rel)\n",
            "    let _a = validate_fm_ancestors(c, rel)?\n"
            "    let _t = ()\n"
            "    get_row(c, rel)\n",
        )
    elif args.mutant == "fm-skip-upload-token-preflight":
        replace_once(
            fm_api,
            "fn upload_token_ancestor_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn upload_token_ancestor_preflight(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-register-gc-before-preflight":
        replace_once(
            fm_api,
            "fn register_preflight(a: Auth, rel: String, _key: String) -> Result[Option[FileRow], String] !io =\n"
            "  with_db(a.db.path, a.db.ext, c => {\n"
            "    let _a = validate_fm_ancestors(c, rel)?\n"
            "    let _t = validate_fm_file_target(c, rel)?\n"
            "    get_row(c, rel)\n"
            "  })\n",
            "fn register_preflight(a: Auth, rel: String, key: String) -> Result[Option[FileRow], String] !io = {\n"
            "  let existing = with_db(a.db.path, a.db.ext, c => get_row(c, rel))?\n"
            "  let _gc = gc_superseded(a.qiniu, existing, key)\n"
            "  let _a = with_db(a.db.path, a.db.ext, c => validate_fm_ancestors(c, rel))?\n"
            "  let _t = with_db(a.db.path, a.db.ext, c => validate_fm_file_target(c, rel))?\n"
            "  Ok(existing)\n"
            "}\n",
        )
    elif args.mutant == "fm-skip-reparent-validation":
        replace_once(
            repo,
            "  let _d = validate_fm_ancestors(c, new_rel)?\n",
            "  let _d = ()\n",
        )
    elif args.mutant == "fm-map-conflict-as-default":
        replace_once(
            fm_api,
            "          match as_http_with(with_db(a.db.path, a.db.ext, c => mk_folder(c, rel, cur)), conflict_or(500))? {\n",
            "          match as_http(with_db(a.db.path, a.db.ext, c => mk_folder(c, rel, cur)), 500)? {\n",
        )
    elif args.mutant == "repo-subtree-shape-fail-open":
        replace_once(
            repo,
            "fn validate_subtree_rows(rows: List[FileRow], old_rel: String, i: Int) -> Result[Unit, String] !io =\n"
            "  if i >= len(rows) {\n"
            "    Ok(())\n"
            "  } else {\n"
            "    let path = rows[i].path\n"
            "    let checked: Result[Unit, String] =\n"
            "      if path == old_rel {\n"
            "        Ok(())\n"
            "      } else {\n"
            "        validate_internal_parent(rows, old_rel, parent_rel(path))\n"
            "      }\n"
            "    let _v = checked?\n"
            "    validate_subtree_rows(rows, old_rel, i + 1)\n"
            "  }\n",
            "fn validate_subtree_rows(_rows: List[FileRow], _old_rel: String, _i: Int) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "webdav-skip-full-ancestor-validation":
        replace_once(
            webdav,
            "fn parent_exists(a: Auth, rel: String) -> Result[Bool, String] !io = {\n"
            "  let checked = with_db(a.db.path, a.db.ext, c => validate_webdav_ancestors(c, rel))\n"
            "  match checked {\n"
            "    Ok(_) -> Ok(true)\n"
            "    Err(m) -> {\n"
            "      let (k, _t) = split_kind(m)\n"
            "      match k {\n"
            "        KConflict -> Ok(false)\n"
            "        _ -> Err(m)\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n",
            "fn parent_exists(a: Auth, rel: String) -> Result[Bool, String] !io = {\n"
            "  let parent = parent_rel(rel)\n"
            '  if parent == "" {\n'
            "    Ok(true)\n"
            "  } else {\n"
            "    let row = with_db(a.db.path, a.db.ext, c => get_row(c, parent))?\n"
            "    Ok(parent_is_directory(parent, row))\n"
            "  }\n"
            "}\n",
        )
        replace_once(
            repo,
            "pub fn validate_webdav_subtree_destination(c: DbConn, old_rel: String, new_rel: String) -> Result[Unit, String] !io = {\n"
            "  let rows = subtree_rows(c, old_rel)?\n"
            "  let _s = validate_subtree_rows(rows, old_rel, 0)?\n"
            "  validate_webdav_ancestors(c, parent_rel(new_rel))\n"
            "}\n",
            "pub fn validate_webdav_subtree_destination(c: DbConn, old_rel: String, new_rel: String) -> Result[Unit, String] !io = {\n"
            "  let rows = subtree_rows(c, old_rel)?\n"
            "  let _s = validate_subtree_rows(rows, old_rel, 0)?\n"
            "  let parent = parent_rel(new_rel)\n"
            '  if parent == "" {\n'
            "    Ok(())\n"
            "  } else {\n"
            "    match get_row(c, parent)? {\n"
            '      Some(o) -> if o.is_dir { Ok(()) } else { Err(conflict("父项不是目录")) }\n'
            '      None -> Err(conflict("父目录不存在"))\n'
            "    }\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "webdav-missing-parent-fail-open":
        replace_once(
            repo,
            "  validate_webdav_ancestors(c, parent_rel(new_rel))\n",
            "  validate_fm_ancestors(c, parent_rel(new_rel))\n",
        )
        replace_once(
            webdav,
            "fn parent_exists(a: Auth, rel: String) -> Result[Bool, String] !io = {\n"
            "  let checked = with_db(a.db.path, a.db.ext, c => validate_webdav_ancestors(c, rel))\n"
            "  match checked {\n"
            "    Ok(_) -> Ok(true)\n"
            "    Err(m) -> {\n"
            "      let (k, _t) = split_kind(m)\n"
            "      match k {\n"
            "        KConflict -> Ok(false)\n"
            "        _ -> Err(m)\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n",
            "fn parent_exists(a: Auth, rel: String) -> Result[Bool, String] !io = {\n"
            "  let checked = with_db(a.db.path, a.db.ext, c => validate_webdav_ancestors(c, rel))\n"
            "  match checked {\n"
            "    Ok(_) -> Ok(true)\n"
            "    Err(m) -> {\n"
            "      let (k, _t) = split_kind(m)\n"
            "      match k {\n"
            "        KConflict -> {\n"
            "          let parent = parent_rel(rel)\n"
            '          if parent == "" {\n'
            "            Ok(true)\n"
            "          } else {\n"
            "            match with_db(a.db.path, a.db.ext, c => get_row(c, parent))? {\n"
            "              None -> Ok(true)\n"
            "              Some(_) -> Ok(false)\n"
            "            }\n"
            "          }\n"
            "        }\n"
            "        _ -> Err(m)\n"
            "      }\n"
            "    }\n"
            "  }\n"
            "}\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
