"""The MCP tool CONTRACT is frozen: names, descriptions and input schemas.

Why: every connected client (claude.ai, ChatGPT, Muse, the Team org) snapshots
`tools/list` when it connects and does not re-read it on redeploy. A changed description
or schema silently diverges from what the client believes until someone clicks "Refresh
tools list". So: a tool, once shipped, keeps its name, description and schema. New
capability = a NEW tool or a NEW OPTIONAL field. Changing anything in this file's
snapshot is a deliberate act (run with --update), and the commit message says which
clients need a refresh.

Also pins the 16,384-byte inputSchema limit claude.ai enforces by silently dropping the
tool (anthropics/claude-code#77296).

  ./.venv/bin/python tests/test_tool_contract.py            # check (no DB needed)
  ./.venv/bin/python tests/test_tool_contract.py --update   # re-snapshot after a deliberate change
"""
import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "oracle" / "agent"))
SNAPSHOT = pathlib.Path(__file__).resolve().parent / "tool_contract.json"
SCHEMA_LIMIT = 16_384


def current_contract():
    import mcp_server
    tm = getattr(mcp_server.mcp, "_tool_manager", None)
    tools = asyncio.run(tm.list_tools() if tm else mcp_server.mcp.list_tools())
    out = {}
    for t in tools:
        schema = getattr(t, "parameters", None) or getattr(t, "inputSchema", None) or {}
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump()
        out[t.name] = {
            "description": (t.description or "").strip(),
            "schema_sha": __import__("hashlib").sha256(
                json.dumps(schema, sort_keys=True).encode()).hexdigest()[:16],
            "schema_bytes": len(json.dumps(schema, separators=(",", ":"))),
        }
    return dict(sorted(out.items()))


def test_contract_frozen():
    got = current_contract()
    assert SNAPSHOT.exists(), f"no snapshot yet: run {__file__} --update once and commit it"
    want = json.loads(SNAPSHOT.read_text())
    removed = sorted(set(want) - set(got))
    added = sorted(set(got) - set(want))
    changed = sorted(n for n in set(want) & set(got)
                     if want[n]["description"] != got[n]["description"]
                     or want[n]["schema_sha"] != got[n]["schema_sha"])
    problems = []
    if removed:
        problems.append(f"REMOVED tools (clients still list them): {removed}")
    if changed:
        problems.append(f"CHANGED description/schema (clients keep the old text): {changed}")
    if added:
        problems.append(f"NEW tools (fine, but every client needs 'Refresh tools list'): {added}")
    assert not problems, "\n".join(problems) + "\n-> if deliberate: --update, and say so in the commit"


def test_schema_size_limit():
    for name, c in current_contract().items():
        assert c["schema_bytes"] < SCHEMA_LIMIT, \
            f"{name}: inputSchema is {c['schema_bytes']} bytes; claude.ai drops tools over {SCHEMA_LIMIT}"


if __name__ == "__main__":
    if "--update" in sys.argv:
        SNAPSHOT.write_text(json.dumps(current_contract(), indent=1) + "\n")
        print(f"snapshot written: {SNAPSHOT} ({len(json.loads(SNAPSHOT.read_text()))} tools)")
        sys.exit(0)
    for fn in (test_schema_size_limit, test_contract_frozen):
        fn()
        print(f"ok  {fn.__name__}")
