"""Builds the SQLite FTS5 index for one canonical version. Imported only by knowledge/admin.py.

Reads the version snapshot (never the originals) and writes index/<version>.sqlite:
- canonical chunks = one per original PDF page of the extracted Brooks text (page, chapter, figures)
- derived chunks   = one per heading section of the AI-synthesized knowledge files
The index is a derived artifact: deleting and rebuilding it never changes a source file.
"""

import json
import os
import re
import sqlite3
from datetime import datetime, timezone

PAGE_RE = re.compile(r"^## ORIGINAL PDF PAGE (\d+)\s*$", re.M)
CHAPTER_RE = re.compile(r"^[ \t]*(?:C H A P T E R|CHAPTER|Chapter)[ \t]*((?:\d ?)+)\.?[ \t]*$", re.M)
FIGURE_RE = re.compile(r"^\s*(?:FIGURE|Figure)\s+([A-Z]{0,3}\d*\.\d+)", re.M)
HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    doc_id TEXT UNIQUE NOT NULL,
    authority TEXT NOT NULL,          -- canonical | derived
    source_id TEXT NOT NULL,
    tag TEXT, title TEXT, file TEXT,
    page INTEGER, chapter TEXT, figures TEXT,
    heading TEXT, line INTEGER,
    weight REAL NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX chunks_page ON chunks(source_id, page);
CREATE VIRTUAL TABLE fts_en USING fts5(text, content='chunks', content_rowid='id',
                                       tokenize='porter unicode61');
CREATE VIRTUAL TABLE fts_cjk USING fts5(text, content='chunks', content_rowid='id', tokenize='trigram');
"""


def clean(text: str) -> str:
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)          # re-join words hyphenated at line ends
    text = text.replace("ﬁ", "fi").replace("ﬂ", "fl")
    return re.sub(r"[ \t]+", " ", text).strip()


def chapter_heading(page_text: str) -> str | None:
    matches = CHAPTER_RE.findall(page_text)
    if len(matches) != 1:            # 0 = no chapter start; 2+ = table of contents
        return None
    m = CHAPTER_RE.search(page_text)
    # the title is the block of lines after the heading, ended by a blank line (the body follows);
    # taking a fixed number of lines pulled in body text such as a drop-cap fragment
    title, lines = [], page_text[m.end():].splitlines()
    for ln in lines:
        if ln.strip():
            title.append(ln.strip())
        elif title:
            break
        if len(title) == 6:
            break
    title = re.sub(r"(\w)- (\w)", r"\1\2", " ".join(title))        # "Ex- isting" -> "Existing"
    return re.sub(r"\s+", " ", f"Ch {matches[0].replace(' ', '')} {title}")[:90].strip()


def page_weight(text: str) -> float:
    """Down-weights table-of-contents, index and near-empty pages."""
    if len(text) < 200:
        return 0.3
    digits = sum(c.isdigit() for c in text) / max(1, len(text))
    if digits > 0.12 or len(re.findall(r"(?im)^\s*chapter\s*\d", text)) >= 3:
        return 0.3
    if text.count("See also") >= 2:      # back-of-book index pages
        return 0.15
    return 1.0


def canonical_chunks(vdir: str, manifest: dict):
    sources = {s["source_id"]: s for s in manifest["sources"]}
    for art in manifest["artifacts"]:
        if art["kind"] != "extracted_text" or art["stored"] != "snapshot":
            continue
        src = sources[art["source_id"]]
        with open(os.path.join(vdir, art["path"]), encoding="utf-8") as f:
            raw = f.read()
        parts = PAGE_RE.split(raw)
        chapter = None
        for i in range(1, len(parts) - 1, 2):
            page, body = int(parts[i]), parts[i + 1].strip().strip("-").strip()
            chapter = chapter_heading(body) or chapter
            text = clean(body)
            if not text:
                continue
            figs = sorted(set(FIGURE_RE.findall(body)), key=lambda s: [int(x) if x.isdigit() else x
                                                                       for x in re.split(r"\.", s)])
            yield {"doc_id": f"{src['source_id']}:p{page}", "authority": "canonical",
                   "source_id": src["source_id"], "tag": src["tag"], "title": src["title"],
                   "file": art["path"], "page": page, "chapter": chapter, "figures": ",".join(figs),
                   "heading": None, "line": None,
                   "weight": src["authority_weight"] * page_weight(text), "text": text}


def derived_chunks(vdir: str, manifest: dict, max_chars: int = 3500):
    for art in manifest["artifacts"]:
        if art["kind"] != "derived_summary":
            continue
        name = os.path.basename(art["path"])
        with open(os.path.join(vdir, art["path"]), encoding="utf-8") as f:
            lines = f.read().splitlines()
        path: list[str] = []
        buf: list[str] = []
        start = 1

        def flush(end_line):
            text = "\n".join(buf).strip()
            if len(text) < 40:
                return
            for n, piece in enumerate(_split(text, max_chars)):
                yield {"doc_id": f"KB:{name}#L{start}" + (f".{n}" if n else ""), "authority": "derived",
                       "source_id": "derived_kb", "tag": "KB", "title": name, "file": art["path"],
                       "page": None, "chapter": None, "figures": "",
                       "heading": " > ".join(path)[-160:], "line": start, "weight": 0.9, "text": piece}

        for i, line in enumerate(lines, 1):
            m = HEADING_RE.match(line)
            if m:
                yield from flush(i)
                level = len(m.group(1))
                path = path[:level - 1] + [m.group(2).strip()]
                buf, start = [line], i
            else:
                buf.append(line)
        yield from flush(len(lines))


def _split(text: str, max_chars: int):
    if len(text) <= max_chars:
        yield text
        return
    cur = ""
    for para in text.split("\n"):
        if len(cur) + len(para) > max_chars and cur:
            yield cur
            cur = ""
        cur += para + "\n"
    if cur.strip():
        yield cur


def build(vdir: str, manifest: dict, out_path: str) -> dict:
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    counts = {"canonical": 0, "derived": 0}
    cols = ("doc_id", "authority", "source_id", "tag", "title", "file", "page", "chapter", "figures",
            "heading", "line", "weight", "text")
    for gen in (canonical_chunks(vdir, manifest), derived_chunks(vdir, manifest)):
        for ch in gen:
            cur = db.execute(f"INSERT INTO chunks ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                             [ch[c] for c in cols])
            db.execute("INSERT INTO fts_en(rowid, text) VALUES (?, ?)", (cur.lastrowid, ch["text"]))
            if ch["authority"] == "derived":   # Chinese-heavy notes also get trigram (CJK) search
                db.execute("INSERT INTO fts_cjk(rowid, text) VALUES (?, ?)", (cur.lastrowid, ch["text"]))
            counts[ch["authority"]] += 1
    meta = {"knowledge_version": manifest["knowledge_version"], "index_version": manifest["index_version"],
            "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "chunks": json.dumps(counts)}
    db.executemany("INSERT INTO meta VALUES (?, ?)", meta.items())
    db.commit()
    db.execute("INSERT INTO fts_en(fts_en) VALUES ('optimize')")
    db.commit()
    db.close()
    os.replace(tmp, out_path)
    return {"index": out_path, **counts}
