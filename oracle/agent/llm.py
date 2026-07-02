"""Provider-agnostic LLM adapter — pick your model with config, not code edits.

In oracle/.env:
  LLM_PROVIDER=anthropic   (default)  needs ANTHROPIC_API_KEY
  LLM_PROVIDER=openai                 needs OPENAI_API_KEY  (pip install openai)
  LLM_PROVIDER=ollama                 needs a local Ollama  (OLLAMA_HOST, default localhost:11434)
  LLM_MODEL=<override>                optional; sensible default per provider

This covers every *structured* LLM step (wiki compiler, memory consolidation, the
classifiers, the idea agent): one `structured(system, prompt, schema)` call that returns
validated JSON on any provider. The research agent's TOOL LOOP (client tools + server-side
web search) is Anthropic-shaped and stays Claude-first — run it with Claude, or point the
Anthropic SDK at an Anthropic-compatible gateway via ANTHROPIC_BASE_URL.
"""
import json
import os
import pathlib
import urllib.request

from dotenv import load_dotenv

# self-sufficient: load oracle/.env relative to this file (same pattern as db.py), so the
# adapter works no matter which module imports it first, from any cwd.
load_dotenv(pathlib.Path(__file__).resolve().parent.parent / ".env")

PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").lower()
DEFAULTS = {"anthropic": "claude-opus-4-8", "openai": "gpt-5.2", "ollama": "llama3.3"}
MODEL = os.environ.get("LLM_MODEL") or DEFAULTS.get(PROVIDER, DEFAULTS["anthropic"])


def structured(system, prompt, schema, max_tokens=4096, model=None):
    """One prompt in, schema-validated JSON out — on whichever provider is configured.
    `model` overrides per call on anthropic (e.g. a cheap classifier model); other
    providers use the configured LLM_MODEL."""
    if PROVIDER == "anthropic":
        import anthropic
        r = anthropic.Anthropic().messages.create(
            model=model or MODEL, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
            output_config={"format": {"type": "json_schema", "schema": schema}})
        return json.loads(next(b.text for b in r.content if b.type == "text"))

    if PROVIDER == "openai":
        try:
            from openai import OpenAI
        except ImportError:
            raise SystemExit("LLM_PROVIDER=openai needs the sdk: ./.venv/bin/pip install openai")
        r = OpenAI().chat.completions.create(
            model=MODEL, max_completion_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": prompt}],
            response_format={"type": "json_schema",
                             "json_schema": {"name": "out", "strict": True, "schema": schema}})
        return json.loads(r.choices[0].message.content)

    if PROVIDER == "ollama":
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        body = json.dumps({"model": MODEL, "stream": False, "format": schema,
                           "messages": [{"role": "system", "content": system},
                                        {"role": "user", "content": prompt}]}).encode()
        req = urllib.request.Request(f"{host}/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=600) as resp:
            return json.loads(json.loads(resp.read())["message"]["content"])

    raise SystemExit(f"unknown LLM_PROVIDER={PROVIDER!r} (anthropic | openai | ollama)")
