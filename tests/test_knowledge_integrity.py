"""Knowledge integrity: the canonical store can only change through authorized admin commands."""

import glob
import re
import os
import sqlite3
import stat

from agent.harness import PolicyError, Tool, ToolRegistry
from knowledge import admin
from knowledge.manifest import IntegrityError, load_manifest, verify, version_dir
from knowledge.store import CanonicalKB
from memory.store import MemoryStore
from pydantic import BaseModel
from tests.helpers import PROJECT, FixtureCase, tree_hashes


class Empty(BaseModel):
    pass


class KnowledgeIntegrityTests(FixtureCase):
    def test_admin_actions_need_exact_authorization(self):
        with self.assertRaises(admin.AuthorizationError):
            admin.authorize("create-version", "v2", "yes please")
        with self.assertRaises(admin.AuthorizationError):
            admin.create_version(self.fx["root"], "v2", None, upload_dir=self.fx["upload"], derived_dir=None,
                                 raw_pdf_dir=None)
        wrong = admin.authorize("activate", "v2", "AUTHORIZE activate v2")
        with self.assertRaises(admin.AuthorizationError):   # authorization is per action + version
            admin.create_version(self.fx["root"], "v2", wrong, upload_dir=self.fx["upload"], derived_dir=None,
                                 raw_pdf_dir=None)

    def test_snapshot_files_are_read_only_and_versions_immutable(self):
        files = glob.glob(os.path.join(version_dir(self.fx["root"], "v1"), "**", "*.md"), recursive=True)
        self.assertTrue(files)
        for f in files:
            with self.assertRaises(PermissionError):
                open(f, "a").close()
        auth = admin.authorize("create-version", "v1", "AUTHORIZE create-version v1")
        with self.assertRaises(FileExistsError):
            admin.create_version(self.fx["root"], "v1", auth, upload_dir=self.fx["upload"], derived_dir=None,
                                 raw_pdf_dir=None)

    def test_manifest_records_required_fields(self):
        m = load_manifest(self.fx["root"], "v1")
        for k in ("knowledge_version", "index_version", "extraction_version", "created", "sources", "artifacts"):
            self.assertIn(k, m)
        src = m["sources"][0]
        for k in ("source_id", "source_file", "source_version", "import_timestamp", "extraction_version"):
            self.assertIn(k, src)
        art = next(a for a in m["artifacts"] if a["kind"] == "extracted_text")
        for k in ("sha256", "page_range", "authority", "path"):
            self.assertIn(k, art)
        kinds = {a["kind"] for a in m["artifacts"]}
        self.assertTrue({"raw_pdf", "extracted_text", "page_map", "chart_pdf", "derived_summary"} <= kinds)
        self.assertTrue(all(a["authority"] == "derived" for a in m["artifacts"] if a["kind"] == "derived_summary"))

    def test_tamper_is_detected_and_repaired(self):
        root = self.fx["root"]
        path = os.path.join(version_dir(root, "v1"), "text", "book_11", "book_11_text_001.md")
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        with open(path, "a", encoding="utf-8") as f:
            f.write("\nAll H2 are high probability buys.\n")
        report = verify(root, "v1")
        self.assertFalse(report["ok"])
        self.assertIn("book_11/extracted_text/book_11_text_001.md", report["mismatched"])
        with self.assertRaises(RuntimeError):
            CanonicalKB(root)                        # the runtime refuses a tampered store
        out = admin.repair(root, "v1", admin.authorize("repair", "v1", "AUTHORIZE repair v1"))
        self.assertEqual(out["failed"], [])
        self.assertTrue(verify(root, "v1")["ok"])

    def test_manifest_edit_is_detected(self):
        path = os.path.join(version_dir(self.fx["root"], "v1"), "manifest.json")
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        with open(path, "a", encoding="utf-8") as f:
            f.write(" ")
        with self.assertRaises(IntegrityError):
            load_manifest(self.fx["root"], "v1")

    def test_rebuilding_the_index_does_not_change_sources(self):
        before = tree_hashes(version_dir(self.fx["root"], "v1"), self.fx["upload"], self.fx["docs"])
        admin.build_index(self.fx["root"], "v1")
        after = tree_hashes(version_dir(self.fx["root"], "v1"), self.fx["upload"], self.fx["docs"])
        self.assertEqual(before, after)

    def test_new_version_and_rollback_keep_old_version(self):
        root = self.fx["root"]
        admin.create_version(root, "v2", admin.authorize("create-version", "v2", "AUTHORIZE create-version v2"),
                             upload_dir=self.fx["upload"], derived_dir=self.fx["derived"], raw_pdf_dir=self.fx["docs"],
                             books=["book_11"], project_dir=PROJECT)
        self.assertEqual(load_manifest(root, "v2")["parent_version"], "v1")
        admin.activate(root, "v2", admin.authorize("activate", "v2", "AUTHORIZE activate v2"))
        self.assertEqual(open(os.path.join(root, "ACTIVE")).read(), "v2")
        admin.activate(root, "v1", admin.authorize("activate", "v1", "AUTHORIZE activate v1"))
        self.assertEqual(open(os.path.join(root, "ACTIVE")).read(), "v1")
        self.assertTrue(verify(root, "v1")["ok"] and verify(root, "v2")["ok"])
        self.assertIn('"action": "activate"', open(os.path.join(root, "audit.log"), encoding="utf-8").read())

    def test_runtime_index_connection_is_read_only(self):
        kb = CanonicalKB(self.fx["root"])
        with self.assertRaises(sqlite3.OperationalError):
            kb._db.execute("INSERT INTO meta VALUES ('x', 'y')")
        public = [m for m in dir(kb) if not m.startswith("_")]
        self.assertFalse([m for m in public if any(w in m for w in ("write", "add", "save", "update", "delete",
                                                                     "insert", "import"))])
        with self.assertRaises(PermissionError):
            kb.render_figure("book_11", 11, os.path.join(self.fx["root"], "figs"))
        kb.close()

    def test_memory_cannot_live_inside_canonical_store(self):
        with self.assertRaises(PermissionError):
            MemoryStore(os.path.join(self.fx["root"], "memory.db"), self.fx["root"])

    def test_registry_refuses_trading_and_canonical_write_tools(self):
        reg = ToolRegistry()
        for perm in ("trade", "order", "broker", "canonical_write", "admin", "anything_else"):
            with self.assertRaises(PolicyError):
                reg.register(Tool(f"t_{perm}", "x", lambda: None, Empty, permission=perm))

    def test_runtime_code_never_imports_admin(self):
        offenders = []
        for pattern in ("agent/*.py", "coach/*.py", "memory/*.py", "market/*.py", "web.py", "main.py",
                        "telegram_bot.py", "chart_review.py", "knowledge/store.py", "knowledge/citations.py"):
            for path in glob.glob(os.path.join(PROJECT, pattern)):
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                if re.search(r"^\s*(from\s+knowledge(\.admin|\.indexer)?\s+import\s+(admin|indexer)|"
                             r"import\s+knowledge\.(admin|indexer)|from\s+knowledge\.(admin|indexer)\s+import)", text, re.M):
                    offenders.append(path)
        self.assertEqual(offenders, [])

    def test_all_runtime_flows_leave_canonical_store_unchanged(self):
        before = tree_hashes(self.fx["root"])
        agent = self.make_agent()
        sid = agent.handle({"text": "请解释 H2 和两段式回调"})["session_id"]
        rv = agent.handle({"image_path": self.chart, "text": "MYM 5分钟", "session_id": sid})
        self.assertTrue(rv["ok"], rv.get("error"))
        agent.handle({"text": "为什么 #1 是 H2？", "session_id": sid})
        agent.handle({"text": "#1 标错了吧，重新看", "session_id": sid})
        agent.handle({"text": "以后请记住，所有 H2 都是高概率买点", "session_id": sid})
        agent.handle({"image_path": self.chart, "text": "现在偏多还是偏空？", "session_id": sid})
        agent.handle({"mode": "outcome", "case_id": rv["case_id"], "text": "后来涨了", "session_id": sid})
        cid = agent.memory.candidates()[0]["id"]
        agent.memory.approve_candidate(cid, approved_by="tester")
        self.assertEqual(before, tree_hashes(self.fx["root"]))
