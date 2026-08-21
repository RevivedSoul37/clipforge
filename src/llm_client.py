"""Shared Ollama chat client for ClipForge.

Centralizes the HTTP call so every LLM stage (transcript cleanup, highlight
selection) gets the same context window, sampling settings and error handling.

Key fixes over ad-hoc calls:
- num_ctx: Ollama defaults to 4096 tokens. With the system prompt + ~1200
  transcript words + JSON output, that window silently truncates the answer,
  which is why highlight selection kept dropping clips. We request
  config.llm_num_ctx (default 8192).
- think: reasoning-style local models (e.g. some gemma builds) will burn the
  ENTIRE generation budget on internal reasoning and return empty content
  (done_reason "length"). We send think=false by default (configurable via
  llm.think) - for structured JSON extraction tasks thinking adds latency
  with no quality gain.
- temperature: kept low (default 0.2) so selection is deterministic.
- format: "json" (Ollama grammar constraint) with a graceful fallback for
  models that reject it.
"""
import json
import urllib.error
import urllib.request

from src.config import config


def _build_options():
    opts = {"num_ctx": config.llm_num_ctx, "temperature": config.llm_temperature}
    if config.llm_num_predict:
        opts["num_predict"] = config.llm_num_predict
    return opts


def call_ollama(messages, model=None, base_url=None, format_json=True,
                timeout=600, num_ctx=None, temperature=None, images=None):
    """POST /api/chat and return the assistant message content (str).

    `images` attaches base64-encoded frame(s) to the LAST user message so the
    same helper works for vision models (qwen2.5vl) as well as text models.

    Raises RuntimeError if both the JSON-format and plain attempts fail.
    """
    model = model or config.llm_model
    base_url = base_url or config.llm_base_url
    endpoint = f"{base_url.rstrip('/')}/api/chat"
    if images:
        messages = [dict(m) for m in messages]
        for m in reversed(messages):
            if m.get("role") == "user":
                m["images"] = list(images)
                break
    attempts = (True, False) if format_json else (False,)
    last_err = None
    for use_format in attempts:
        options = _build_options()
        if num_ctx is not None:
            options["num_ctx"] = int(num_ctx)
        if temperature is not None:
            options["temperature"] = float(temperature)
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        if use_format:
            payload["format"] = "json"
        if config.llm_think is False:
            payload["think"] = False
        req = urllib.request.Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return data["message"]["content"]
        except urllib.error.HTTPError as exc:
            last_err = exc
            continue  # e.g. model rejects `format: json`; retry without it
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, OSError) as exc:
            raise RuntimeError(f"Ollama request failed: {exc}") from exc
    raise RuntimeError(f"Ollama request failed: {last_err}")