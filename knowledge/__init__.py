"""Brooks Canonical Knowledge Base: versioned, hash-verified, read-only at runtime.

- manifest.py  layout, manifests and integrity checks (read only)
- store.py     CanonicalKB: retrieval over the active version (read only)
- citations.py evidence labels, claim verification, citation formatting
- admin.py     the ONLY place that writes the canonical store (CLI, needs explicit authorization)
"""
