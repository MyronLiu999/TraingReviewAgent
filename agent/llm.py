"""One interface for every model call: structured JSON output, timeouts, retries, usage logging.

Provider: any OpenAI-compatible endpoint (set llm_base_url in config.toml for others).
The API key is read from the environment by the SDK and is never logged or stored.
"""

import json
import logging
import time

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


class LLM:
    def __init__(self, cfg: dict, client=None):
        self.cfg = cfg
        self.default_model = cfg.get("review_model", "gpt-4o")
        self.pricing = cfg.get("pricing", {})
        if client is None:
            import openai
            kwargs = {"timeout": float(cfg.get("llm_timeout_seconds", 180)), "max_retries": 2}
            if cfg.get("llm_base_url"):
                kwargs["base_url"] = cfg["llm_base_url"]
            client = openai.OpenAI(**kwargs)
        self.client = client

    def model_for(self, purpose: str) -> str:
        return self.cfg.get(f"{purpose}_model") or self.default_model

    def json_call(self, *, system: str, content: list | str, schema_name: str, schema: dict,
                  purpose: str = "review", max_tokens: int = 6000, recorder=None) -> dict:
        """Returns the parsed JSON object. `recorder(dict)` receives usage/latency for the task log."""
        model = self.model_for(purpose)
        max_tokens = int(self.cfg.get("llm_max_output_tokens", max_tokens))   # reasoning models need more
        start = time.time()
        try:
            response = self.client.chat.completions.create(
                model=model,
                max_completion_tokens=max_tokens,
                messages=[{"role": "system", "content": system},
                          {"role": "user", "content": content}],
                response_format={"type": "json_schema", "json_schema": {
                    "name": schema_name, "strict": True, "schema": schema}},
            )
        except Exception as e:
            self._record(recorder, model, schema_name, start, None, error=type(e).__name__)
            raise
        choice = response.choices[0]
        self._record(recorder, model, schema_name, start, getattr(response, "usage", None))
        if getattr(choice.message, "refusal", None):
            raise LLMError(f"Model declined: {choice.message.refusal}")
        if choice.finish_reason == "length" or not choice.message.content:
            raise LLMError("Model response was cut off or empty")
        return json.loads(choice.message.content)

    def _record(self, recorder, model, schema_name, start, usage, error=None):
        entry = {"model": model, "schema": schema_name, "latency_s": round(time.time() - start, 2)}
        if usage is not None:
            pin, pout = getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0
            entry.update(prompt_tokens=pin, completion_tokens=pout)
            price = self.pricing.get(model)
            if price:
                entry["cost_usd"] = round((pin * price[0] + pout * price[1]) / 1e6, 5)
        if error:
            entry["error"] = error
        log.info("LLM %s %s %.1fs tokens=%s/%s", model, schema_name, entry["latency_s"],
                 entry.get("prompt_tokens", "?"), entry.get("completion_tokens", "?"))
        if recorder:
            recorder(entry)


class ScriptedLLM:
    """Test double: returns canned JSON per schema name and records every prompt it received.
    `responses[schema_name]` may be a dict, a list (consumed in order) or a callable(system, content)."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls: list[dict] = []
        self.default_model = "scripted"

    def model_for(self, purpose: str) -> str:
        return "scripted"

    def json_call(self, *, system, content, schema_name, schema, purpose="review", max_tokens=6000,
                  recorder=None) -> dict:
        self.calls.append({"system": system, "content": content, "schema_name": schema_name})
        if recorder:
            recorder({"model": "scripted", "schema": schema_name, "latency_s": 0.0})
        r = self.responses.get(schema_name)
        if r is None:
            raise LLMError(f"ScriptedLLM has no response for {schema_name}")
        if isinstance(r, list):
            r = r.pop(0) if len(r) > 1 else r[0]
        if callable(r):
            r = r(system, content)
        return json.loads(json.dumps(r))

    def prompt_text(self, i: int = -1) -> str:
        c = self.calls[i]
        parts = [c["system"]]
        content = c["content"]
        if isinstance(content, str):
            parts.append(content)
        else:
            parts += [p.get("text", "") for p in content if isinstance(p, dict)]
        return "\n".join(parts)
