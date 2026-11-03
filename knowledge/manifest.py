"""Canonical store layout, manifests and integrity checks. Nothing in this module writes.

Layout of the canonical store (kept outside the Git repository):

    <canonical_store>/
        ACTIVE                         name of the active version, e.g. "v1"
        audit.log                      append-only log of admin operations
        versions/<v>/manifest.json     sources + artifacts with SHA-256 hashes
        versions/<v>/manifest.sha256   hash of manifest.json, so edits to the manifest are detected
        versions/<v>/text/<book_id>/   snapshot of the extracted book text + page_map.json (read-only files)
        versions/<v>/derived/          snapshot of the AI-synthesized knowledge files (DERIVED, not Brooks' words)
        index/<v>.sqlite               full-text index, rebuildable from the snapshot at any time

Writes happen only in knowledge/admin.py, behind an explicit AdminAuthorization.
"""

import hashlib
import json
import os

MANIFEST = "manifest.json"
MANIFEST_HASH = "manifest.sha256"
SCHEMA_VERSION = 1


class IntegrityError(RuntimeError):
    """The canonical store does not match its manifest."""


def sha256_file(path: str, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()


def is_inside(path: str, root: str) -> bool:
    """True if `path` is `root` or anything below it (case-insensitive on Windows)."""
    p = os.path.normcase(os.path.realpath(os.path.abspath(path)))
    r = os.path.normcase(os.path.realpath(os.path.abspath(root)))
    return p == r or p.startswith(r.rstrip("\\/") + os.sep)


def active_version(root: str) -> str:
    path = os.path.join(root, "ACTIVE")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"No active knowledge version in {root}. "
                                "Create one with: python -m knowledge.admin create-version v1")
    with open(path, encoding="utf-8") as f:
        return f.read().strip()


def version_dir(root: str, version: str) -> str:
    return os.path.join(root, "versions", version)


def index_path(root: str, version: str) -> str:
    return os.path.join(root, "index", f"{version}.sqlite")


def list_versions(root: str) -> list[str]:
    base = os.path.join(root, "versions")
    return sorted(os.listdir(base)) if os.path.isdir(base) else []


def load_manifest(root: str, version: str) -> dict:
    """Loads a version's manifest after checking it against its stored hash."""
    vdir = version_dir(root, version)
    path = os.path.join(vdir, MANIFEST)
    with open(path, "rb") as f:
        raw = f.read()
    with open(os.path.join(vdir, MANIFEST_HASH), encoding="utf-8") as f:
        expected = f.read().split()[0]
    if hashlib.sha256(raw).hexdigest() != expected:
        raise IntegrityError(f"{path} was modified after it was created")
    return json.loads(raw)


def artifact_path(root: str, version: str, art: dict) -> str:
    """Absolute path of an artifact: snapshot files live in the version folder, external
    files (raw PDFs, chart PDFs) stay where they were imported from."""
    if art["stored"] == "snapshot":
        return os.path.join(version_dir(root, version), art["path"])
    return art["path"]


def verify(root: str, version: str | None = None, full: bool = False) -> dict:
    """Recomputes hashes and compares them with the manifest.

    Quick mode checks the snapshot (extracted text, page maps, derived files: ~30 MB).
    Full mode also re-hashes the external raw PDFs and chart PDFs (several GB)."""
    version = version or active_version(root)
    report = {"version": version, "ok": True, "checked": 0, "mismatched": [], "missing": [],
              "skipped_external": 0, "manifest_ok": True}
    try:
        manifest = load_manifest(root, version)
    except (IntegrityError, FileNotFoundError, OSError) as e:
        report.update(ok=False, manifest_ok=False, error=str(e))
        return report
    for art in manifest["artifacts"]:
        if art["stored"] != "snapshot" and not full:
            report["skipped_external"] += 1
            continue
        path = artifact_path(root, version, art)
        if not os.path.isfile(path):
            report["missing"].append(art["artifact_id"])
        elif sha256_file(path) != art["sha256"]:
            report["mismatched"].append(art["artifact_id"])
        report["checked"] += 1
    report["ok"] = not report["mismatched"] and not report["missing"]
    return report


def snapshot_files(root: str, version: str) -> list[str]:
    """Every file inside a version folder (used by tests to prove nothing changed)."""
    out = []
    for dirpath, _, files in os.walk(version_dir(root, version)):
        out += [os.path.join(dirpath, f) for f in files]
    return sorted(out)
