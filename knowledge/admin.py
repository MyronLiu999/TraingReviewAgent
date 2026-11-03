"""Admin commands for the Brooks Canonical Knowledge Base. This is the ONLY code that writes it.

Run from PowerShell in the project folder:

    .venv\\Scripts\\python -m knowledge.admin status
    .venv\\Scripts\\python -m knowledge.admin verify [--full]
    .venv\\Scripts\\python -m knowledge.admin create-version v2 [--books book_11,book_12] [--note "..."]
    .venv\\Scripts\\python -m knowledge.admin activate v1          (switch / roll back)
    .venv\\Scripts\\python -m knowledge.admin repair v1            (restore tampered snapshot files)
    .venv\\Scripts\\python -m knowledge.admin build-index [v1]     (rebuild the search index; sources untouched)

create-version / activate / repair change what the agent treats as Brooks' canon, so they ask
you to type an authorization phrase (or pass --authorize "AUTHORIZE <command> <version>").
Nothing in the agent runtime imports this module, and the tool registry refuses admin tools.
"""

import argparse
import hashlib
import json
import os
import shutil
import stat
import sys
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone

from knowledge import indexer
from knowledge.manifest import (MANIFEST, MANIFEST_HASH, SCHEMA_VERSION, active_version, index_path,
                                list_versions, load_manifest, sha256_file, verify, version_dir)

# Approved Brooks material: source_id -> (citation tag, title, authority weight, note).
# Weight orders sources when they disagree: 2012 trilogy > 2018 course > 2009 Bar by Bar >
# Manual / Top 10 > third-party encyclopedia.
APPROVED_SOURCES = {
    "book_11": ("Trends", "Trading Price Action: Trends (Al Brooks, 2012)", 1.0, "2012 trilogy"),
    "book_12": ("TR", "Trading Price Action: Trading Ranges (Al Brooks, 2012)", 1.0, "2012 trilogy, ebook reflow"),
    "book_10": ("Rev", "Trading Price Action: Reversals (Al Brooks, 2012)", 1.0, "2012 trilogy"),
    "book_03": ("Course2018", "Al Brooks Price Action course slides (2018, OCR)", 0.85, "OCR text of slides"),
    "book_08": ("BbB", "Reading Price Charts Bar by Bar (Al Brooks, 2009)", 0.8,
                "2009 book; some terms differ from the 2012 trilogy"),
    "book_07": ("Manual", "How to Trade Price Action Manual (Al Brooks)", 0.75, "outline-style manual"),
    "book_01": ("Top10", "Al Brooks 10 best price action patterns", 0.75, "short course handout"),
    "book_14": ("Abbr", "Al Brooks abbreviation list", 0.7, "image only; no extracted text"),
    "book_05": ("Encyc", "Al Brooks chart encyclopedia (third-party compilation)", 0.6,
                "third-party compilation of Brooks charts; use for cases, not definitions"),
}
EXCLUDED_SOURCES = {
    "book_02": "duplicate of book_01",
    "book_04": "machine translation of book_03",
    "book_06": "machine translation of book_07",
    "book_09": "machine translation of book_08",
    "book_13": "scanned Chinese translation; no reliable extracted text",
}
ADMIN_ACTIONS = ("create-version", "activate", "repair")


class AuthorizationError(PermissionError):
    pass


@dataclass(frozen=True)
class AdminAuthorization:
    """Proof that a human typed the authorization phrase for one action on one version."""
    action: str
    version: str


def authorize(action: str, version: str, phrase: str) -> AdminAuthorization:
    expected = f"AUTHORIZE {action} {version}"
    if phrase.strip() != expected:
        raise AuthorizationError(f'Authorization phrase must be exactly: {expected}')
    return AdminAuthorization(action, version)


def _check(auth, action: str, version: str) -> None:
    if not isinstance(auth, AdminAuthorization) or (auth.action, auth.version) != (action, version):
        raise AuthorizationError(f"{action} {version} needs an AdminAuthorization for exactly that action")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _audit(root: str, event: dict) -> None:
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "audit.log"), "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now(), **event}, ensure_ascii=False) + "\n")


def _make_read_only(path: str) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)


def _copy_read_only(src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    _make_read_only(dst)


def _extraction_version(project_dir: str) -> str:
    script = os.path.join(project_dir, "prepare_trading_books.py")
    return f"prepare_trading_books.py@sha256:{sha256_file(script)[:16]}" if os.path.isfile(script) else "unknown"


def create_version(root: str, version: str, auth: AdminAuthorization, *, upload_dir: str,
                   derived_dir: str | None, raw_pdf_dir: str | None, books: list[str] | None = None,
                   note: str = "", activate_now: bool | None = None, project_dir: str = ".",
                   hash_external: bool = True) -> dict:
    """Snapshots approved extracted text (+ derived knowledge files) into a new read-only
    version folder and writes its manifest. Existing versions are never touched."""
    _check(auth, "create-version", version)
    vdir = version_dir(root, version)
    if os.path.exists(vdir):
        raise FileExistsError(f"{vdir} already exists; versions are immutable. Pick a new name.")
    books = books or sorted(APPROVED_SOURCES)
    unknown = [b for b in books if b not in APPROVED_SOURCES]
    if unknown:
        raise ValueError(f"Not approved sources: {unknown}. Add them to APPROVED_SOURCES first.")

    with open(os.path.join(upload_dir, "manifest.json"), encoding="utf-8") as f:
        upload = {b["book_id"]: b for b in json.load(f)}
    parent = None
    try:
        parent = active_version(root)
    except FileNotFoundError:
        pass
    extraction = _extraction_version(project_dir)
    tmp = vdir + ".partial"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    artifacts, sources = [], []

    def add(kind, source_id, src, rel=None, stored="snapshot", pages=None, authority="canonical"):
        if stored == "snapshot":
            _copy_read_only(src, os.path.join(tmp, rel))
            digest, size, path = sha256_file(os.path.join(tmp, rel)), os.path.getsize(src), rel.replace("\\", "/")
        else:
            digest = sha256_file(src) if hash_external else None
            size, path = os.path.getsize(src), os.path.abspath(src)
        artifacts.append({"artifact_id": f"{source_id}/{kind}/{os.path.basename(src)}", "kind": kind,
                          "source_id": source_id, "stored": stored, "path": path, "sha256": digest,
                          "bytes": size, "page_range": pages, "authority": authority,
                          "extraction_version": extraction if kind in ("extracted_text", "page_map", "chart_pdf") else None,
                          "original_location": os.path.abspath(src)})
        return digest

    for book in books:
        info = upload.get(book)
        if not info:
            raise ValueError(f"{book} not found in {upload_dir}/manifest.json")
        tag, title, weight, why = APPROVED_SOURCES[book]
        bdir = os.path.join(upload_dir, book)
        raw = os.path.join(raw_pdf_dir, info["source"]) if raw_pdf_dir else None
        raw_hash = None
        if raw and os.path.isfile(raw):
            raw_hash = add("raw_pdf", book, raw, stored="external", pages=[1, info["pages"]])
        for name in info.get("text_files", []):
            add("extracted_text", book, os.path.join(bdir, name), f"text/{book}/{name}")
        add("page_map", book, os.path.join(bdir, "page_map.json"), f"text/{book}/page_map.json")
        for vf in info.get("visual_files", []):
            path = os.path.join(bdir, vf["name"])
            if os.path.isfile(path):
                add("chart_pdf", book, path, stored="external",
                    pages=[vf["start_original_page"], vf["end_original_page"]])
        import_ts = datetime.fromtimestamp(os.path.getmtime(os.path.join(bdir, "page_map.json")),
                                           timezone.utc).isoformat(timespec="seconds")
        sources.append({"source_id": book, "tag": tag, "title": title, "authority_weight": weight,
                        "note": why, "approved": True, "source_file": info["source"],
                        "source_bytes": info.get("source_bytes"), "pages": info.get("pages"),
                        "source_version": f"sha256:{raw_hash}" if raw_hash else f"bytes:{info.get('source_bytes')}",
                        "import_timestamp": import_ts, "extraction_version": extraction})

    if derived_dir and os.path.isdir(derived_dir):
        for name in sorted(os.listdir(derived_dir)):
            if name.endswith(".md"):
                add("derived_summary", "derived_kb", os.path.join(derived_dir, name), f"derived/{name}",
                    authority="derived")

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "knowledge_version": version,
        "parent_version": parent,
        "created": _now(),
        "note": note,
        "index_version": f"{version}-fts5-1",
        "extraction_version": extraction,
        "authority_rules": {
            "canonical": "extracted text of approved Brooks material; cite as [BROOKS SOURCE]",
            "derived": "AI-synthesized notes built from the canonical text; cite as [DERIVED INTERPRETATION], "
                       "never as Brooks' own words",
        },
        "sources": sources,
        "excluded_sources": EXCLUDED_SOURCES,
        "artifacts": artifacts,
    }
    raw = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
    with open(os.path.join(tmp, MANIFEST), "wb") as f:
        f.write(raw)
    with open(os.path.join(tmp, MANIFEST_HASH), "w", encoding="utf-8") as f:
        f.write(hashlib.sha256(raw).hexdigest() + f"  {MANIFEST}\n")
    _make_read_only(os.path.join(tmp, MANIFEST))
    _make_read_only(os.path.join(tmp, MANIFEST_HASH))
    os.makedirs(os.path.dirname(vdir), exist_ok=True)
    os.rename(tmp, vdir)
    if activate_now or (activate_now is None and parent is None):
        _write_active(root, version)
    _audit(root, {"action": "create-version", "version": version, "parent": parent, "books": books,
                  "artifacts": len(artifacts), "note": note})
    return manifest


def _write_active(root: str, version: str) -> None:
    path = os.path.join(root, "ACTIVE")
    if os.path.exists(path):
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    with open(path, "w", encoding="utf-8") as f:
        f.write(version)
    _make_read_only(path)


def activate(root: str, version: str, auth: AdminAuthorization) -> None:
    """Switches the active version (also how you roll back). Old versions stay untouched."""
    _check(auth, "activate", version)
    load_manifest(root, version)  # must exist and be intact
    before = None
    try:
        before = active_version(root)
    except FileNotFoundError:
        pass
    _write_active(root, version)
    _audit(root, {"action": "activate", "version": version, "previous": before})


def repair(root: str, version: str, auth: AdminAuthorization) -> dict:
    """Restores snapshot files that no longer match the manifest, copying them again from
    their original location, but only if that original still has the recorded hash."""
    _check(auth, "repair", version)
    manifest = load_manifest(root, version)
    fixed, failed = [], []
    for art in manifest["artifacts"]:
        if art["stored"] != "snapshot":
            continue
        dst = os.path.join(version_dir(root, version), art["path"])
        if os.path.isfile(dst) and sha256_file(dst) == art["sha256"]:
            continue
        src = art.get("original_location", "")
        if os.path.isfile(src) and sha256_file(src) == art["sha256"]:
            if os.path.exists(dst):
                os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)
            _copy_read_only(src, dst)
            fixed.append(art["artifact_id"])
        else:
            failed.append(art["artifact_id"])
    _audit(root, {"action": "repair", "version": version, "fixed": fixed, "failed": failed})
    return {"fixed": fixed, "failed": failed}


def build_index(root: str, version: str | None = None) -> dict:
    """(Re)builds the full-text index from the version snapshot. Reads the snapshot only."""
    version = version or active_version(root)
    manifest = load_manifest(root, version)
    stats = indexer.build(version_dir(root, version), manifest, index_path(root, version))
    _audit(root, {"action": "build-index", "version": version, **stats})
    return stats


# ---------------------------------------------------------------- CLI

def _cfg(project_dir: str) -> dict:
    path = os.path.join(project_dir, "config.toml")
    if not os.path.isfile(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _ask_authorization(action: str, version: str, given: str | None) -> AdminAuthorization:
    phrase = given
    if phrase is None:
        if not sys.stdin.isatty():
            raise AuthorizationError("Run this in an interactive terminal or pass --authorize")
        print(f"\nThis changes the Brooks canonical knowledge base ({action} {version}).")
        phrase = input(f'Type "AUTHORIZE {action} {version}" to continue: ')
    return authorize(action, version, phrase)


def main(argv: list[str] | None = None) -> int:
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = _cfg(project_dir)
    ap = argparse.ArgumentParser(prog="python -m knowledge.admin")
    ap.add_argument("--store", default=cfg.get("canonical_store", ""), help="canonical store folder")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("verify"); p.add_argument("version", nargs="?"); p.add_argument("--full", action="store_true")
    p = sub.add_parser("create-version"); p.add_argument("version")
    p.add_argument("--books", help="comma-separated source ids (default: all approved)")
    p.add_argument("--note", default="")
    p.add_argument("--activate", action="store_true", help="make it the active version")
    p.add_argument("--authorize")
    for name in ("activate", "repair"):
        p = sub.add_parser(name); p.add_argument("version"); p.add_argument("--authorize")
    p = sub.add_parser("build-index"); p.add_argument("version", nargs="?")
    args = ap.parse_args(argv)
    root = args.store
    if not root:
        print("Set canonical_store in config.toml or pass --store"); return 2

    if args.cmd == "status":
        try:
            act = active_version(root)
        except FileNotFoundError as e:
            print(e); return 1
        print(f"Store:   {root}\nActive:  {act}\nVersions: {', '.join(list_versions(root))}")
        m = load_manifest(root, act)
        print(f"Created: {m['created']}  parent: {m['parent_version']}  index: {m['index_version']}")
        for s in m["sources"]:
            print(f"  {s['source_id']:8} [{s['tag']}] {s['title']}  ({s['source_version'][:20]})")
        print(f"  excluded: {', '.join(m['excluded_sources'])}")
        print(f"Index:   {'present' if os.path.isfile(index_path(root, act)) else 'MISSING - run build-index'}")
        return 0
    if args.cmd == "verify":
        r = verify(root, args.version, full=args.full)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r["ok"] else 1
    if args.cmd == "build-index":
        print(json.dumps(build_index(root, args.version), indent=2)); return 0
    auth = _ask_authorization(args.cmd, args.version, args.authorize)
    if args.cmd == "create-version":
        upload = cfg.get("knowledge_upload_folder", "")
        m = create_version(root, args.version, auth, upload_dir=upload,
                           derived_dir=cfg.get("knowledge_base_folder") or None,
                           raw_pdf_dir=os.path.join(project_dir, cfg.get("docs_folder", "docs")),
                           books=args.books.split(",") if args.books else None, note=args.note,
                           activate_now=True if args.activate else None, project_dir=project_dir)
        print(f"Created {args.version}: {len(m['artifacts'])} artifacts. Now run: build-index {args.version}")
    elif args.cmd == "activate":
        activate(root, args.version, auth); print(f"Active version is now {args.version}")
    elif args.cmd == "repair":
        print(json.dumps(repair(root, args.version, auth), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
