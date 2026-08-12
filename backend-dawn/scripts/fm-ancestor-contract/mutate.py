#!/usr/bin/env python3
"""Apply one compiling FM/WebDAV ancestor mutant in place."""

import argparse
from pathlib import Path

MUTANTS = (
    "immediate-tx-as-deferred",
    "immediate-tx-panic-no-rollback",
    "repo-file-ancestor-fail-open",
    "repo-immediate-parent-only",
    "repo-directory-target-fail-open",
    "repo-fm-insert-split-transaction",
    "repo-webdav-strict-insert-uses-fm",
    "repo-rename-fill-missing",
    "repo-fm-reparent-split-transaction",
    "repo-webdav-strict-reparent-uses-fm",
    "repo-file-upsert-disappeared-ok",
    "repo-empty-subtree-ok",
    "repo-copy-skip-source-revalidation",
    "repo-copy-skip-target-revalidation",
    "repo-literal-prefix-use-like",
    "repo-subtree-child-first",
    "repo-fm-copy-split-transaction",
    "repo-webdav-copy-split-transaction",
    "fm-reject-missing-ancestor",
    "fm-preflight-after-qiniu",
    "fm-upload-reuse-existing-key",
    "fm-copy-root-only-preflight",
    "fm-skip-directory-target-preflight",
    "fm-skip-upload-token-preflight",
    "fm-register-gc-before-preflight",
    "fm-skip-reparent-validation",
    "fm-skip-rename-validation",
    "fm-map-conflict-as-default",
    "repo-subtree-shape-fail-open",
    "webdav-skip-full-ancestor-validation",
    "webdav-missing-parent-fail-open",
    "webdav-reverse-overlap-fail-open",
    "webdav-copy-root-only-preflight",
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
    db_sql = args.project / "src/db/sql.dawn"
    service = args.project / "src/svc/files.dawn"
    fm_api = args.project / "src/api/api_fm.dawn"
    webdav = args.project / "src/api/webdav.dawn"

    if args.mutant == "immediate-tx-as-deferred":
        replace_once(
            db_sql,
            '  let _b = exec(c, "begin immediate", [])?\n',
            '  let _b = exec(c, "begin", [])?\n',
        )
    elif args.mutant == "immediate-tx-panic-no-rollback":
        replace_once(
            db_sql,
            "  bracket(c, h => rollback_immediate(h), _h => finish_immediate_tx(c, body))\n",
            "  finish_immediate_tx(c, body)\n",
        )
    elif args.mutant == "repo-file-ancestor-fail-open":
        replace_once(
            repo,
            "fn validate_insert_folder_strict_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_webdav_ancestors(c, rel)\n",
            "fn validate_insert_folder_strict_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io = {\n"
            "  let parent = parent_rel(rel)\n"
            "  match get_row(c, parent)? {\n"
            "    Some(_) -> Ok(())\n"
            "    None -> validate_webdav_ancestors(c, rel)\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "repo-immediate-parent-only":
        replace_once(
            repo,
            "fn validate_insert_file_strict_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_webdav_ancestors(c, rel)\n",
            "fn validate_insert_file_strict_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io = {\n"
            "  let parent = parent_rel(rel)\n"
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
    elif args.mutant == "repo-fm-insert-split-transaction":
        replace_once(
            repo,
            "pub fn insert_folder(c: DbConn, rel: String) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let _v = validate_insert_folder_ancestors(c, rel)?\n"
            "    let _a = ensure_loop(c, ancestor_paths(rel), 0)?\n"
            "    insert_folder_row(c, rel)\n"
            "  })\n",
            "pub fn insert_folder(c: DbConn, rel: String) -> Result[Int, String] !io = {\n"
            "  let _a = ensure_dirs(c, rel)?\n"
            "  with_immediate_tx(c, () => insert_folder_row(c, rel))\n"
            "}\n",
        )
        replace_once(
            repo,
            "pub fn insert_file(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let _v = validate_insert_file_ancestors(c, rel)?\n"
            "    let _a = ensure_loop(c, ancestor_paths(rel), 0)?\n"
            "    insert_file_row(c, rel, key, content_type, size)\n"
            "  })\n",
            "pub fn insert_file(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[Int, String] !io = {\n"
            "  let _a = ensure_dirs(c, rel)?\n"
            "  with_immediate_tx(c, () => insert_file_row(c, rel, key, content_type, size))\n"
            "}\n",
        )
    elif args.mutant == "repo-webdav-strict-insert-uses-fm":
        replace_once(
            repo,
            "pub fn insert_folder_strict(c: DbConn, rel: String) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let _a = validate_insert_folder_strict_ancestors(c, rel)?\n"
            "    insert_folder_row(c, rel)\n"
            "  })\n",
            "pub fn insert_folder_strict(c: DbConn, rel: String) -> Result[Int, String] !io =\n"
            "  insert_folder(c, rel)\n",
        )
        replace_once(
            repo,
            "pub fn insert_file_strict(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let _a = validate_insert_file_strict_ancestors(c, rel)?\n"
            "    insert_file_row(c, rel, key, content_type, size)\n"
            "  })\n",
            "pub fn insert_file_strict(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[Int, String] !io =\n"
            "  insert_file(c, rel, key, content_type, size)\n",
        )
    elif args.mutant == "repo-rename-fill-missing":
        replace_once(
            repo,
            "pub fn reparent_allow_missing(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let paths = validated_subtree_paths(c, old_rel)?\n"
            "    let _v = validate_reparent_allow_missing_destination(c, new_rel)?\n"
            "    reparent_loop(c, paths, old_rel, new_rel, 0)\n"
            "  })\n",
            "pub fn reparent_allow_missing(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  reparent_fill_missing(c, old_rel, new_rel)\n",
        )
    elif args.mutant == "repo-fm-reparent-split-transaction":
        replace_once(
            repo,
            "pub fn reparent_fill_missing(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let paths = validated_subtree_paths(c, old_rel)?\n"
            "    let _v = validate_reparent_fill_destination(c, new_rel)?\n"
            "    let _d = ensure_loop(c, ancestor_paths(new_rel), 0)?\n"
            "    reparent_loop(c, paths, old_rel, new_rel, 0)\n"
            "  })\n",
            "pub fn reparent_fill_missing(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io = {\n"
            "  let _d = ensure_dirs(c, new_rel)?\n"
            "  with_immediate_tx(c, () => {\n"
            "    let paths = validated_subtree_paths(c, old_rel)?\n"
            "    let _v = validate_reparent_fill_destination(c, new_rel)?\n"
            "    reparent_loop(c, paths, old_rel, new_rel, 0)\n"
            "  })\n"
            "}\n",
        )
    elif args.mutant == "repo-webdav-strict-reparent-uses-fm":
        start = (
            "pub fn reparent_strict(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let paths = validated_subtree_paths(c, old_rel)?\n"
            "    let _d = validate_reparent_strict_destination(c, new_rel)?\n"
            "    reparent_loop(c, paths, old_rel, new_rel, 0)\n"
            "  })\n"
        )
        replace_once(
            repo,
            start,
            "pub fn reparent_strict(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  reparent_fill_missing(c, old_rel, new_rel)\n",
        )
    elif args.mutant == "repo-file-upsert-disappeared-ok":
        replace_once(
            repo,
            '      None -> Err("file upsert target disappeared: ${full(rel)}")\n',
            "      None -> Ok(n)\n",
        )
    elif args.mutant == "repo-empty-subtree-ok":
        replace_once(
            repo,
            "fn validate_present_subtree(rows: List[FileRow], old_rel: String) -> Result[Unit, String] !io =\n"
            "  if len(rows) == 0 {\n"
            '    Err(conflict("源不存在：${full(old_rel)}"))\n'
            "  } else {\n"
            "    validate_subtree_rows(rows, old_rel, 0)\n"
            "  }\n",
            "fn validate_present_subtree(rows: List[FileRow], old_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_subtree_rows(rows, old_rel, 0)\n",
        )
    elif args.mutant == "repo-copy-skip-source-revalidation":
        replace_once(
            repo,
            "  let _s = validate_copy_snapshots(c, trees, 0)?\n",
            "  let _s = ()\n",
        )
    elif args.mutant == "repo-copy-skip-target-revalidation":
        replace_once(
            repo,
            "  let _t = validate_copy_targets(c, trees)?\n",
            "  let _t = ()\n",
        )
    elif args.mutant == "repo-literal-prefix-use-like":
        replace_once(
            repo,
            "    query(c, \"select $COLS from files where instr(path, ? || '/') = 1\", [PStr(parent)], col_types())?\n",
            '    query(c, "select $COLS from files where path like ?", [PStr("$parent/%")], col_types())?\n',
        )
        replace_once(
            repo,
            "    query(c, \"select $COLS from files where instr(path, ? || '/') = 1\", [PStr(base)], col_types())?\n",
            '    query(c, "select $COLS from files where path like ?", [PStr("$base/%")], col_types())?\n',
        )
        replace_once(
            repo,
            "  let rows = query(c, \"select $COLS from files where path = ? or instr(path, ? || '/') = 1 order by path\", [PStr(rel), PStr(rel)], col_types())?\n",
            '  let rows = query(c, "select $COLS from files where path = ? or path like ? order by path", [PStr(rel), PStr("$rel/%")], col_types())?\n',
        )
    elif args.mutant == "repo-subtree-child-first":
        replace_once(
            repo,
            "  let rows = query(c, \"select $COLS from files where path = ? or instr(path, ? || '/') = 1 order by path\", [PStr(rel), PStr(rel)], col_types())?\n",
            "  let rows = query(c, \"select $COLS from files where path = ? or instr(path, ? || '/') = 1 order by path desc\", [PStr(rel), PStr(rel)], col_types())?\n",
        )
    elif args.mutant == "repo-fm-copy-split-transaction":
        replace_once(
            repo,
            "pub fn commit_fm_copies(c: DbConn, trees: List[CopyTree]) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => commit_copy_trees_tx(c, trees, FillMissingCopyAncestors))\n",
            "pub fn commit_fm_copies(c: DbConn, trees: List[CopyTree]) -> Result[Int, String] !io =\n"
            "  commit_copy_trees_tx(c, trees, FillMissingCopyAncestors)\n",
        )
    elif args.mutant == "repo-webdav-copy-split-transaction":
        replace_once(
            repo,
            "pub fn commit_webdav_copy(c: DbConn, tree: CopyTree) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => commit_copy_trees_tx(c, [tree], RequireExistingCopyAncestors))\n",
            "pub fn commit_webdav_copy(c: DbConn, tree: CopyTree) -> Result[Int, String] !io =\n"
            "  commit_copy_trees_tx(c, [tree], RequireExistingCopyAncestors)\n",
        )
    elif args.mutant == "fm-reject-missing-ancestor":
        replace_once(
            service,
            "use repo/repo_fm.{FileRow, CopiedRow, CopyTree, fs_data, taken, insert_folder, insert_file, reparent_allow_missing, reparent_fill_missing, get_row, delete_row, subtree_rows, entry_json, validate_fm_ancestors, validate_fm_file_target, validate_subtree_destination, prepare_fm_copy, validate_copy_plan_targets, commit_fm_copies}\n",
            "use repo/repo_fm.{FileRow, CopiedRow, CopyTree, fs_data, taken, insert_folder, insert_folder_strict, insert_file, reparent_allow_missing, reparent_fill_missing, get_row, delete_row, subtree_rows, entry_json, validate_fm_ancestors, validate_fm_file_target, validate_subtree_destination, prepare_fm_copy, validate_copy_plan_targets, commit_fm_copies}\n",
        )
        replace_once(
            service,
            "    let _i = insert_folder(c, rel)?\n",
            "    let _i = insert_folder_strict(c, rel)?\n",
        )
    elif args.mutant == "fm-preflight-after-qiniu":
        replace_once(
            service,
            "fn validate_effect_ancestors(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn validate_effect_ancestors(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-upload-reuse-existing-key":
        replace_once(
            service,
            "fn upload_key(_existing: Option[FileRow]) -> String !io = uuid_hex()\n",
            "fn upload_key(existing: Option[FileRow]) -> String !io =\n"
            "  match existing {\n"
            "    Some(o) -> match o.key { Some(k) -> k, None -> uuid_hex() }\n"
            "    None -> uuid_hex()\n"
            "  }\n",
        )
    elif args.mutant == "fm-copy-root-only-preflight":
        replace_once(
            repo,
            "fn validate_prepare_fm_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io =\n"
            "  validate_copy_targets(c, [tree])\n",
            "fn validate_prepare_fm_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io =\n"
            "  match get_row(c, tree.destination_root)? {\n"
            '    Some(_) -> Err(conflict("复制目标已存在：${full(tree.destination_root)}"))\n'
            "    None -> Ok(())\n"
            "  }\n",
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
            service,
            "fn validate_move_destination(c: DbConn, src: String, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_subtree_destination(c, src, new_rel)\n",
            "fn validate_move_destination(_c: DbConn, _src: String, _new_rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            repo,
            "fn validate_reparent_fill_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, new_rel)\n",
            "fn validate_reparent_fill_destination(_c: DbConn, _new_rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-skip-rename-validation":
        replace_once(
            repo,
            "fn validate_reparent_allow_missing_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, new_rel)\n",
            "fn validate_reparent_allow_missing_destination(_c: DbConn, _new_rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
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
            "  with_db(a.db.path, a.db.ext, c => {\n"
            "    let parent = parent_rel(rel)\n"
            '    let found: Result[Option[FileRow], String] = if parent == "" { Ok(None) } else { get_row(c, parent) }\n'
            "    let row = found?\n"
            "    if not parent_is_directory(parent, row) {\n"
            "      Ok(false)\n"
            "    } else {\n"
            "      match validate_webdav_ancestors(c, parent) {\n"
            "        Ok(_) -> Ok(true)\n"
            "        Err(m) -> {\n"
            "          let (k, _t) = split_kind(m)\n"
            "          match k {\n"
            "            KConflict -> Ok(false)\n"
            "            _ -> Err(m)\n"
            "          }\n"
            "        }\n"
            "      }\n"
            "    }\n"
            "  })\n"
            "}\n",
            "fn parent_exists(a: Auth, rel: String) -> Result[Bool, String] !io =\n"
            "  with_db(a.db.path, a.db.ext, c => {\n"
            "    let parent = parent_rel(rel)\n"
            '    let found: Result[Option[FileRow], String] = if parent == "" { Ok(None) } else { get_row(c, parent) }\n'
            "    let row = found?\n"
            "    Ok(parent_is_directory(parent, row))\n"
            "  })\n",
        )
        replace_once(
            repo,
            "  validate_webdav_ancestors(c, parent_rel(new_rel))\n",
            "  Ok(())\n",
        )
    elif args.mutant == "webdav-missing-parent-fail-open":
        replace_once(
            repo,
            "  validate_webdav_ancestors(c, parent_rel(new_rel))\n",
            "  validate_fm_ancestors(c, parent_rel(new_rel))\n",
        )
        replace_once(
            webdav,
            "    if not parent_is_directory(parent, row) {\n",
            "    let direct_ok = match row { None -> true, Some(o) -> parent_is_directory(parent, Some(o)) }\n"
            "    if not direct_ok {\n",
        )
    elif args.mutant == "webdav-reverse-overlap-fail-open":
        replace_once(
            webdav,
            "fn destination_is_source_ancestor(rel: String, dst: String) -> Bool =\n"
            '  str.starts_with(rel, dst ++ "/")\n',
            "fn destination_is_source_ancestor(_rel: String, _dst: String) -> Bool =\n"
            "  false\n",
        )
    elif args.mutant == "webdav-copy-root-only-preflight":
        replace_once(
            repo,
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io =\n"
            "  validate_copy_targets(c, [tree])\n",
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io =\n"
            "  match get_row(c, tree.destination_root)? {\n"
            '    Some(_) -> Err(conflict("复制目标已存在：${full(tree.destination_root)}"))\n'
            "    None -> Ok(())\n"
            "  }\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
