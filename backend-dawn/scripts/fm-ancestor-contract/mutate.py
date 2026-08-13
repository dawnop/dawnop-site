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
    "repo-file-target-descendants-preflight-fail-open",
    "repo-file-target-descendants-final-fail-open",
    "repo-fm-insert-split-transaction",
    "repo-webdav-strict-insert-uses-fm",
    "repo-rename-fill-missing",
    "repo-fm-reparent-split-transaction",
    "repo-fm-move-request-split-transaction",
    "repo-webdav-strict-reparent-uses-fm",
    "repo-file-upsert-disappeared-ok",
    "repo-empty-subtree-ok",
    "repo-copy-skip-source-revalidation",
    "repo-copy-skip-target-revalidation",
    "repo-copy-source-shape-preflight-fail-open",
    "repo-copy-blank-key-preflight-fail-open",
    "repo-copy-source-shape-final-fail-open",
    "repo-destination-subtree-preflight-fail-open",
    "repo-destination-subtree-final-fail-open",
    "repo-literal-prefix-use-like",
    "repo-subtree-child-first",
    "repo-fm-copy-split-transaction",
    "repo-webdav-copy-split-transaction",
    "fm-reject-missing-ancestor",
    "fm-preflight-after-qiniu",
    "fm-upload-reuse-existing-key",
    "fm-copy-root-only-preflight",
    "fm-copy-plain-error-as-502",
    "fm-skip-directory-target-preflight",
    "fm-skip-upload-token-preflight",
    "fm-register-stat-before-preflight",
    "fm-skip-reparent-validation",
    "fm-skip-rename-validation",
    "fm-map-conflict-as-default",
    "repo-subtree-shape-fail-open",
    "webdav-skip-full-ancestor-validation",
    "webdav-missing-parent-fail-open",
    "webdav-reverse-overlap-fail-open",
    "webdav-copy-root-only-preflight",
    "object-gc-panic-leak",
    "object-gc-release-lock-before-delete",
    "webdav-put-use-preflight-superseded",
    "gc-skip-live-key-reference",
    "webdav-delete-qiniu-before-detach",
    "webdav-delete-subtree-rowwise",
    "webdav-copy-overwrite-delete-on-failure",
    "webdav-move-overwrite-delete-on-failure",
    "webdav-collection-overwrite-delete-on-failure",
    "repo-webdav-copy-overwrite-prepare-root-blind",
    "repo-webdav-delete-root-blind",
    "repo-fm-delete-list-root-blind",
    "webdav-precondition-map-as-conflict",
    "fm-save-codepoint-size",
    "webdav-propfind-depth-singleton-fail-open",
    "webdav-propfind-depth-repeat-fail-open",
    "qiniu-upload-mime-passthrough",
    "multipart-part-ctype-literal-spelling",
    "fm-persisted-mime-passthrough",
    "fm-split-trims-addressing",
    "json-duplicate-members-fail-open",
    "config-range-falls-back-to-default",
    "config-nonnumeric-falls-back-to-default",
    "upload-token-expired-deadline",
    "name-guard-fm-create-folder-fail-open",
    "name-guard-fm-rename-fail-open",
    "name-guard-fm-move-fail-open",
    "name-guard-fm-copy-fail-open",
    "name-guard-fm-create-file-fail-open",
    "name-guard-fm-save-fail-open",
    "name-guard-fm-upload-token-fail-open",
    "name-guard-fm-register-fail-open",
    "name-guard-fm-proxy-upload-fail-open",
    "name-guard-webdav-put-fail-open",
    "name-guard-webdav-mkcol-fail-open",
    "name-guard-webdav-destination-fail-open",
    "entry-json-mime-passthrough",
    "copy-mime-passthrough",
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
    qiniu_rs = args.project / "src/qiniu/rs.dawn"
    multipart = args.project / "src/util/multipart.dawn"
    paths = args.project / "src/util/paths.dawn"
    jsonread = args.project / "src/util/jsonread.dawn"
    config = args.project / "src/config.dawn"

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
            "pub fn replace_file_strict(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[ReplaceOutcome, String] !io =\n"
            "  with_immediate_tx(c, () => replace_file_tx(c, rel, key, content_type, size, RequireExistingAncestors))\n",
            "pub fn replace_file_strict(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[ReplaceOutcome, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let parent = parent_rel(rel)\n"
            '    let checked: Result[Unit, String] = if parent == "" {\n'
            "      Ok(())\n"
            "    } else {\n"
            "      match get_row(c, parent)? {\n"
            '        Some(o) -> if o.is_dir { Ok(()) } else { Err(conflict("父项不是目录")) }\n'
            '        None -> Err(conflict("父目录不存在"))\n'
            "      }\n"
            "    }\n"
            "    let _v = checked?\n"
            "    let existing = get_row(c, rel)?\n"
            "    let _i = insert_file_row(c, rel, key, content_type, size)?\n"
            "    Ok(ReplaceOutcome {\n"
            "      target_existed: existing != None,\n"
            "      superseded_key: superseded_file_key(existing, key),\n"
            "    })\n"
            "  })\n",
        )
    elif args.mutant == "repo-directory-target-fail-open":
        replace_once(
            repo,
            "fn validate_insert_file_directory_target(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  reject_directory_target(c, rel)\n",
            "fn validate_insert_file_directory_target(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            repo,
            ' where files.is_dir = 0"',
            '"',
        )
    elif args.mutant == "repo-file-target-descendants-preflight-fail-open":
        replace_once(
            repo,
            "fn validate_file_target_descendants_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  reject_file_target_descendants(c, rel)\n",
            "fn validate_file_target_descendants_preflight(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "repo-file-target-descendants-final-fail-open":
        replace_once(
            repo,
            "fn validate_file_target_descendants_final(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  reject_file_target_descendants(c, rel)\n",
            "fn validate_file_target_descendants_final(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
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
            "pub fn replace_file(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[ReplaceOutcome, String] !io =\n"
            "  with_immediate_tx(c, () => replace_file_tx(c, rel, key, content_type, size, AllowMissingAncestors))\n",
            "pub fn replace_file(c: DbConn, rel: String, key: String, content_type: String, size: Int) -> Result[ReplaceOutcome, String] !io = {\n"
            "  let _a = ensure_dirs(c, rel)?\n"
            "  with_immediate_tx(c, () => replace_file_tx(c, rel, key, content_type, size, AllowMissingAncestors))\n"
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
            "  match replace_file_strict(c, rel, key, content_type, size) {\n"
            "    Ok(_) -> Ok(1)\n"
            "    Err(m) -> Err(m)\n"
            "  }\n",
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
    elif args.mutant == "repo-fm-move-request-split-transaction":
        replace_once(
            repo,
            "pub fn commit_fm_moves(c: DbConn, plans: List[MovePlan]) -> Result[Int, String] !io =\n"
            "  with_immediate_tx(c, () => commit_fm_move_plans(c, plans, 0))\n",
            "pub fn commit_fm_moves(c: DbConn, plans: List[MovePlan]) -> Result[Int, String] !io =\n"
            "  commit_fm_move_plans(c, plans, 0)\n",
        )
    elif args.mutant == "repo-webdav-strict-reparent-uses-fm":
        replace_once(
            repo,
            "pub fn reparent_strict(c: DbConn, old_rel: String, new_rel: String) -> Result[Int, String] !io =\n"
            "  match commit_webdav_move(c, old_rel, new_rel, false) {\n"
            "    Ok(outcome) -> Ok(outcome.written)\n"
            "    Err(m) -> Err(m)\n"
            "  }\n",
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
    elif args.mutant == "repo-copy-source-shape-preflight-fail-open":
        replace_once(
            repo,
            "fn validate_copy_source_preflight_shape(row: FileRow) -> Result[Unit, String] !io =\n"
            "  validate_copy_source_shape(row)\n",
            "fn validate_copy_source_preflight_shape(_row: FileRow) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "repo-copy-blank-key-preflight-fail-open":
        replace_once(
            repo,
            "fn validate_copy_source_preflight_key(row: FileRow) -> Result[Unit, String] !io =\n"
            "  validate_copy_source_nonblank_key(row)\n",
            "fn validate_copy_source_preflight_key(_row: FileRow) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "repo-copy-source-shape-final-fail-open":
        replace_once(
            repo,
            "    let _s = validate_copy_source_row(row.source)?\n",
            "    let _s = ()\n",
        )
        replace_once(
            repo,
            "      match row.copied_key {\n"
            '        Some(key) -> str.trim(key) != ""\n'
            "        None -> false\n"
            "      }\n",
            "      match row.copied_key {\n"
            '        Some(key) -> str.trim(key) != ""\n'
            "        None -> row.source.key == None\n"
            "      }\n",
        )
        replace_once(
            repo,
            '        None -> Err("copy insertion has no object result: ${full(row.source.path)}")\n',
            "        None -> Ok(0)\n",
        )
    elif args.mutant == "repo-destination-subtree-preflight-fail-open":
        replace_once(
            repo,
            "pub fn validate_absent_destination_subtree(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_empty_destination_subtree(c, new_rel)\n",
            "pub fn validate_absent_destination_subtree(_c: DbConn, _new_rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            repo,
            "fn validate_preflight_copy_unmapped_targets(c: DbConn, trees: List[CopyTree]) -> Result[Unit, String] !io =\n"
            "  validate_copy_unmapped_targets(c, trees, 0)\n",
            "fn validate_preflight_copy_unmapped_targets(_c: DbConn, _trees: List[CopyTree]) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "repo-destination-subtree-final-fail-open":
        replace_once(
            repo,
            "fn validate_final_destination_subtree(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_empty_destination_subtree(c, new_rel)\n",
            "fn validate_final_destination_subtree(_c: DbConn, _new_rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
        replace_once(
            repo,
            "fn validate_final_copy_unmapped_targets(c: DbConn, trees: List[CopyTree]) -> Result[Unit, String] !io =\n"
            "  validate_copy_unmapped_targets(c, trees, 0)\n",
            "fn validate_final_copy_unmapped_targets(_c: DbConn, _trees: List[CopyTree]) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
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
            "pub fn commit_webdav_copy(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[SubtreeWriteOutcome, String] !io =\n"
            "  with_immediate_tx(c, () => {\n",
            "fn mutant_webdav_copy_tx(c: DbConn, overwrite: Bool, body: fn() -> Result[SubtreeWriteOutcome, String] !io) -> Result[SubtreeWriteOutcome, String] !io =\n"
            "  if overwrite { with_immediate_tx(c, body) } else { body() }\n"
            "\n"
            "pub fn commit_webdav_copy(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[SubtreeWriteOutcome, String] !io =\n"
            "  mutant_webdav_copy_tx(c, overwrite, () => {\n",
        )
    elif args.mutant == "fm-reject-missing-ancestor":
        replace_once(
            service,
            "use repo/repo_fm.{FileRow, ReplaceOutcome, MovePlan, CopiedRow, CopyTree, fs_data, taken, insert_folder, replace_file, create_file_once, reparent_allow_missing, prepare_fm_move, commit_fm_moves, get_row, delete_subtrees, entry_json, key_referenced, key_pending, validate_fm_ancestors, validate_fm_file_target, prepare_fm_copy, validate_copy_plan_targets, commit_fm_copies}\n",
            "use repo/repo_fm.{FileRow, ReplaceOutcome, MovePlan, CopiedRow, CopyTree, fs_data, taken, insert_folder, insert_folder_strict, replace_file, create_file_once, reparent_allow_missing, prepare_fm_move, commit_fm_moves, get_row, delete_subtrees, entry_json, key_referenced, key_pending, validate_fm_ancestors, validate_fm_file_target, prepare_fm_copy, validate_copy_plan_targets, commit_fm_copies}\n",
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
            "  let key = upload_key()\n",
            "  let existing = with_db(a.db.path, a.db.ext, c => get_row(c, rel))?\n"
            "  let key = upload_key(existing)\n",
        )
        replace_once(
            service,
            "fn upload_key() -> String !io = uuid_hex()\n",
            "fn upload_key(existing: Option[FileRow]) -> String !io =\n"
            "  match existing {\n"
            "    Some(o) -> match o.key { Some(k) -> k, None -> uuid_hex() }\n"
            "    None -> uuid_hex()\n"
            "  }\n",
        )
    elif args.mutant == "fm-copy-root-only-preflight":
        replace_once(
            repo,
            "fn validate_prepare_fm_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io = {\n"
            "  let _u = validate_preflight_copy_unmapped_targets(c, [tree])?\n"
            "  validate_copy_targets(c, [tree])\n"
            "}\n",
            "fn validate_prepare_fm_copy_targets(c: DbConn, tree: CopyTree) -> Result[Unit, String] !io = {\n"
            "  let _u = validate_preflight_copy_unmapped_targets(c, [tree])?\n"
            "  match get_row(c, tree.destination_root)? {\n"
            '    Some(_) -> Err(conflict("复制目标已存在：${full(tree.destination_root)}"))\n'
            "    None -> Ok(())\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "fm-copy-plain-error-as-502":
        replace_once(
            fm_api,
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_copy(a.qiniu, c, sources, dest, cur, 0)), mutation_failure)?))\n",
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_copy(a.qiniu, c, sources, dest, cur, 0)), conflict_or(502))?))\n",
        )
    elif args.mutant == "fm-skip-directory-target-preflight":
        replace_once(
            repo,
            "fn validate_file_target_directory_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  reject_directory_target(c, rel)\n",
            "fn validate_file_target_directory_preflight(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-skip-upload-token-preflight":
        replace_once(
            fm_api,
            "fn upload_token_ancestor_preflight(c: DbConn, rel: String) -> Result[Unit, String] !io =\n"
            "  validate_fm_ancestors(c, rel)\n",
            "fn upload_token_ancestor_preflight(_c: DbConn, _rel: String) -> Result[Unit, String] !io =\n"
            "  Ok(())\n",
        )
    elif args.mutant == "fm-register-stat-before-preflight":
        replace_once(
            repo,
            "  } else {\n"
            "    let _a = validate_fm_ancestors(c, rel)?\n"
            "    validate_fm_file_target(c, rel)\n"
            "  }\n"
            "}\n"
            "\n"
            "pub fn finalize_register",
            "  } else {\n"
            "    validate_fm_file_target(c, rel)\n"
            "  }\n"
            "}\n"
            "\n"
            "pub fn finalize_register",
        )
        replace_once(
            fm_api,
            "          let r = as_http(stat(a.qiniu.rs_host, a.qiniu.ak, a.qiniu.sk, a.qiniu.bucket, key), 502)?\n"
            "          if r.status == 200 {\n",
            "          let r = as_http(stat(a.qiniu.rs_host, a.qiniu.ak, a.qiniu.sk, a.qiniu.bucket, key), 502)?\n"
            "          let _ancestors = as_http_with(with_db(a.db.path, a.db.ext, c => validate_fm_ancestors(c, rel)), conflict_or(500))?\n"
            "          if r.status == 200 {\n",
        )
    elif args.mutant == "fm-skip-reparent-validation":
        replace_once(
            repo,
            "pub fn prepare_fm_move(c: DbConn, old_rel: String, new_rel: String) -> Result[MovePlan, String] !io = {\n"
            "  let _v = validate_subtree_destination(c, old_rel, new_rel)?\n"
            "  Ok(MovePlan { source_root: old_rel, destination_root: new_rel })\n"
            "}\n",
            "pub fn prepare_fm_move(_c: DbConn, old_rel: String, new_rel: String) -> Result[MovePlan, String] !io =\n"
            "  Ok(MovePlan { source_root: old_rel, destination_root: new_rel })\n",
        )
        replace_once(
            repo,
            "fn validate_reparent_fill_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io = {\n"
            "  let _a = validate_fm_ancestors(c, new_rel)?\n"
            "  validate_final_destination_subtree(c, new_rel)\n"
            "}\n",
            "fn validate_reparent_fill_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_final_destination_subtree(c, new_rel)\n",
        )
    elif args.mutant == "fm-skip-rename-validation":
        replace_once(
            repo,
            "fn validate_reparent_allow_missing_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io = {\n"
            "  let _a = validate_fm_ancestors(c, new_rel)?\n"
            "  validate_final_destination_subtree(c, new_rel)\n"
            "}\n",
            "fn validate_reparent_allow_missing_destination(c: DbConn, new_rel: String) -> Result[Unit, String] !io =\n"
            "  validate_final_destination_subtree(c, new_rel)\n",
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
        replace_once(
            repo,
            "      written: moved,\n    })\n  })\n\nfn validate_disjoint_subtrees",
            "      written: moved,\n"
            "    })\n"
            "  })\n"
            "\n"
            "pub fn mutant_commit_webdav_move_without_overlap_guard(c: DbConn, old_rel: String, new_rel: String, overwrite: Bool) -> Result[SubtreeWriteOutcome, String] !io =\n"
            "  with_immediate_tx(c, () => {\n"
            "    let paths = validated_subtree_paths(c, old_rel)?\n"
            "    let _a = validate_webdav_ancestors(c, new_rel)?\n"
            "    let destination_rows = subtree_rows(c, new_rel)?\n"
            "    let destination_existed = validate_webdav_destination_snapshot(destination_rows, new_rel, overwrite)?\n"
            "    let replaced: List[FileRow] = if destination_existed { delete_subtree_tx(c, new_rel)? } else { [] }\n"
            "    let moved = reparent_loop(c, paths, old_rel, new_rel, 0)?\n"
            "    Ok(SubtreeWriteOutcome {\n"
            "      destination_existed: destination_existed,\n"
            "      replaced_rows: replaced,\n"
            "      written: moved,\n"
            "    })\n"
            "  })\n"
            "\n"
            "fn validate_disjoint_subtrees",
        )
        replace_once(
            webdav,
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, mutant_commit_webdav_move_without_overlap_guard, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
        )
        replace_once(
            webdav,
            "      with_db(a.db.path, a.db.ext, c => commit_webdav_move(c, rel, dst, overwrite))\n",
            "      with_db(a.db.path, a.db.ext, c => mutant_commit_webdav_move_without_overlap_guard(c, rel, dst, overwrite))\n",
        )
    elif args.mutant == "webdav-copy-root-only-preflight":
        replace_once(
            repo,
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[Unit, String] !io =\n"
            "  {\n"
            "    let _s = validate_webdav_destination_preflight(c, tree.destination_root, overwrite)?\n"
            "    if overwrite {\n"
            "      Ok(())\n"
            "    } else {\n"
            "      let _u = validate_preflight_copy_unmapped_targets(c, [tree])?\n"
            "      validate_copy_targets(c, [tree])\n"
            "    }\n"
            "  }\n",
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[Unit, String] !io =\n"
            "  if overwrite {\n"
            "    validate_webdav_destination_preflight(c, tree.destination_root, overwrite)\n"
            "  } else {\n"
            "    match get_row(c, tree.destination_root)? {\n"
            '      Some(_) -> Err(conflict("复制目标已存在：${full(tree.destination_root)}"))\n'
            "      None -> Ok(())\n"
            "    }\n"
            "  }\n",
        )
    elif args.mutant == "object-gc-panic-leak":
        replace_once(
            service,
            "fn best_effort_gc(body: fn() -> Result[Unit, String] !io) -> Unit !io = {\n"
            "  let _outcome: Result[Result[Unit, String], ForeignError] = catch_panic(body)\n"
            "  ()\n"
            "}\n",
            "fn best_effort_gc(body: fn() -> Result[Unit, String] !io) -> Unit !io = {\n"
            "  let _outcome: Result[Unit, String] = body()\n"
            "  ()\n"
            "}\n",
        )
    elif args.mutant == "object-gc-release-lock-before-delete":
        replace_once(
            service,
            "fn gc_unreferenced_on(q: Qiniu, c: DbConn, candidate: Option[String]) -> Result[Unit, String] !io =\n"
            "  match candidate {\n"
            "    Some(key) ->\n"
            '      if str.trim(key) == "" {\n'
            "        Ok(())\n"
            "      } else {\n"
            "        with_immediate_tx(c, () => {\n"
            "          let referenced = key_referenced(c, key)?\n"
            "          let pending = key_pending(c, key)?\n"
            "          if referenced || pending {\n"
            "            Ok(())\n"
            "          } else {\n"
            "            let _d: Result[Unit, String] = delete_obj(q.rs_host, q.ak, q.sk, q.bucket, key)\n"
            "            Ok(())\n"
            "          }\n"
            "        })\n"
            "      }\n"
            "    None -> Ok(())\n"
            "  }\n",
            "fn gc_unreferenced_on(q: Qiniu, c: DbConn, candidate: Option[String]) -> Result[Unit, String] !io =\n"
            "  match candidate {\n"
            "    Some(key) ->\n"
            '      if str.trim(key) == "" {\n'
            "        Ok(())\n"
            "      } else {\n"
            "        let checked: Result[Bool, String] = with_immediate_tx(c, () => {\n"
            "          let referenced = key_referenced(c, key)?\n"
            "          let pending = key_pending(c, key)?\n"
            "          Ok(not referenced && not pending)\n"
            "        })\n"
            "        let deletable = checked?\n"
            "        if deletable {\n"
            "          let _d: Result[Unit, String] = delete_obj(q.rs_host, q.ak, q.sk, q.bucket, key)\n"
            "          Ok(())\n"
            "        } else {\n"
            "          Ok(())\n"
            "        }\n"
            "      }\n"
            "    None -> Ok(())\n"
            "  }\n",
        )
    elif args.mutant == "webdav-put-use-preflight-superseded":
        replace_once(
            webdav,
            "    } else {\n"
            "      let mime = guess_mime(basename(rel))\n"
            "      let e = ext(basename(rel))\n",
            "    } else {\n"
            "      let stale = as_http(with_db(a.db.path, a.db.ext, c => get_row(c, rel)), 500)?\n"
            "      let stale_key = match stale { Some(o) -> o.key, None -> None }\n"
            "      let mime = guess_mime(basename(rel))\n"
            "      let e = ext(basename(rel))\n",
        )
        replace_once(
            webdav,
            "            Ok(_) -> put_commit(a, rel, key, mime, size)\n",
            "            Ok(_) -> put_commit(a, rel, key, mime, size, stale_key)\n",
        )
        replace_once(
            webdav,
            "fn put_commit(a: Auth, rel: String, key: String, mime: String, size: Int) -> Result[Response, HttpError] !io =\n",
            "fn put_commit(a: Auth, rel: String, key: String, mime: String, size: Int, stale_key: Option[String]) -> Result[Response, HttpError] !io =\n",
        )
        replace_once(
            webdav,
            "      let _gc = gc_unreferenced(a, outcome.superseded_key)\n",
            "      let _gc = gc_unreferenced(a, stale_key)\n",
        )
    elif args.mutant == "gc-skip-live-key-reference":
        replace_once(
            service,
            "          if referenced || pending {\n",
            "          if pending {\n",
        )
    elif args.mutant == "webdav-delete-qiniu-before-detach":
        replace_once(
            webdav,
            "use db/sql.{DbConn}\n",
            "use db/sql.{DbConn, with_immediate_tx}\n",
        )
        replace_once(
            webdav,
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, delete_row, subtree_rows, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
        )
        replace_once(
            webdav,
            "use qiniu/rs.{upload_file}\n",
            "use qiniu/rs.{upload_file, delete_obj}\n",
        )
        replace_once(
            webdav,
            "fn del(a: Auth, rel: String) -> Result[Response, HttpError] !io =\n"
            '  if rel == "" {\n'
            '    Err(http_error(405, "不能删除根"))\n'
            "  } else {\n"
            "    let rows = as_http(with_db(a.db.path, a.db.ext, c => delete_subtree(c, rel)), 500)?\n"
            "    if len(rows) == 0 {\n"
            '      Err(http_error(404, "资源不存在"))\n'
            "    } else {\n"
            "      let _gc = gc_rows_unreferenced(a, rows)\n"
            '      Ok(text(204, ""))\n'
            "    }\n"
            "  }\n",
            "fn mutant_delete_metadata(c: DbConn, rows: List[FileRow], i: Int) -> Result[Int, String] !io =\n"
            "  if i >= len(rows) {\n"
            "    Ok(0)\n"
            "  } else {\n"
            "    let deleted = delete_row(c, rows[i].path)?\n"
            "    let rest = mutant_delete_metadata(c, rows, i + 1)?\n"
            "    Ok(deleted + rest)\n"
            "  }\n"
            "\n"
            "fn mutant_delete_objects(a: Auth, rows: List[FileRow], i: Int) -> Unit !io =\n"
            "  if i >= len(rows) {\n"
            "    ()\n"
            "  } else {\n"
            "    let _d: Result[Unit, String] = match rows[i].key {\n"
            "      Some(key) -> delete_obj(a.qiniu.rs_host, a.qiniu.ak, a.qiniu.sk, a.qiniu.bucket, key)\n"
            "      None -> Ok(())\n"
            "    }\n"
            "    mutant_delete_objects(a, rows, i + 1)\n"
            "  }\n"
            "\n"
            "fn mutant_delete_before_commit(a: Auth, rel: String) -> Result[List[FileRow], String] !io =\n"
            "  with_db(a.db.path, a.db.ext, c =>\n"
            "    with_immediate_tx(c, () => {\n"
            "      let rows = subtree_rows(c, rel)?\n"
            "      let _d = mutant_delete_metadata(c, rows, 0)?\n"
            "      let _q = mutant_delete_objects(a, rows, 0)\n"
            "      Ok(rows)\n"
            "    }))\n"
            "\n"
            "fn del(a: Auth, rel: String) -> Result[Response, HttpError] !io =\n"
            '  if rel == "" {\n'
            '    Err(http_error(405, "不能删除根"))\n'
            "  } else {\n"
            "    let rows = as_http(mutant_delete_before_commit(a, rel), 500)?\n"
            "    if len(rows) == 0 {\n"
            '      Err(http_error(404, "资源不存在"))\n'
            "    } else {\n"
            '      Ok(text(204, ""))\n'
            "    }\n"
            "  }\n",
        )
    elif args.mutant == "webdav-delete-subtree-rowwise":
        replace_once(
            webdav,
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, delete_row, subtree_rows, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
        )
        replace_once(
            webdav,
            "fn del(a: Auth, rel: String) -> Result[Response, HttpError] !io =\n"
            '  if rel == "" {\n'
            '    Err(http_error(405, "不能删除根"))\n'
            "  } else {\n"
            "    let rows = as_http(with_db(a.db.path, a.db.ext, c => delete_subtree(c, rel)), 500)?\n"
            "    if len(rows) == 0 {\n"
            '      Err(http_error(404, "资源不存在"))\n'
            "    } else {\n"
            "      let _gc = gc_rows_unreferenced(a, rows)\n"
            '      Ok(text(204, ""))\n'
            "    }\n"
            "  }\n",
            "fn mutant_delete_rows(c: DbConn, rows: List[FileRow], i: Int) -> Result[Int, String] !io =\n"
            "  if i >= len(rows) {\n"
            "    Ok(0)\n"
            "  } else {\n"
            "    let deleted = delete_row(c, rows[i].path)?\n"
            "    let rest = mutant_delete_rows(c, rows, i + 1)?\n"
            "    Ok(deleted + rest)\n"
            "  }\n"
            "\n"
            "fn mutant_delete_rowwise(a: Auth, rel: String) -> Result[List[FileRow], String] !io =\n"
            "  with_db(a.db.path, a.db.ext, c => {\n"
            "    let rows = subtree_rows(c, rel)?\n"
            "    let _d = mutant_delete_rows(c, rows, 0)?\n"
            "    Ok(rows)\n"
            "  })\n"
            "\n"
            "fn del(a: Auth, rel: String) -> Result[Response, HttpError] !io =\n"
            '  if rel == "" {\n'
            '    Err(http_error(405, "不能删除根"))\n'
            "  } else {\n"
            "    let rows = as_http(mutant_delete_rowwise(a, rel), 500)?\n"
            "    if len(rows) == 0 {\n"
            '      Err(http_error(404, "资源不存在"))\n'
            "    } else {\n"
            "      let _gc = gc_rows_unreferenced(a, rows)\n"
            '      Ok(text(204, ""))\n'
            "    }\n"
            "  }\n",
        )
    elif args.mutant == "webdav-copy-overwrite-delete-on-failure":
        replace_once(
            webdav,
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
            "use repo/repo_fm.{FileRow, SubtreeWriteOutcome, CopyTree, get_row, children, replace_file_strict, insert_folder_strict, delete_subtree, commit_webdav_move, validate_fm_file_target, validate_webdav_ancestors, validate_webdav_subtree_destination, validate_webdav_destination_preflight, validate_copy_source_objects, prepare_webdav_copy, commit_webdav_copy}\n",
        )
        replace_once(
            webdav,
            "  let copied = copy_tree_objects(a.qiniu, planned)?\n",
            "  let copied_result: Result[CopyTree, String] = match copy_tree_objects(a.qiniu, planned) {\n"
            "    Ok(tree) -> Ok(tree)\n"
            "    Err(m) -> {\n"
            "      let removed = with_db(a.db.path, a.db.ext, c => delete_subtree(c, dst))?\n"
            "      let _gc = gc_rows_unreferenced(a, removed)\n"
            "      Err(m)\n"
            "    }\n"
            "  }\n"
            "  let copied = copied_result?\n",
        )
    elif args.mutant == "webdav-move-overwrite-delete-on-failure":
        replace_once(
            webdav,
            "fn mc_apply(a: Auth, rel: String, dst: String, is_move: Bool, overwrite: Bool) -> Result[Response, HttpError] !io = {\n",
            "fn mutant_cleanup_failed_file_move(a: Auth, rel: String, dst: String, is_move: Bool, overwrite: Bool) -> Unit !io =\n"
            "  if not is_move || not overwrite {\n"
            "    ()\n"
            "  } else {\n"
            "    match with_db(a.db.path, a.db.ext, c => get_row(c, rel)) {\n"
            "      Ok(Some(source)) -> if source.is_dir {\n"
            "        ()\n"
            "      } else {\n"
            "        match with_db(a.db.path, a.db.ext, c => delete_subtree(c, dst)) {\n"
            "          Ok(rows) -> gc_rows_unreferenced(a, rows)\n"
            "          Err(_) -> ()\n"
            "        }\n"
            "      }\n"
            "      Ok(None) -> ()\n"
            "      Err(_) -> ()\n"
            "    }\n"
            "  }\n"
            "\n"
            "fn mc_apply(a: Auth, rel: String, dst: String, is_move: Bool, overwrite: Bool) -> Result[Response, HttpError] !io = {\n",
        )
        replace_once(
            webdav,
            "    Err(m) -> Err(mutation_error(m))\n",
            "    Err(m) -> {\n"
            "      let _cleanup = mutant_cleanup_failed_file_move(a, rel, dst, is_move, overwrite)\n"
            "      Err(mutation_error(m))\n"
            "    }\n",
        )
    elif args.mutant == "webdav-collection-overwrite-delete-on-failure":
        replace_once(
            webdav,
            "fn mc_apply(a: Auth, rel: String, dst: String, is_move: Bool, overwrite: Bool) -> Result[Response, HttpError] !io = {\n",
            "fn mutant_cleanup_failed_collection(a: Auth, rel: String, dst: String, overwrite: Bool) -> Unit !io =\n"
            "  if not overwrite {\n"
            "    ()\n"
            "  } else {\n"
            "    match with_db(a.db.path, a.db.ext, c => get_row(c, rel)) {\n"
            "      Ok(Some(source)) -> if source.is_dir {\n"
            "        match with_db(a.db.path, a.db.ext, c => delete_subtree(c, dst)) {\n"
            "          Ok(rows) -> gc_rows_unreferenced(a, rows)\n"
            "          Err(_) -> ()\n"
            "        }\n"
            "      } else {\n"
            "        ()\n"
            "      }\n"
            "      Ok(None) -> ()\n"
            "      Err(_) -> ()\n"
            "    }\n"
            "  }\n"
            "\n"
            "fn mc_apply(a: Auth, rel: String, dst: String, is_move: Bool, overwrite: Bool) -> Result[Response, HttpError] !io = {\n",
        )
        replace_once(
            webdav,
            "    Err(m) -> Err(mutation_error(m))\n",
            "    Err(m) -> {\n"
            "      let _cleanup = mutant_cleanup_failed_collection(a, rel, dst, overwrite)\n"
            "      Err(mutation_error(m))\n"
            "    }\n",
        )
    elif args.mutant == "repo-webdav-copy-overwrite-prepare-root-blind":
        replace_once(
            repo,
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[Unit, String] !io =\n"
            "  {\n"
            "    let _s = validate_webdav_destination_preflight(c, tree.destination_root, overwrite)?\n"
            "    if overwrite {\n"
            "      Ok(())\n"
            "    } else {\n"
            "      let _u = validate_preflight_copy_unmapped_targets(c, [tree])?\n"
            "      validate_copy_targets(c, [tree])\n"
            "    }\n"
            "  }\n",
            "fn validate_prepare_webdav_copy_targets(c: DbConn, tree: CopyTree, overwrite: Bool) -> Result[Unit, String] !io =\n"
            "  if overwrite {\n"
            "    Ok(())\n"
            "  } else {\n"
            "    let _s = validate_webdav_destination_preflight(c, tree.destination_root, overwrite)?\n"
            "    let _u = validate_preflight_copy_unmapped_targets(c, [tree])?\n"
            "    validate_copy_targets(c, [tree])\n"
            "  }\n",
        )
    elif args.mutant == "repo-webdav-delete-root-blind":
        replace_once(
            repo,
            "pub fn delete_subtree(c: DbConn, rel: String) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => delete_subtree_tx(c, rel))\n",
            "pub fn delete_subtree(c: DbConn, rel: String) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => mutant_delete_subtree_root_blind_tx(c, rel))\n",
        )
        replace_once(
            repo,
            "pub fn delete_subtrees(c: DbConn, rels: List[String]) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => delete_subtree_list_tx(c, rels, 0, []))\n",
            "fn mutant_delete_subtree_root_blind_tx(c: DbConn, rel: String) -> Result[List[FileRow], String] !io =\n"
            '  if rel == "" {\n'
            "    Ok([])\n"
            "  } else {\n"
            "    let rows = subtree_rows(c, rel)?\n"
            "    let n = exec(c, \"delete from files where path = ? or instr(path, ? || '/') = 1\", [PStr(rel), PStr(rel)])?\n"
            '    if n == len(rows) { Ok(rows) } else { Err("subtree delete count changed: ${full(rel)}") }\n'
            "  }\n"
            "\n"
            "pub fn delete_subtrees(c: DbConn, rels: List[String]) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => delete_subtree_list_tx(c, rels, 0, []))\n",
        )
    elif args.mutant == "repo-fm-delete-list-root-blind":
        replace_once(
            repo,
            "pub fn delete_subtrees(c: DbConn, rels: List[String]) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => delete_subtree_list_tx(c, rels, 0, []))\n",
            "pub fn delete_subtrees(c: DbConn, rels: List[String]) -> Result[List[FileRow], String] !io =\n"
            "  with_immediate_tx(c, () => mutant_delete_subtree_list_root_blind_tx(c, rels, 0, []))\n",
        )
        replace_once(
            repo,
            "fn delete_subtree_list_tx(c: DbConn, rels: List[String], i: Int, acc: List[FileRow]) -> Result[List[FileRow], String] !io =\n",
            "fn mutant_delete_subtree_root_blind_tx(c: DbConn, rel: String) -> Result[List[FileRow], String] !io =\n"
            '  if rel == "" {\n'
            "    Ok([])\n"
            "  } else {\n"
            "    let rows = subtree_rows(c, rel)?\n"
            "    let n = exec(c, \"delete from files where path = ? or instr(path, ? || '/') = 1\", [PStr(rel), PStr(rel)])?\n"
            '    if n == len(rows) { Ok(rows) } else { Err("subtree delete count changed: ${full(rel)}") }\n'
            "  }\n"
            "\n"
            "fn mutant_delete_subtree_list_root_blind_tx(c: DbConn, rels: List[String], i: Int, acc: List[FileRow]) -> Result[List[FileRow], String] !io =\n"
            "  if i >= len(rels) {\n"
            "    Ok(acc)\n"
            "  } else {\n"
            "    let rows = mutant_delete_subtree_root_blind_tx(c, rels[i])?\n"
            "    mutant_delete_subtree_list_root_blind_tx(c, rels, i + 1, acc ++ rows)\n"
            "  }\n"
            "\n"
            "fn delete_subtree_list_tx(c: DbConn, rels: List[String], i: Int, acc: List[FileRow]) -> Result[List[FileRow], String] !io =\n",
        )
    elif args.mutant == "webdav-precondition-map-as-conflict":
        replace_once(
            webdav,
            "fn conflict_or(other: Int) -> fn(String) -> HttpError =\n"
            "  msg => {\n"
            "    let (k, t) = split_kind(msg)\n"
            "    match k {\n"
            "      KConflict -> http_error(409, t)\n"
            "      KPrecondition -> http_error(412, t)\n"
            "      _ -> http_error(other, t)\n"
            "    }\n"
            "  }\n",
            "fn conflict_or(other: Int) -> fn(String) -> HttpError =\n"
            "  msg => {\n"
            "    let (k, t) = split_kind(msg)\n"
            "    match k {\n"
            "      KConflict -> http_error(409, t)\n"
            "      KPrecondition -> http_error(409, t)\n"
            "      _ -> http_error(other, t)\n"
            "    }\n"
            "  }\n",
        )
        replace_once(
            webdav,
            "fn mutation_error(msg: String) -> HttpError = {\n"
            "  let (k, t) = split_kind(msg)\n"
            "  match k {\n"
            "    KConflict -> http_error(409, t)\n"
            "    KPrecondition -> http_error(412, t)\n"
            "    KUpstream -> http_error(502, t)\n"
            "    _ -> http_error(500, t)\n"
            "  }\n"
            "}\n",
            "fn mutation_error(msg: String) -> HttpError = {\n"
            "  let (k, t) = split_kind(msg)\n"
            "  match k {\n"
            "    KConflict -> http_error(409, t)\n"
            "    KPrecondition -> http_error(409, t)\n"
            "    KUpstream -> http_error(502, t)\n"
            "    _ -> http_error(500, t)\n"
            "  }\n"
            "}\n",
        )
    elif args.mutant == "fm-save-codepoint-size":
        replace_once(
            service,
            "  match with_db(a.db.path, a.db.ext, c => save_db(c, rel, new_key, mime, text_byte_size(content))) {\n",
            "  match with_db(a.db.path, a.db.ext, c => save_db(c, rel, new_key, mime, str.len(content))) {\n",
        )
    elif args.mutant == "webdav-propfind-depth-singleton-fail-open":
        # the reading before the tightening: whatever the single header said was
        # accepted, and everything that was not "0" listed one level down
        replace_once(
            webdav,
            "    let only = given[0]\n"
            '    if only == "0" || only == "1" {\n'
            "      Ok(only)\n"
            "    } else {\n"
            '      Err(http_error(400, "PROPFIND Depth 只支持 0 或 1"))\n'
            "    }\n",
            "    Ok(given[0])\n",
        )
    elif args.mutant == "webdav-propfind-depth-repeat-fail-open":
        # first header wins, the guess this server refuses to make
        replace_once(
            webdav,
            "  } else if len(given) > 1 {\n"
            '    Err(http_error(400, "PROPFIND Depth 只能出现一次"))\n'
            "  } else {\n",
            "  } else {\n",
        )
    elif args.mutant == "qiniu-upload-mime-passthrough":
        # a stored content type reaches the qiniu multipart header block as written
        replace_once(
            qiniu_rs,
            'Content-Type: ${safe_persisted_mime(mime)}$nl$nl"',
            'Content-Type: $mime$nl$nl"',
        )
    elif args.mutant == "multipart-part-ctype-literal-spelling":
        # #241: one spelling of the header name recognised, the rest reported as
        # "the part declared no type"
        replace_once(
            multipart,
            'fn part_ctype(headers: String) -> String = unwrap_or(part_header(headers, "content-type"), "")\n',
            'fn part_ctype(headers: String) -> String = unwrap_or(between(headers, "Content-Type: ", crlf()), "")\n',
        )
    elif args.mutant == "fm-persisted-mime-passthrough":
        # save reuses the row's stored type without the outbound boundary, so a
        # dirty row stays dirty across a rewrite
        replace_once(
            service,
            "fn save_mime(existing: Option[FileRow], rel: String) -> String =\n"
            "  safe_persisted_mime(match existing {\n"
            '    Some(o) -> if o.content_type != "" { o.content_type } else { guess_mime(rel) }\n'
            "    None -> guess_mime(rel)\n"
            "  })\n",
            "fn save_mime(existing: Option[FileRow], rel: String) -> String =\n"
            "  match existing {\n"
            '    Some(o) -> if o.content_type != "" { o.content_type } else { guess_mime(rel) }\n'
            "    None -> guess_mime(rel)\n"
            "  }\n",
        )
    elif args.mutant == "fm-split-trims-addressing":
        # the lossy addressing this backend shipped with: fm_split normalised
        # away the whitespace at the edges of a path, so "a.txt " and "a.txt"
        # became one address and a write aimed at either hit whichever row the
        # trimmed spelling matched
        replace_once(
            paths,
            "    strip_slashes(after)\n",
            "    strip_slashes(str.trim(after))\n",
        )
    elif args.mutant == "json-duplicate-members-fail-open":
        # the silent "one of the two wins" the duplicate rule replaced: the
        # per-object scope never records the names it walked past, so every
        # membership test answers no
        replace_once(
            jsonread,
            "            scan_members(src, cursor.next(src, k), map.insert(seen, name, true))\n",
            "            scan_members(src, cursor.next(src, k), seen)\n",
        )
    elif args.mutant == "config-range-falls-back-to-default":
        # the shape this rule replaced: a stated value the bound rejects quietly
        # becomes the default, so a typo and an omission run the same server
        replace_once(
            config,
            "          if n < lo || n > hi {\n"
            '            Err("expected an integer in ${to_string(lo)}..${to_string(hi)}, got ${to_string(n)}")\n',
            "          if n < lo || n > hi {\n            Ok(dflt)\n",
        )
    elif args.mutant == "config-nonnumeric-falls-back-to-default":
        replace_once(
            config,
            '        None -> Err("expected an integer in ${to_string(lo)}..${to_string(hi)}, got \\"$s\\"")\n',
            "        None -> Ok(dflt)\n",
        )
    elif args.mutant == "upload-token-expired-deadline":
        # a token minted already out of date: same 200, same well-formed
        # credential, and nothing on this backend's own paths can tell
        replace_once(
            fm_api,
            "    let token = upload_token(a.qiniu.ak, a.qiniu.sk, a.qiniu.bucket, key, now_s() + a.qiniu.expires)\n",
            "    let token = upload_token(a.qiniu.ak, a.qiniu.sk, a.qiniu.bucket, key, now_s() - a.qiniu.expires)\n",
        )
    # One mutant per write entry, each dropping only that entry's call to the
    # name guard. The guard itself is shared, so a mutant that broke the
    # predicate would turn every one of these cases red at once and could not be
    # owned; dropping call sites one at a time is what shows each door is
    # actually locked rather than standing behind someone else's lock.
    elif args.mutant == "name-guard-fm-create-folder-fail-open":
        replace_once(
            fm_api,
            "          let _n = require_trimmed_names(rel)?\n"
            "          match as_http_with(with_db(a.db.path, a.db.ext, c => mk_folder(c, rel, cur)), conflict_or(500))? {\n",
            "          match as_http_with(with_db(a.db.path, a.db.ext, c => mk_folder(c, rel, cur)), conflict_or(500))? {\n",
        )
    elif args.mutant == "name-guard-fm-rename-fail-open":
        replace_once(
            fm_api,
            "          let _n = require_trimmed_names(new_rel)?\n",
            "",
        )
    elif args.mutant == "name-guard-fm-move-fail-open":
        replace_once(
            fm_api,
            "        let _n = check_relocation_targets(dest, sources, 0)?\n"
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_move(c, sources, dest, cur, 0)), conflict_or(500))?))\n",
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_move(c, sources, dest, cur, 0)), conflict_or(500))?))\n",
        )
    elif args.mutant == "name-guard-fm-copy-fail-open":
        replace_once(
            fm_api,
            "        let _n = check_relocation_targets(dest, sources, 0)?\n"
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_copy(a.qiniu, c, sources, dest, cur, 0)), mutation_failure)?))\n",
            "        Ok(json_ok(as_http_with(with_db(a.db.path, a.db.ext, c => do_copy(a.qiniu, c, sources, dest, cur, 0)), mutation_failure)?))\n",
        )
    elif args.mutant == "name-guard-fm-create-file-fail-open":
        replace_once(
            fm_api,
            "          let _n = require_trimmed_names(rel)?\n"
            "          match as_http_with(do_create_file(a, rel, cur), mutation_failure)? {\n",
            "          match as_http_with(do_create_file(a, rel, cur), mutation_failure)? {\n",
        )
    elif args.mutant == "name-guard-fm-save-fail-open":
        replace_once(
            fm_api,
            "          let _n = require_trimmed_names(rel)?\n"
            "          Ok(json_ok(as_http_with(do_save(a, rel, content), mutation_failure)?))\n",
            "          Ok(json_ok(as_http_with(do_save(a, rel, content), mutation_failure)?))\n",
        )
    elif args.mutant == "name-guard-fm-upload-token-fail-open":
        replace_once(
            fm_api,
            "    let _n = require_trimmed_names(rel)?\n"
            "    let _p = as_http_with(with_db(a.db.path, a.db.ext, c => upload_token_preflight(c, rel)), conflict_or(500))?\n",
            "    let _p = as_http_with(with_db(a.db.path, a.db.ext, c => upload_token_preflight(c, rel)), conflict_or(500))?\n",
        )
    elif args.mutant == "name-guard-fm-register-fail-open":
        replace_once(
            fm_api,
            "          let _n = require_trimmed_names(rel)?\n"
            "          let _preflight = as_http_with(register_preflight(a, rel, key), conflict_or(500))?\n",
            "          let _preflight = as_http_with(register_preflight(a, rel, key), conflict_or(500))?\n",
        )
    elif args.mutant == "name-guard-fm-proxy-upload-fail-open":
        replace_once(
            fm_api,
            "  let _n = require_trimmed_names(rel)?\n"
            "  # the part's declared type is a client-supplied string that ends up in the row\n",
            "  # the part's declared type is a client-supplied string that ends up in the row\n",
        )
    elif args.mutant == "name-guard-webdav-put-fail-open":
        replace_once(
            webdav,
            "    let _n = require_trimmed_names(rel)?\n"
            "    match as_http_with(with_db(a.db.path, a.db.ext, c => put_preflight(c, rel)), conflict_or(500))? {\n",
            "    match as_http_with(with_db(a.db.path, a.db.ext, c => put_preflight(c, rel)), conflict_or(500))? {\n",
        )
    elif args.mutant == "name-guard-webdav-mkcol-fail-open":
        replace_once(
            webdav,
            "    let _n = require_trimmed_names(rel)?\n"
            "    match as_http(with_db(a.db.path, a.db.ext, c => get_row(c, rel)), 500)? {\n",
            "    match as_http(with_db(a.db.path, a.db.ext, c => get_row(c, rel)), 500)? {\n",
        )
    elif args.mutant == "name-guard-webdav-destination-fail-open":
        replace_once(
            webdav,
            "              let _n = require_trimmed_names(dst)?\n",
            "",
        )
    elif args.mutant == "entry-json-mime-passthrough":
        # a stored content type reaches the browser's file manager as written
        replace_once(
            repo,
            '    ("mime_type", if is_dir || o.content_type == "" { JNull } else { JStr(safe_persisted_mime(o.content_type)) }),\n',
            '    ("mime_type", if is_dir || o.content_type == "" { JNull } else { JStr(o.content_type) }),\n',
        )
    elif args.mutant == "copy-mime-passthrough":
        # a copy duplicates the source row's stored type into a brand new row
        replace_once(
            repo,
            "        Some(key) -> insert_file_row(c, destination, key, safe_persisted_mime(row.source.content_type), row.source.size)\n",
            "        Some(key) -> insert_file_row(c, destination, key, row.source.content_type, row.source.size)\n",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
