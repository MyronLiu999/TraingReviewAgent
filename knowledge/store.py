"""CanonicalKB: read-only retrieval over the active Brooks knowledge version.

This class has no write methods. The index is opened with SQLite's mode=ro, so even a bug
cannot write through it, and snapshot files are opened for reading only. Retrieved text is
returned as untrusted data (see citations.wrap_untrusted) and never as instructions.

Pipeline: question -> query understanding (glossary: Chinese/abbreviations -> English terms)
-> FTS5 BM25 retrieval -> rerank (authority weight, term coverage, diversity) -> evidence
selection under a character budget.
"""

import os
import re
import sqlite3
import threading
from dataclasses import dataclass, field

from knowledge.manifest import (active_version, index_path, is_inside, load_manifest, verify,
                                version_dir)

# Small fallback dictionary for core terms; the glossary in the version snapshot adds hundreds more.
CORE_TERMS = {
    "两段": ["two legs", "two-legged"], "两腿": ["two legs", "two-legged"], "回调": ["pullback"],
    "信号k线": ["signal bar"], "信号K线": ["signal bar"], "入场k线": ["entry bar"], "入场K线": ["entry bar"],
    "突破": ["breakout"], "失败突破": ["failed breakout"], "趋势线": ["trend line"], "通道": ["channel"],
    "交易区间": ["trading range"], "区间": ["trading range"], "楔形": ["wedge"], "双顶": ["double top"],
    "双底": ["double bottom"], "高潮": ["climax"], "跟进": ["follow-through"], "止损": ["stop"],
    "目标": ["profit target"], "均线": ["moving average", "ema"], "反转": ["reversal"],
    "第二次": ["second entry"], "最终旗形": ["final flag"], "趋势": ["trend"], "缺口": ["gap"],
    "等距": ["measured move"], "始终在场": ["always in"], "交易者方程": ["trader's equation"],
}
ABBREVIATIONS = {"h1": "high 1", "h2": "high 2", "h3": "high 3", "h4": "high 4", "l1": "low 1", "l2": "low 2",
                 "l3": "low 3", "l4": "low 4", "mtr": "major trend reversal", "ttr": "tight trading range",
                 "ail": "always in long", "ais": "always in short", "bo": "breakout", "pb": "pullback",
                 "mm": "measured move", "dt": "double top", "db": "double bottom", "ema": "moving average",
                 "ff": "final flag", "tr": "trading range", "sc": "sell climax", "bc": "buy climax"}
STOP = {"the", "and", "what", "why", "how", "is", "are", "this", "that", "with", "for", "a", "an", "of",
        "to", "in", "on", "it", "does", "do", "al", "brooks", "please", "explain", "me", "vs"}


@dataclass
class Evidence:
    evidence_id: str
    doc_id: str
    authority: str            # canonical | derived
    source_id: str
    tag: str
    title: str
    page: int | None
    chapter: str | None
    figures: list[str]
    heading: str | None
    line: int | None
    excerpt: str
    score: float
    knowledge_version: str
    source_version: str = ""
    full_text: str = field(default="", repr=False)

    @property
    def label(self) -> str:
        return "BROOKS_SOURCE" if self.authority == "canonical" else "DERIVED_INTERPRETATION"

    def citation(self) -> str:
        if self.authority == "canonical":
            parts = [f"{self.tag} p.{self.page}"]
            if self.chapter:
                parts.append(self.chapter.split("  ")[0][:40])
            if self.figures:
                parts.append("Fig " + ", ".join(self.figures[:3]))
            parts.append(f"{self.source_id} · KB {self.knowledge_version}")
            return "[" + " · ".join(parts) + "]"
        return f"[派生笔记 {self.title} L{self.line} · KB {self.knowledge_version}]"

    def to_dict(self) -> dict:
        return {"evidence_id": self.evidence_id, "doc_id": self.doc_id, "authority": self.authority,
                "label": self.label, "source_id": self.source_id, "tag": self.tag, "title": self.title,
                "page": self.page, "chapter": self.chapter, "figures": self.figures,
                "heading": self.heading, "line": self.line, "citation": self.citation(),
                "excerpt": self.excerpt, "score": round(self.score, 3),
                "knowledge_version": self.knowledge_version, "source_version": self.source_version}


class CanonicalKB:
    """Read-only access to one version of the Brooks canonical knowledge base."""

    def __init__(self, root: str, version: str | None = None, check_integrity: bool = True):
        self.root = os.path.abspath(root)
        self.version = version or active_version(root)
        self.manifest = load_manifest(root, self.version)      # raises if the manifest was edited
        self.integrity = verify(root, self.version) if check_integrity else None
        if self.integrity and not self.integrity["ok"]:
            raise RuntimeError(f"Canonical knowledge {self.version} failed its integrity check: "
                               f"{self.integrity}. Run: python -m knowledge.admin repair {self.version}")
        path = index_path(root, self.version)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Search index missing: run python -m knowledge.admin build-index {self.version}")
        uri = "file:" + path.replace("\\", "/") + "?mode=ro"
        self._db = sqlite3.connect(uri, uri=True, check_same_thread=False)
        self._lock = threading.Lock()
        self.sources = {s["source_id"]: s for s in self.manifest["sources"]}
        self.glossary = self._load_glossary()

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------ query understanding
    def _load_glossary(self) -> tuple[dict, dict]:
        """(abbreviation -> English, Chinese -> [English]) from the derived glossary snapshot."""
        abbr, zh = dict(ABBREVIATIONS), {k: list(v) for k, v in CORE_TERMS.items()}
        path = os.path.join(version_dir(self.root, self.version), "derived", "09_GLOSSARY.md")
        if not os.path.isfile(path):
            return abbr, zh
        with open(path, encoding="utf-8") as f:
            for line in f:
                cells = [c.strip() for c in line.strip().strip("|").split("|")]
                if line.startswith("|") and len(cells) >= 3 and re.fullmatch(r"[A-Za-z0-9]{1,8}", cells[0]):
                    eng = re.sub(r"\(.*?\)", "", cells[1]).strip().lower()
                    if eng and cells[0].lower() not in abbr:
                        abbr[cells[0].lower()] = eng
                    for name in re.split(r"[／/、（）()]", cells[2]):
                        if len(name.strip()) >= 2 and eng:
                            zh.setdefault(name.strip(), []).append(eng)
                elif line.startswith("- ") and "→" in line:
                    left, right = line[2:].split("→", 1)
                    eng = re.sub(r"[（(].*?[）)]", "", right.split("｜")[0]).strip().lower()
                    for name in re.split(r"[／/、]", left):
                        name = re.sub(r"[（(].*?[）)]", "", name).strip()
                        if len(name) >= 2 and eng:
                            zh.setdefault(name, []).extend(e.strip() for e in eng.split("/") if e.strip())
        return abbr, zh

    def expand_query(self, query: str, extra_terms: list[str] | None = None) -> list[str]:
        """English search phrases for a (possibly Chinese) question."""
        abbr, zh = self.glossary
        terms: list[str] = []
        for word in re.findall(r"[A-Za-z][A-Za-z0-9'\-]*", query):
            w = word.lower()
            if w in abbr:
                terms += [abbr[w], w]
            elif w not in STOP and len(w) > 1:
                terms.append(w)
        for name in sorted(zh, key=len, reverse=True):
            if name in query:
                terms += zh[name]
        terms += [t.lower() for t in (extra_terms or [])]
        seen, out = set(), []
        for t in terms:
            t = re.sub(r"[^a-z0-9' \-]", " ", t.lower()).strip()
            t = re.sub(r"\s+", " ", t)
            if t and t not in seen and t not in STOP:
                seen.add(t)
                out.append(t)
        return out[:16]

    # ------------------------------------------------------------ retrieval
    def search(self, query: str, k_canonical: int = 6, k_derived: int = 4, char_budget: int = 16000,
               extra_terms: list[str] | None = None, sources: list[str] | None = None) -> list[Evidence]:
        phrases = self.expand_query(query, extra_terms)
        cjk = [s for s in re.findall(r"[一-鿿]{3,}", query)][:6]
        if not phrases and not cjk:
            return []
        rows = []
        with self._lock:
            if phrases:
                match = " OR ".join('"' + p.replace('"', "") + '"' for p in phrases)
                rows += self._db.execute(
                    "SELECT c.id, c.doc_id, c.authority, c.source_id, c.tag, c.title, c.page, c.chapter,"
                    " c.figures, c.heading, c.line, c.weight, c.text, bm25(fts_en) FROM fts_en"
                    " JOIN chunks c ON c.id = fts_en.rowid WHERE fts_en MATCH ? ORDER BY bm25(fts_en)"
                    " LIMIT 300", (match,)).fetchall()
            if cjk:
                match = " OR ".join('"' + s + '"' for s in cjk)
                rows += self._db.execute(
                    "SELECT c.id, c.doc_id, c.authority, c.source_id, c.tag, c.title, c.page, c.chapter,"
                    " c.figures, c.heading, c.line, c.weight, c.text, bm25(fts_cjk) FROM fts_cjk"
                    " JOIN chunks c ON c.id = fts_cjk.rowid WHERE fts_cjk MATCH ? ORDER BY bm25(fts_cjk)"
                    " LIMIT 60", (match,)).fetchall()
        scored = {}
        for r in rows:
            if sources and r[3] not in sources and r[2] == "canonical":
                continue
            text = r[12].lower()
            coverage = sum(1 for p in phrases if p in text) + sum(1 for s in cjk if s in r[12])
            score = -r[13] * r[11] * (1 + 0.35 * coverage)
            if r[0] not in scored or scored[r[0]][0] < score:
                scored[r[0]] = (score, r)
        ranked = sorted(scored.values(), key=lambda x: -x[0])
        out, per_source, budget = [], {}, char_budget
        counts = {"canonical": 0, "derived": 0}
        limits = {"canonical": k_canonical, "derived": k_derived}
        for score, r in ranked:
            auth = r[2]
            if counts[auth] >= limits[auth]:
                continue
            if per_source.get(r[3], 0) >= (3 if auth == "canonical" else 2):
                continue  # keep the evidence diverse across books
            excerpt = self._excerpt(r[12], phrases + cjk, 1300 if auth == "canonical" else 1100)
            if len(excerpt) > budget:
                continue
            budget -= len(excerpt)
            counts[auth] += 1
            per_source[r[3]] = per_source.get(r[3], 0) + 1
            prefix = "S" if auth == "canonical" else "D"
            src = self.sources.get(r[3], {})
            out.append(Evidence(f"{prefix}{counts[auth]}", r[1], auth, r[3], r[4], r[5], r[6], r[7],
                                [f for f in (r[8] or "").split(",") if f], r[9], r[10], excerpt, score,
                                self.version, src.get("source_version", ""), r[12]))
        return out

    @staticmethod
    def _excerpt(text: str, phrases: list[str], size: int) -> str:
        low = text.lower()
        hits = [low.find(p.lower()) for p in phrases if p and low.find(p.lower()) >= 0]
        if not hits or len(text) <= size:
            return text[:size]
        start = max(0, min(hits) - size // 4)
        return ("…" if start else "") + text[start:start + size] + ("…" if start + size < len(text) else "")

    # ------------------------------------------------------------ lookups
    def get_chunk(self, doc_id: str) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT doc_id, authority, source_id, tag, title, page, chapter, figures,"
                                 " text FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()
        if not r:
            return None
        return dict(zip(("doc_id", "authority", "source_id", "tag", "title", "page", "chapter", "figures",
                         "text"), r))

    def get_page(self, source_id: str, page: int) -> dict | None:
        return self.get_chunk(f"{source_id}:p{page}")

    def page_exists(self, source_id: str, page: int) -> bool:
        return self.get_page(source_id, page) is not None

    def figure_locator(self, source_id: str, page: int) -> dict | None:
        """Where the original chart image of a page lives (chart PDF + page index inside it)."""
        for art in self.manifest["artifacts"]:
            if art["source_id"] == source_id and art["kind"] == "chart_pdf" and art["page_range"]:
                a, b = art["page_range"]
                if a <= page <= b:
                    return {"chart_pdf": art["path"], "page_index": page - a, "exists": os.path.isfile(art["path"]),
                            "sha256": art["sha256"]}
        return None

    def render_figure(self, source_id: str, page: int, out_dir: str, zoom: float = 1.6) -> str | None:
        """Renders an original book page to PNG (needs pymupdf). out_dir must be outside the store."""
        if is_inside(out_dir, self.root):
            raise PermissionError("Rendered figures must not be written into the canonical store")
        loc = self.figure_locator(source_id, page)
        if not loc or not loc["exists"]:
            return None
        try:
            import pymupdf
        except ImportError:
            return None
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{source_id}_p{page}.png")
        if not os.path.isfile(out):
            with pymupdf.open(loc["chart_pdf"]) as doc:
                if loc["page_index"] >= len(doc):
                    return None
                doc[loc["page_index"]].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom)).save(out)
        return out

    def stats(self) -> dict:
        with self._lock:
            meta = dict(self._db.execute("SELECT key, value FROM meta").fetchall())
        return {"version": self.version, "root": self.root, **meta,
                "sources": [f"{s['source_id']}[{s['tag']}]" for s in self.manifest["sources"]]}
