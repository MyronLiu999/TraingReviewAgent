"""Agent harness for the Brooks coach: tasks, tools, model calls, mode routing and workflows.

- llm.py      one model interface (OpenAI-compatible), token/cost/latency logging, test double
- harness.py  task lifecycle + checkpoints (SQLite), tool registry with permissions/timeouts/retries
- router.py   picks Study / Review / Decision / Explain / Memory mode for a request
- runtime.py  CoachAgent: the workflows that tie knowledge, engines, memory and rendering together
"""
