"""MemoryStore: every non-canonical thing the coach remembers, in one SQLite file.

Layers (tables):
    sessions / messages        short-term: current chart, case, annotation, open questions (resumable)
    profile                    semantic learning memory: CONFIRMED preferences / difficulties only
    procedural_rules           APPROVED personal workflow rules only
    candidates                 candidate insights: proposed by the agent or the user, never trusted
                               until a human approves them (approve -> profile or procedural_rules)
    concept_stats              objective counters (how often a concept was asked about / practised)
    cases                      episodic case memory; the pre-outcome hypothesis is IMMUTABLE (trigger)
    annotations                versioned annotation registry per case (corrections add versions)
    case_feedback              user feedback on a case
    case_outcomes              what happened afterwards; stored apart from the hypothesis
    discussion                 the ongoing conversation about one case (questions, challenges, answers)
    case_revisions             later changes of the coach's judgment (context, always-in, bias, plan);
                               the original hypothesis stays as it was, current_view() applies these
    audit                      every memory write, with its source

Every row records where it came from (source_type / source_ref). Users can list, edit
and delete anything here; nothing here is ever copied into the canonical knowledge base.
"""

import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone

from knowledge.manifest import is_inside

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, created TEXT, updated TEXT,
    channel TEXT, state TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, ts TEXT,
    role TEXT, mode TEXT, content TEXT, refs TEXT);
CREATE TABLE IF NOT EXISTS profile (id INTEGER PRIMARY KEY AUTOINCREMENT, category TEXT, content TEXT,
    source_type TEXT, source_ref TEXT, evidence TEXT, confirmed_by TEXT, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS procedural_rules (id INTEGER PRIMARY KEY AUTOINCREMENT, rule TEXT,
    source_type TEXT, source_ref TEXT, approved_by TEXT, created TEXT, updated TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS candidates (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, content TEXT,
    rationale TEXT, source_type TEXT, source_ref TEXT, status TEXT DEFAULT 'candidate', created TEXT,
    decided TEXT, decided_note TEXT, promoted_to TEXT);
CREATE TABLE IF NOT EXISTS concept_stats (concept TEXT PRIMARY KEY, times_asked INTEGER DEFAULT 0,
    times_practised INTEGER DEFAULT 0, last_seen TEXT, user_status TEXT DEFAULT 'unrated');
CREATE TABLE IF NOT EXISTS cases (case_id TEXT PRIMARY KEY, created TEXT, analysis_timestamp TEXT,
    market_timestamp TEXT, mode TEXT, symbol TEXT, timeframe TEXT, original_path TEXT,
    annotated_path TEXT, image_w INTEGER, image_h INTEGER, market_context TEXT, setup_classification TEXT,
    initial_hypothesis TEXT, supporting_evidence TEXT, data_quality TEXT, task_id TEXT, note TEXT,
    review_notes TEXT DEFAULT '');
CREATE TRIGGER IF NOT EXISTS cases_hypothesis_immutable BEFORE UPDATE OF
    created, analysis_timestamp, market_timestamp, mode, original_path, image_w, image_h, market_context,
    setup_classification, initial_hypothesis, supporting_evidence, data_quality, task_id ON cases
BEGIN SELECT RAISE(ABORT, 'case analysis records are immutable; add feedback, outcomes or annotation versions'); END;
CREATE TABLE IF NOT EXISTS annotations (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT,
    annotation_id TEXT, version INTEGER, type TEXT, status TEXT, body TEXT, created TEXT,
    change_reason TEXT, superseded INTEGER DEFAULT 0, UNIQUE(case_id, annotation_id, version));
CREATE TABLE IF NOT EXISTS case_feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT,
    annotation_id TEXT, text TEXT);
CREATE TABLE IF NOT EXISTS case_outcomes (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT,
    outcome TEXT, direction TEXT, evaluation TEXT, source_type TEXT);
CREATE TABLE IF NOT EXISTS discussion (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT,
    role TEXT, content TEXT, task_id TEXT, refs TEXT);
CREATE TABLE IF NOT EXISTS case_revisions (id INTEGER PRIMARY KEY AUTOINCREMENT, case_id TEXT, ts TEXT,
    field TEXT, value TEXT, reason TEXT, task_id TEXT);
CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, action TEXT,
    target TEXT, detail TEXT);
"""
CANDIDATE_KINDS = ("learning_difficulty", "preference", "procedural_rule", "user_claim", "reflection",
                   "case_pattern")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class MemoryStore:
    def __init__(self, path: str, canonical_root: str | None = None):
        if canonical_root and is_inside(path, canonical_root):
            raise PermissionError("Memory must not be stored inside the canonical knowledge base")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------ helpers
    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self.lock:
            cur = self.db.execute(sql, params)
            self.db.commit()
            return cur

    def _rows(self, sql: str, params=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def _audit(self, action: str, target: str, detail) -> None:
        self._exec("INSERT INTO audit (ts, action, target, detail) VALUES (?,?,?,?)",
                   (_now(), action, target, json.dumps(detail, ensure_ascii=False, default=str)[:4000]))

    # ------------------------------------------------------------ short-term (sessions)
    def session(self, session_id: str | None = None, channel: str = "web") -> dict:
        sid = session_id or f"s_{uuid.uuid4().hex[:10]}"
        rows = self._rows("SELECT * FROM sessions WHERE session_id = ?", (sid,))
        if not rows:
            self._exec("INSERT INTO sessions VALUES (?,?,?,?,?)", (sid, _now(), _now(), channel, "{}"))
            return {"session_id": sid, "state": {}}
        return {"session_id": sid, "state": json.loads(rows[0]["state"] or "{}")}

    def update_session(self, session_id: str, **state) -> dict:
        cur = self.session(session_id)["state"]
        cur.update({k: v for k, v in state.items()})
        self._exec("UPDATE sessions SET state = ?, updated = ? WHERE session_id = ?",
                   (json.dumps(cur, ensure_ascii=False), _now(), session_id))
        return cur

    def add_message(self, session_id: str, role: str, content: str, mode: str = "", refs=None) -> None:
        self._exec("INSERT INTO messages (session_id, ts, role, mode, content, refs) VALUES (?,?,?,?,?,?)",
                   (session_id, _now(), role, mode, content, json.dumps(refs or [], ensure_ascii=False)))

    def messages(self, session_id: str, limit: int = 40) -> list[dict]:
        rows = self._rows("SELECT ts, role, mode, content, refs FROM messages WHERE session_id = ? "
                          "ORDER BY id DESC LIMIT ?", (session_id, limit))
        return list(reversed(rows))

    def context_for(self, session_id: str, char_budget: int = 6000) -> dict:
        """Short-term context under a budget. Pinned state is always kept; recent turns verbatim;
        older turns shrink to just the user's questions. Brooks text is never summarized here:
        it is re-retrieved from the canonical store for every answer."""
        sess = self.session(session_id)
        msgs = self.messages(session_id, 60)
        recent, used = [], 0
        for m in reversed(msgs):
            size = len(m["content"])
            if used + size > char_budget * 0.75 and recent:
                break
            recent.insert(0, {"role": m["role"], "content": m["content"][:2500]})
            used += min(size, 2500)
        older = msgs[:len(msgs) - len(recent)]
        earlier = [m["content"][:160] for m in older if m["role"] == "user"][-10:]
        return {"pinned": sess["state"], "earlier_user_questions": earlier, "recent": recent}

    # ------------------------------------------------------------ candidates
    def add_candidate(self, kind: str, content: str, rationale: str = "", source_type: str = "agent",
                      source_ref: str = "") -> int:
        if kind not in CANDIDATE_KINDS:
            raise ValueError(f"kind must be one of {CANDIDATE_KINDS}")
        dup = self._rows("SELECT id FROM candidates WHERE kind = ? AND content = ? AND status = 'candidate'",
                         (kind, content))
        if dup:
            return dup[0]["id"]
        cid = self._exec("INSERT INTO candidates (kind, content, rationale, source_type, source_ref, created) "
                         "VALUES (?,?,?,?,?,?)", (kind, content, rationale, source_type, source_ref, _now())).lastrowid
        self._audit("candidate.create", f"candidate:{cid}", {"kind": kind, "source": source_type, "ref": source_ref})
        return cid

    def candidates(self, status: str | None = "candidate") -> list[dict]:
        if status:
            return self._rows("SELECT * FROM candidates WHERE status = ? ORDER BY id DESC", (status,))
        return self._rows("SELECT * FROM candidates ORDER BY id DESC")

    def approve_candidate(self, cid: int, approved_by: str, note: str = "", edited_content: str | None = None) -> str:
        """Human approval: promotes a candidate into the profile or the procedural rules.
        User claims about trading theory are never promoted into anything 'Brooks'; they become a
        confirmed USER NOTE in the profile, still labeled as the user's own view."""
        if not approved_by:
            raise PermissionError("Approval needs the name of the human approving it")
        rows = self._rows("SELECT * FROM candidates WHERE id = ?", (cid,))
        if not rows or rows[0]["status"] != "candidate":
            raise ValueError(f"candidate {cid} is not pending")
        c = rows[0]
        content = edited_content or c["content"]
        ref = f"candidate:{cid}"
        if c["kind"] == "procedural_rule":
            new_id = self._exec("INSERT INTO procedural_rules (rule, source_type, source_ref, approved_by, created, "
                                "updated) VALUES (?,?,?,?,?,?)", (content, c["source_type"], ref, approved_by,
                                                                    _now(), _now())).lastrowid
            target = f"procedural_rules:{new_id}"
        else:
            category = {"user_claim": "user_view"}.get(c["kind"], c["kind"])
            new_id = self._exec("INSERT INTO profile (category, content, source_type, source_ref, evidence, "
                                "confirmed_by, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                                (category, content, c["source_type"], ref, c["rationale"], approved_by, _now(),
                                 _now())).lastrowid
            target = f"profile:{new_id}"
        self._exec("UPDATE candidates SET status='approved', decided=?, decided_note=?, promoted_to=? WHERE id=?",
                   (_now(), note, target, cid))
        self._audit("candidate.approve", ref, {"by": approved_by, "to": target})
        return target

    def reject_candidate(self, cid: int, note: str = "") -> None:
        self._exec("UPDATE candidates SET status='rejected', decided=?, decided_note=? WHERE id=? "
                   "AND status='candidate'", (_now(), note, cid))
        self._audit("candidate.reject", f"candidate:{cid}", {"note": note})

    def delete_candidate(self, cid: int) -> None:
        self._exec("DELETE FROM candidates WHERE id = ?", (cid,))
        self._audit("candidate.delete", f"candidate:{cid}", {})

    # ------------------------------------------------------------ confirmed profile / rules
    def add_profile(self, category: str, content: str, confirmed_by: str, source_type: str = "user",
                    source_ref: str = "", evidence: str = "") -> int:
        """Direct entry by the user (e.g. from the memory page). Requires a confirming human."""
        if not confirmed_by:
            raise PermissionError("Profile entries need a confirming human")
        pid = self._exec("INSERT INTO profile (category, content, source_type, source_ref, evidence, confirmed_by, "
                         "created, updated) VALUES (?,?,?,?,?,?,?,?)",
                         (category, content, source_type, source_ref, evidence, confirmed_by, _now(), _now())).lastrowid
        self._audit("profile.add", f"profile:{pid}", {"category": category, "by": confirmed_by})
        return pid

    def profile(self) -> list[dict]:
        return self._rows("SELECT * FROM profile ORDER BY id")

    def update_profile(self, pid: int, content: str) -> None:
        self._exec("UPDATE profile SET content = ?, updated = ? WHERE id = ?", (content, _now(), pid))
        self._audit("profile.update", f"profile:{pid}", {"content": content[:200]})

    def delete_profile(self, pid: int) -> None:
        self._exec("DELETE FROM profile WHERE id = ?", (pid,))
        self._audit("profile.delete", f"profile:{pid}", {})

    def add_rule(self, rule: str, approved_by: str, source_type: str = "user") -> int:
        if not approved_by:
            raise PermissionError("Procedural rules need a human approver")
        rid = self._exec("INSERT INTO procedural_rules (rule, source_type, source_ref, approved_by, created, updated) "
                         "VALUES (?,?,?,?,?,?)", (rule, source_type, "", approved_by, _now(), _now())).lastrowid
        self._audit("rule.add", f"procedural_rules:{rid}", {"by": approved_by})
        return rid

    def rules(self, active_only: bool = True) -> list[dict]:
        return self._rows("SELECT * FROM procedural_rules" + (" WHERE active = 1" if active_only else "") + " ORDER BY id")

    def delete_rule(self, rid: int) -> None:
        self._exec("DELETE FROM procedural_rules WHERE id = ?", (rid,))
        self._audit("rule.delete", f"procedural_rules:{rid}", {})

    # ------------------------------------------------------------ concept stats (objective counters)
    def note_concepts(self, concepts: list[str], field_name: str = "times_asked") -> None:
        assert field_name in ("times_asked", "times_practised")
        for c in dict.fromkeys(x.strip().lower() for x in concepts if x.strip()):
            self._exec(f"INSERT INTO concept_stats (concept, {field_name}, last_seen) VALUES (?, 1, ?) "
                       f"ON CONFLICT(concept) DO UPDATE SET {field_name} = {field_name} + 1, last_seen = excluded.last_seen",
                       (c, _now()))

    def set_concept_status(self, concept: str, status: str) -> None:
        """Only the user sets this (e.g. 'mastered', 'struggling'); the agent never does."""
        self._exec("INSERT INTO concept_stats (concept, user_status, last_seen) VALUES (?,?,?) "
                   "ON CONFLICT(concept) DO UPDATE SET user_status = excluded.user_status",
                   (concept.lower(), status, _now()))
        self._audit("concept.status", concept, {"status": status})

    def concept_stats(self, limit: int = 30) -> list[dict]:
        return self._rows("SELECT * FROM concept_stats ORDER BY times_asked + times_practised DESC LIMIT ?", (limit,))

    def learning_context(self) -> dict:
        """What the coach may use to personalise teaching: confirmed items only (+ counters)."""
        return {"confirmed_profile": [{"id": p["id"], "category": p["category"], "content": p["content"]}
                                      for p in self.profile()],
                "approved_rules": [r["rule"] for r in self.rules()],
                "concept_stats": self.concept_stats(15)}

    # ------------------------------------------------------------ episodic cases
    def save_case(self, case: dict) -> str:
        cols = ("case_id", "created", "analysis_timestamp", "market_timestamp", "mode", "symbol", "timeframe",
                "original_path", "annotated_path", "image_w", "image_h", "market_context", "setup_classification",
                "initial_hypothesis", "supporting_evidence", "data_quality", "task_id", "note")
        row = {**{c: None for c in cols}, "created": _now(), **case}
        for k in ("initial_hypothesis", "supporting_evidence", "data_quality"):
            if not isinstance(row[k], (str, type(None))):
                row[k] = json.dumps(row[k], ensure_ascii=False)
        self._exec(f"INSERT INTO cases ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", [row[c] for c in cols])
        self._audit("case.save", f"case:{row['case_id']}", {"mode": row["mode"], "task": row["task_id"]})
        return row["case_id"]

    def set_annotated_path(self, case_id: str, path: str) -> None:
        self._exec("UPDATE cases SET annotated_path = ? WHERE case_id = ?", (path, case_id))

    def add_review_note(self, case_id: str, text: str) -> None:
        self._exec("UPDATE cases SET review_notes = review_notes || ? WHERE case_id = ?", (f"[{_now()}] {text}\n", case_id))

    def case(self, case_id: str) -> dict | None:
        rows = self._rows("SELECT * FROM cases WHERE case_id = ?", (case_id,))
        if not rows:
            return None
        c = rows[0]
        for k in ("initial_hypothesis", "supporting_evidence", "data_quality"):
            try:
                c[k] = json.loads(c[k]) if c[k] else None
            except (TypeError, json.JSONDecodeError):
                pass
        c["feedback"] = self._rows("SELECT ts, annotation_id, text FROM case_feedback WHERE case_id = ? ORDER BY id", (case_id,))
        c["outcomes"] = self._rows("SELECT ts, outcome, direction, evaluation, source_type FROM case_outcomes "
                                   "WHERE case_id = ? ORDER BY id", (case_id,))
        return c

    def cases(self, limit: int = 50, symbol: str | None = None) -> list[dict]:
        q = "SELECT case_id, created, mode, symbol, timeframe, setup_classification, market_context FROM cases"
        params: tuple = ()
        if symbol:
            q += " WHERE symbol = ?"
            params = (symbol,)
        return self._rows(q + " ORDER BY created DESC LIMIT ?", params + (limit,))

    def search_cases(self, text: str, limit: int = 5) -> list[dict]:
        like = f"%{text}%"
        return self._rows("SELECT case_id, created, symbol, timeframe, setup_classification, market_context FROM cases "
                          "WHERE setup_classification LIKE ? OR market_context LIKE ? OR symbol LIKE ? "
                          "ORDER BY created DESC LIMIT ?", (like, like, like, limit))

    def add_feedback(self, case_id: str, text: str, annotation_id: str | None = None) -> None:
        self._exec("INSERT INTO case_feedback (case_id, ts, annotation_id, text) VALUES (?,?,?,?)",
                   (case_id, _now(), annotation_id, text))
        self._audit("case.feedback", f"case:{case_id}", {"annotation": annotation_id})

    def add_outcome(self, case_id: str, outcome: str, direction: str = "", evaluation=None,
                    source_type: str = "user") -> int:
        oid = self._exec("INSERT INTO case_outcomes (case_id, ts, outcome, direction, evaluation, source_type) "
                         "VALUES (?,?,?,?,?,?)", (case_id, _now(), outcome, direction,
                                                   json.dumps(evaluation, ensure_ascii=False) if evaluation else None,
                                                   source_type)).lastrowid
        self._audit("case.outcome", f"case:{case_id}", {"direction": direction})
        return oid

    def delete_case(self, case_id: str) -> None:
        with self.lock:
            for t in ("annotations", "case_feedback", "case_outcomes", "discussion", "case_revisions", "cases"):
                self.db.execute(f"DELETE FROM {t} WHERE case_id = ?", (case_id,))
            self.db.commit()
        self._audit("case.delete", f"case:{case_id}", {})

    # ------------------------------------------------------------ case discussion + revised judgment
    REVISABLE = ("market_context", "always_in", "current_state", "decision_bias", "decision_plan")

    def add_discussion(self, case_id: str, role: str, content: str, task_id: str = "", refs=None) -> None:
        self._exec("INSERT INTO discussion (case_id, ts, role, content, task_id, refs) VALUES (?,?,?,?,?,?)",
                   (case_id, _now(), role, content, task_id, json.dumps(refs or [], ensure_ascii=False)))

    def discussion(self, case_id: str, limit: int = 40) -> list[dict]:
        rows = self._rows("SELECT ts, role, content, task_id, refs FROM discussion WHERE case_id = ? "
                          "ORDER BY id DESC LIMIT ?", (case_id, limit))
        return list(reversed(rows))

    def add_case_revision(self, case_id: str, field: str, value: str, reason: str, task_id: str = "") -> int:
        if field not in self.REVISABLE:
            raise ValueError(f"field must be one of {self.REVISABLE}")
        rid = self._exec("INSERT INTO case_revisions (case_id, ts, field, value, reason, task_id) VALUES (?,?,?,?,?,?)",
                         (case_id, _now(), field, value, reason, task_id)).lastrowid
        self._audit("case.revise", f"case:{case_id}", {"field": field, "reason": reason[:200]})
        return rid

    def case_revisions(self, case_id: str) -> list[dict]:
        return self._rows("SELECT id, ts, field, value, reason, task_id FROM case_revisions WHERE case_id = ? "
                          "ORDER BY id", (case_id,))

    def current_view(self, case_id: str) -> dict:
        """The coach's current judgment: the original hypothesis with later revisions applied.
        Each field records whether it was revised and how many times."""
        case = self.case(case_id) or {}
        hyp = case.get("initial_hypothesis") or {}
        view = {"market_context": case.get("market_context") or "",
                "always_in": hyp.get("always_in", ""), "current_state": hyp.get("current_state", ""),
                "decision_bias": hyp.get("bias", ""), "decision_plan": hyp.get("conclusion", "")}
        revised: dict[str, int] = {}
        for r in self.case_revisions(case_id):
            view[r["field"]] = r["value"]
            revised[r["field"]] = revised.get(r["field"], 0) + 1
        view["revised_fields"] = revised
        return view

    # ------------------------------------------------------------ annotation registry
    def save_annotations(self, case_id: str, annotations: list[dict], reason: str = "initial analysis") -> None:
        for a in annotations:
            self._insert_annotation(case_id, a, 1, reason)

    def _insert_annotation(self, case_id, a, version, reason):
        self._exec("INSERT INTO annotations (case_id, annotation_id, version, type, status, body, created, change_reason) "
                   "VALUES (?,?,?,?,?,?,?,?)", (case_id, a["annotation_id"], version, a.get("type"), a.get("status"),
                                                json.dumps(a, ensure_ascii=False), _now(), reason))

    def revise_annotation(self, case_id: str, annotation_id: str, new_body: dict, reason: str) -> int:
        """Adds a new version; earlier versions stay (marked superseded), never overwritten."""
        cur = self.annotation(case_id, annotation_id)
        if cur is None:
            raise KeyError(f"{annotation_id} not found in case {case_id}")
        version = cur["version"] + 1
        body = {**new_body, "annotation_id": annotation_id, "version": version}
        self._exec("UPDATE annotations SET superseded = 1 WHERE case_id = ? AND annotation_id = ?", (case_id, annotation_id))
        self._insert_annotation(case_id, body, version, reason)
        self._audit("annotation.revise", f"{case_id}/{annotation_id}", {"version": version, "reason": reason[:200]})
        return version

    def annotation(self, case_id: str, annotation_id: str, version: int | None = None) -> dict | None:
        q = "SELECT version, body, change_reason, created, superseded FROM annotations WHERE case_id = ? AND annotation_id = ?"
        params = [case_id, annotation_id]
        if version:
            q += " AND version = ?"
            params.append(version)
        rows = self._rows(q + " ORDER BY version DESC LIMIT 1", params)
        if not rows:
            return None
        r = rows[0]
        return {**json.loads(r["body"]), "version": r["version"], "change_reason": r["change_reason"],
                "created": r["created"], "superseded": bool(r["superseded"])}

    def annotations(self, case_id: str, include_history: bool = False) -> list[dict]:
        q = "SELECT version, body, change_reason, superseded FROM annotations WHERE case_id = ?"
        if not include_history:
            q += " AND superseded = 0"
        rows = self._rows(q + " ORDER BY id", (case_id,))
        return [{**json.loads(r["body"]), "version": r["version"], "change_reason": r["change_reason"],
                 "superseded": bool(r["superseded"])} for r in rows]

    def audit_log(self, limit: int = 100) -> list[dict]:
        return self._rows("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))
