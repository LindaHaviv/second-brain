"""Oracle AI Agent Memory (OAMP) — the official package as the SHIP-PATH memory core.

Set MEMORY_BACKEND=oamp to use it: `pip install oracleagentmemory` (already in the
requirements) turns the same database this repo already uses into a managed memory
core — the recommended path once you've learned the model and want Oracle maintaining
it. What it takes over, and what stays:

  conversational memory -> OAMP threads + context summaries   (BRAIN_THREAD / BRAIN_MESSAGE)
  semantic memory       -> OAMP durable memories, extracted   (BRAIN_MEMORY)
                           automatically by an LLM from each exchange
  episodic run log      -> stays custom (memory.py)           (AGENT_MEMORY)
  procedural tool stats -> stays custom (procedural.py)       (TOOL_REGISTRY)

OAMP has no run-log or tool-ranking record types — those two are this repo's EXTENSION
of the memory core, living in the same database. The default backend (MEMORY_BACKEND
unset, or =custom) is the from-scratch learning track (semantic_memory.py +
conversation.py) — hand-built tables, verified on every LLM provider including the
$0 Ollama path; OAMP extraction is verified here with claude-sonnet-5 (small local
models may fail its structured-output format — see _EXTRACT_DEFAULTS below).

Everything below reuses the repo's existing configuration:
  - the in-DB MINILM embedder (zero embedding API calls — same story as search)
  - LLM_PROVIDER / LLM_MODEL from llm.py, mapped to LiteLLM ids for OAMP
  - the PRIVACY GUARD, passed as 26.6's memory_extraction_custom_instructions,
    so the managed extractor obeys the same rule as the custom consolidator

Inspect it like everything else in this build — it's just tables:
  SELECT memory_type, content FROM brain_memory ORDER BY created_at DESC;
"""
import os
import uuid

_MINILM_DIM = 384
_MINILM_TOKENS = 128          # all_MiniLM_L12_v2 context window
STORE_ID = os.environ.get("OAMP_STORE_ID", "brain")   # table prefix: BRAIN_*
USER_ID = os.environ.get("BRAIN_USER", "me")
AGENT_ID = "research"

# Same rule as semantic_memory.py's consolidator — the guard moves INTO the managed core.
_PRIVACY_GUARD = (
    "Never extract financial or private business details: no earnings, rates, fees, "
    "pricing, invoices, payments, banking, budgets, taxes, or contract/deal terms. "
    "A post being a brand collaboration is fine to remember; the money and terms are not."
)

# LiteLLM model ids per provider (OAMP speaks LiteLLM). Overridable with OAMP_LLM_MODEL.
# NOTE: haiku-4-5 fails OAMP 26.6's structured extraction format (verified: "invalid
# structured output" -> zero memories), so the anthropic default is sonnet-5.
_EXTRACT_DEFAULTS = {
    "anthropic": "anthropic/claude-sonnet-5",
    "openai": "openai/gpt-5.2",
    "ollama": "ollama/llama3.2",
}

_client = None
_thread = None


def _llm():
    from oracleagentmemory.core.llms import Llm
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    model = os.environ.get("OAMP_LLM_MODEL") or _EXTRACT_DEFAULTS.get(
        provider, _EXTRACT_DEFAULTS["anthropic"])
    kwargs = {}
    if provider == "ollama":
        kwargs["api_base"] = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    return Llm(model=model, **kwargs)


def get_client(conn):
    """One OracleAgentMemory client per process, over the repo's existing connection.
    Schema is auto-created on first use (CREATE_IF_NECESSARY) — no extra setup step."""
    global _client
    if _client is None:
        from oracleagentmemory.core import (
            OracleAgentMemory, SchemaPolicy, SearchStrategy, MemoryExtractionConfig)
        from oracleagentmemory.core.embedders import OracleDBEmbedder
        _client = OracleAgentMemory(
            connection=conn,
            embedder=OracleDBEmbedder(connection=conn, model="MINILM",
                                      embedding_dimension=_MINILM_DIM,
                                      max_input_tokens=_MINILM_TOKENS),
            llm=_llm(),
            schema_policy=SchemaPolicy.CREATE_IF_NECESSARY,
            memory_store_id=STORE_ID,
            search_strategy=SearchStrategy.HYBRID,   # 26.6: lexical + vector, one index
            memory_extraction_config=MemoryExtractionConfig(
                memory_extraction_custom_instructions=_PRIVACY_GUARD),
        )
    return _client


def recall_facts(conn, query, k=5):
    """Durable memories relevant to `query`, shaped like semantic_memory.semantic_recall
    (category + fact) so the agent prompt renders identically on either backend."""
    results = get_client(conn).search(
        query=query, user_id=USER_ID, exact_user_match=True, max_results=k,
        record_types=["memory", "fact", "preference", "guideline"])
    return [{"category": getattr(r, "record_type", None) or "memory",
             "fact": getattr(r, "content", None) or str(r)} for r in results]


def record_exchange(conn, question, answer):
    """Persist one Q/A exchange to the session thread. OAMP extracts durable memories
    from it automatically (this replaces the custom episodic->semantic consolidation
    step on this backend). Best-effort: memory must never break a research answer."""
    global _thread
    try:
        client = get_client(conn)
        if _thread is None:
            _thread = client.create_thread(
                thread_id="sess-" + uuid.uuid4().hex[:10],
                user_id=USER_ID, agent_id=AGENT_ID)
        _thread.add_messages([
            {"role": "user", "content": question},
            {"role": "assistant", "content": answer},
        ])
        return _thread.thread_id
    except Exception as e:
        print(f"[oamp] exchange not recorded: {type(e).__name__}: {str(e)[:120]}")
        return None
