"""Task lifecycle, checkpoints, tool registry and execution policy.

Every request becomes a Task with a unique id that moves through
    CREATED -> VALIDATING -> RETRIEVING -> ANALYZING -> VERIFYING -> RENDERING -> COMPLETED
(or FAILED / RECOVERABLE). Each transition is a checkpoint persisted to SQLite (data/agent.db)
and to a JSONL trace, so a task can be inspected afterwards and a RECOVERABLE one resumed
from its last finished step without repeating model calls.

Tools are registered with a name, description, input/output schema (pydantic), permission,
timeout, retry policy and logging policy. The policy refuses tools that could trade, administer
the canonical knowledge base, or write to it - those permissions simply do not exist here.
"""

import contextvars
import json
import logging
import os
import sqlite3
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

log = logging.getLogger(__name__)

STATES = ("CREATED", "VALIDATING", "RETRIEVING", "ANALYZING", "VERIFYING", "RENDERING", "SAVING",
          "COMPLETED", "FAILED", "RECOVERABLE")
ALLOWED_PERMISSIONS = {"read", "llm", "render", "memory_write", "case_write"}
FORBIDDEN_PERMISSIONS = {"trade", "order", "broker", "canonical_write", "admin"}
SECRET_KEYS = ("key", "token", "password", "secret", "authorization")


class PolicyError(PermissionError):
    pass


class ToolError(RuntimeError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def redact(obj: Any, limit: int = 300) -> Any:
    """Shortens and scrubs tool arguments before they are logged."""
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in SECRET_KEYS) else redact(v, limit)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact(v, limit) for v in obj[:20]]
    if isinstance(obj, str):
        if obj.startswith("data:image") or len(obj) > limit:
            return obj[:60] + f"...<{len(obj)} chars>"
        return obj
    if isinstance(obj, (int, float, bool)) or obj is None:
        return obj
    return f"<{type(obj).__name__}>"


# ---------------------------------------------------------------- tasks

@dataclass
class Task:
    task_id: str
    mode: str
    state: str = "CREATED"
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)
    session_id: str | None = None
    inputs: dict = field(default_factory=dict)
    data: dict = field(default_factory=dict)          # step outputs (checkpointed)
    completed_steps: list = field(default_factory=list)
    tool_calls: list = field(default_factory=list)
    llm_calls: list = field(default_factory=list)
    retrieved_doc_ids: list = field(default_factory=list)
    memory_writes: list = field(default_factory=list)
    artifacts: list = field(default_factory=list)
    validation: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(self.__dict__, ensure_ascii=False, default=str)

    @classmethod
    def from_json(cls, s: str) -> "Task":
        return cls(**json.loads(s))

    def record_llm(self, entry: dict) -> None:
        self.llm_calls.append({"ts": now(), **entry})


class TaskStore:
    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (task_id TEXT PRIMARY KEY, mode TEXT, state TEXT,
                created TEXT, updated TEXT, session_id TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT,
                ts TEXT, state TEXT, note TEXT);
        """)

    def close(self) -> None:
        self.db.close()

    def save(self, task: Task, note: str = "") -> None:
        task.updated = now()
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO tasks VALUES (?,?,?,?,?,?,?)",
                            (task.task_id, task.mode, task.state, task.created, task.updated,
                             task.session_id, task.to_json()))
            self.db.execute("INSERT INTO task_events (task_id, ts, state, note) VALUES (?,?,?,?)",
                            (task.task_id, task.updated, task.state, note[:500]))
            self.db.commit()

    def load(self, task_id: str) -> Task | None:
        with self.lock:
            row = self.db.execute("SELECT body FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return Task.from_json(row[0]) if row else None

    def events(self, task_id: str) -> list[tuple]:
        with self.lock:
            return self.db.execute("SELECT ts, state, note FROM task_events WHERE task_id = ? ORDER BY id",
                                   (task_id,)).fetchall()

    def recent(self, limit: int = 50) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT task_id, mode, state, created, updated FROM tasks "
                                   "ORDER BY created DESC LIMIT ?", (limit,)).fetchall()
        return [dict(zip(("task_id", "mode", "state", "created", "updated"), r)) for r in rows]


class Tracer:
    """Append-only JSONL trace (data/traces/YYYY-MM-DD.jsonl). Arguments are redacted."""

    def __init__(self, folder: str):
        self.folder = folder
        os.makedirs(folder, exist_ok=True)
        self.lock = threading.Lock()

    def emit(self, event: dict) -> None:
        path = os.path.join(self.folder, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        with self.lock, open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": now(), **event}, ensure_ascii=False, default=str) + "\n")


# ---------------------------------------------------------------- tools

@dataclass
class Tool:
    name: str
    description: str
    fn: Callable[..., Any]
    input_model: type[BaseModel]
    output_model: type[BaseModel] | None = None
    permission: str = "read"
    timeout_s: float = 60.0
    retries: int = 0
    retry_on: tuple = (ConnectionError, TimeoutError)
    log_args: bool = True

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "permission": self.permission,
                "timeout_s": self.timeout_s, "retries": self.retries,
                "input_schema": self.input_model.model_json_schema(),
                "output_schema": self.output_model.model_json_schema() if self.output_model else None}


class ToolRegistry:
    def __init__(self):
        self.tools: dict[str, Tool] = {}
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool")

    def register(self, tool: Tool) -> None:
        if tool.permission in FORBIDDEN_PERMISSIONS or tool.permission not in ALLOWED_PERMISSIONS:
            raise PolicyError(f"Tool {tool.name!r} asks for permission {tool.permission!r}, which the "
                              "runtime policy does not allow (no trading, no canonical writes, no admin).")
        if tool.name in self.tools:
            raise PolicyError(f"Tool {tool.name!r} is already registered")
        self.tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [t.spec() for t in self.tools.values()]

    def invoke(self, name: str, args: dict, task: Task | None = None, tracer: Tracer | None = None) -> Any:
        tool = self.tools.get(name)
        if tool is None:
            raise ToolError(f"Unknown tool {name!r}")
        try:
            parsed = tool.input_model(**args)
        except ValidationError as e:
            raise ToolError(f"Bad input for {name}: {e.errors()[:3]}") from e
        call = {"tool": name, "permission": tool.permission, "started": now(),
                "args": redact(args) if tool.log_args else "<not logged>"}
        attempt, start = 0, time.time()
        while True:
            attempt += 1
            try:
                ctx = contextvars.copy_context()      # tools see the caller's context (current task)
                result = self._pool.submit(ctx.run, tool.fn, **parsed.model_dump()).result(timeout=tool.timeout_s)
                if tool.output_model is not None:
                    tool.output_model.model_validate(result)
                call.update(ok=True, attempts=attempt, latency_s=round(time.time() - start, 3))
                break
            except FutureTimeout:
                err: Exception = ToolError(f"{name} timed out after {tool.timeout_s}s")
            except ValidationError as e:
                err = ToolError(f"{name} returned output that does not match its schema: {e.errors()[:2]}")
            except Exception as e:  # noqa: BLE001 - every tool error is recorded, then re-raised
                err = e
            if attempt <= tool.retries and isinstance(err, tool.retry_on):
                time.sleep(min(2 ** attempt, 8))
                continue
            call.update(ok=False, attempts=attempt, latency_s=round(time.time() - start, 3),
                        error=f"{type(err).__name__}: {err}"[:500])
            if task is not None:
                task.tool_calls.append(call)
            if tracer:
                tracer.emit({"event": "tool", "task_id": task.task_id if task else None, **call})
            raise err
        if task is not None:
            task.tool_calls.append(call)
        if tracer:
            tracer.emit({"event": "tool", "task_id": task.task_id if task else None, **call})
        return result


# ---------------------------------------------------------------- harness

Step = tuple[str, str, Callable[[Task], None]]   # (step name, state while running, function)


class Harness:
    def __init__(self, store: TaskStore, tools: ToolRegistry, tracer: Tracer):
        self.store, self.tools, self.tracer = store, tools, tracer

    def new_task(self, mode: str, inputs: dict, session_id: str | None = None) -> Task:
        task = Task(task_id=f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}", mode=mode,
                    session_id=session_id, inputs=redact(inputs, 2000))
        self.store.save(task, "created")
        self.tracer.emit({"event": "task_created", "task_id": task.task_id, "mode": mode})
        return task

    def transition(self, task: Task, state: str, note: str = "") -> None:
        assert state in STATES, state
        task.state = state
        self.store.save(task, note)
        self.tracer.emit({"event": "state", "task_id": task.task_id, "state": state, "note": note})

    def tool(self, task: Task, name: str, **args) -> Any:
        return self.tools.invoke(name, args, task, self.tracer)

    def run(self, task: Task, steps: list[Step]) -> Task:
        """Runs the steps in order, skipping ones already completed (resume). On error the task
        becomes RECOVERABLE (transient errors) or FAILED, with the traceback in task.errors."""
        started = time.time()
        for name, state, fn in steps:
            if name in task.completed_steps:
                continue
            self.transition(task, state, f"step {name}")
            try:
                fn(task)
            except Exception as e:  # noqa: BLE001
                transient = type(e).__name__ in ("APIConnectionError", "APITimeoutError", "RateLimitError",
                                                 "InternalServerError", "TimeoutError", "ConnectionError")
                task.errors.append({"step": name, "error": f"{type(e).__name__}: {e}"[:1000],
                                    "trace": traceback.format_exc()[-2000:], "ts": now()})
                self.transition(task, "RECOVERABLE" if transient else "FAILED", f"{name}: {type(e).__name__}")
                raise
            task.completed_steps.append(name)
            self.store.save(task, f"checkpoint {name}")
        task.data["latency_s"] = round(time.time() - started, 2)
        self.transition(task, "COMPLETED", "done")
        return task
