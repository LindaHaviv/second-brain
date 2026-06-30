"""Content-Ops agent (PROPOSE-ONLY) — keeps your Notion content tracker honest.

Reads your "Content Tracker" database (title / High Level Phase / Platform / URL / Expected
Publish Date) and your actually-published content from the brain, then proposes:
  - phase fixes: tracker items that are clearly published but still sitting in an early phase
  - missing entries: published videos with no tracker row
It NEVER writes to Notion — it prints proposals (with the Notion page_id) for you to approve.

  cd oracle/agent && ../../.venv/bin/python content_ops.py
"""
import json
import os

import anthropic
from notion_client import Client

from db import connect          # importing db loads oracle/.env (NOTION_TOKEN + ANTHROPIC_API_KEY)

MODEL = "claude-opus-4-8"

SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "phase_updates": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"page_id": {"type": "string"}, "title": {"type": "string"},
                           "current_phase": {"type": "string"}, "proposed_phase": {"type": "string"},
                           "evidence": {"type": "string"}},
            "required": ["page_id", "title", "current_phase", "proposed_phase", "evidence"]}},
        "missing_from_tracker": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"title": {"type": "string"}, "url": {"type": "string"}},
            "required": ["title", "url"]}},
    },
    "required": ["phase_updates", "missing_from_tracker"],
}


def _g(props, name):
    v = props.get(name, {})
    ty = v.get("type")
    if ty == "title":
        return "".join(t.get("plain_text", "") for t in v.get("title", []))
    if ty == "status":
        return (v.get("status") or {}).get("name")
    if ty == "select":
        return (v.get("select") or {}).get("name")
    if ty == "url":
        return v.get("url")
    if ty == "date":
        return (v.get("date") or {}).get("start")
    return None


def _title(props):
    return next((_g(props, k) for k, v in props.items() if v.get("type") == "title"), "")


def find_tracker(notion):
    for d in notion.search(filter={"property": "object", "value": "data_source"}).get("results", []):
        name = "".join(p.get("plain_text", "") for p in (d.get("title") or []))
        if "Content" in name:
            return d["id"]
    return None


def fetch_tracker(notion, ds):
    items, cursor = [], None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        r = notion.request(path=f"data_sources/{ds}/query", method="POST", body=body)
        for x in r.get("results", []):
            p = x["properties"]
            items.append({"page_id": x["id"], "title": _title(p) or "(untitled)",
                          "phase": _g(p, "High Level Phase") or "", "platform": _g(p, "Platform") or "",
                          "url": _g(p, "URL") or "", "date": _g(p, "Expected Publish Date") or ""})
        if not r.get("has_more"):
            break
        cursor = r.get("next_cursor")
    return items


def published(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT title, url FROM posts WHERE platform_id = 'youtube' "
                    "AND title IS NOT NULL ORDER BY published_at DESC FETCH FIRST 150 ROWS ONLY")
        return [{"title": t, "url": u} for t, u in cur.fetchall()]


def reconcile(client, items, pub):
    phases = sorted({i["phase"] for i in items if i["phase"]})
    prompt = (
        f"PRODUCTION PHASES in use (early -> done): {phases}\n\n"
        "CONTENT TRACKER (page_id | title | phase | url):\n" +
        "\n".join(f"- {i['page_id']} | {i['title']} | {i['phase']} | {i['url']}" for i in items) +
        "\n\nACTUALLY PUBLISHED VIDEOS (title | url):\n" +
        "\n".join(f"- {p['title']} | {p['url']}" for p in pub) +
        "\n\nPropose: (1) tracker items that clearly match a published video but are still in an "
        "early/mid phase -> advance to the most 'done/published' phase in the list; (2) published "
        "videos with no tracker row. Match on meaning, not exact strings. High-confidence only."
    )
    r = client.messages.create(
        model=MODEL, max_tokens=4096,
        system=("You reconcile a creator's Notion content tracker against what she actually "
                "published. Be conservative — only propose changes you're confident about."),
        messages=[{"role": "user", "content": prompt}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}})
    return json.loads(next(b.text for b in r.content if b.type == "text"))


def main():
    if not os.environ.get("NOTION_TOKEN"):
        print("NOTION_TOKEN not set in oracle/.env"); return
    notion = Client(auth=os.environ["NOTION_TOKEN"])
    ds = find_tracker(notion)
    if not ds:
        print("Content Tracker database not found (check integration sharing)."); return
    conn = connect()
    try:
        items = fetch_tracker(notion, ds)
        pub = published(conn)
        print(f"tracker items: {len(items)} · published videos: {len(pub)}\n")
        out = reconcile(anthropic.Anthropic(), items, pub)
        print("=== PROPOSED PHASE FIXES (approve before applying) ===")
        for s in out["phase_updates"]:
            print(f"  • {s['title']}: {s['current_phase']} -> {s['proposed_phase']}")
            print(f"      evidence: {s['evidence']}   [page {s['page_id']}]")
        print("  (none)" if not out["phase_updates"] else "")
        print("=== PUBLISHED BUT MISSING FROM TRACKER ===")
        for m in out["missing_from_tracker"]:
            print(f"  • {m['title']}  {m['url']}")
        print("  (none)" if not out["missing_from_tracker"] else "")
        print("propose-only — nothing was written to Notion.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
