"""Clean up imported chats: separate PRIVATE material and prune OFF-TOPIC noise, so the content
brain only holds (and only ever surfaces) chats relevant to the creator's work.

Chat exports (Claude, ChatGPT, code sessions) are a mixed bag — content work, the business side of
brand deals, and a lot of random one-off questions. This does one LLM pass and tags posts.visibility:
  content   -> kept (creator/tech work: content, learning, interview prep, career/brand)
  business  -> private financial/deal side (rates, earnings, contracts, invoices, negotiations)
  archived  -> off-topic personal one-offs unrelated to her work

Only visibility='content' is searched / wiki'd / consolidated, so business + archived are hidden
everywhere automatically (no other plumbing). Run AFTER importing any chat export:
    ../.venv/bin/python scripts/classify_private.py            # preview counts
    ../.venv/bin/python scripts/classify_private.py --apply    # tag business + archived
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oracle", "agent"))
import db
import anthropic

MODEL = "claude-haiku-4-5"
RUBRIC = """Classify each AI chat from a TECH CONTENT CREATOR / developer advocate into exactly one:

"business" — the chat's primary purpose is the MONEY / LEGAL / TRACKING side of a brand deal:
  rates, fees, pricing, quotes, earnings, income, invoices, payments, budgets, expenses; contracts,
  SOW, NDAs, exclusivity terms; negotiating/closing a paid deal; maintaining a deal/financial tracker.

"archived" — an OFF-TOPIC personal one-off with nothing to do with her work OR her story:
  e.g. cooking, travel/logistics bookings, shopping, random trivia, unrelated errands, throwaway
  tests ("hi", "test"). Prune these — they're noise. (NOTE: her personal *background/story* is NOT
  noise — see content.)

"content" — ANYTHING related to her professional/creator domain OR her personal narrative (keep):
  content creation (scripts, captions, hooks, video ideas, edits); technical/learning topics (AI,
  ML, LLMs, cloud, dev tools, MCP, agents, coding); interview prep or researching a person/company;
  industry and product topics she covers; tools she uses for content; career, personal-brand and
  positioning; AND her personal BACKGROUND / life story / career journey / values / bio / origin
  story / non-traditional path — a creator's own story is core content material (bios, "about me",
  personal-brand posts, interview intros). Being for a paid/sponsored campaign does NOT make it
  business — only the money/terms do.

Bias: if it plausibly relates to her tech/creator work OR her personal story/brand, choose "content"
(don't over-prune). Only mark "archived" when it's clearly an unrelated personal one-off. Only
"business" when it's clearly the deal money/terms.
Return STRICT JSON list of {"id":<int>,"label":"content"|"business"|"archived"}."""

VIS = {"business": "business", "archived": "archived"}   # content stays as-is


def classify(client, batch):
    lines = "\n".join(f'{p["id"]}: {p["title"]} :: {p["snip"]}' for p in batch)
    msg = client.messages.create(model=MODEL, max_tokens=2200, system=RUBRIC,
        messages=[{"role": "user", "content": f"Classify each:\n{lines}\n\nJSON list only."}])
    t = msg.content[0].text.strip()
    if t.startswith("```"):
        t = t.split("```")[1].replace("json", "", 1).strip()
    return json.loads(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write visibility (else dry-run)")
    ap.add_argument("--all", action="store_true", help="reclassify all chats, not just untagged")
    args = ap.parse_args()
    client = anthropic.Anthropic()
    conn = db.connect(); cur = conn.cursor()
    cur.execute("alter session disable parallel dml")
    scope = "" if args.all else "and nvl(visibility,'content')='content'"
    rows = [{"id": int(r[0]), "title": (r[1] or "")[:90],
             "snip": (r[2] or "").replace("\n", " ")[:180]}
            for r in cur.execute("select post_id, title, dbms_lob.substr(caption,220,1) from posts "
                                 f"where platform_id in ('claude','claude_code','chatgpt') {scope}")]
    if not rows:
        print("no chats to classify"); return
    print(f"classifying {len(rows)} chats ({MODEL})...")
    labels = {}
    for i in range(0, len(rows), 25):
        try:
            for r in classify(client, rows[i:i+25]):
                labels[int(r["id"])] = r.get("label")
        except Exception as e:
            print(f"  batch {i}: {str(e)[:70]} (kept as content)")
        if (i // 25) % 10 == 0:
            print(f"  {min(i+25, len(rows))}/{len(rows)}")
    buckets = {"business": [], "archived": []}
    for pid, lab in labels.items():
        if lab in buckets:
            buckets[lab].append(pid)
    kept = len(rows) - len(buckets["business"]) - len(buckets["archived"])
    print(f"\ncontent(keep)={kept}  business={len(buckets['business'])}  "
          f"archived(off-topic)={len(buckets['archived'])}  of {len(rows)}")
    if args.apply:
        for lab, ids in buckets.items():
            if not ids:
                continue
            b = {f"i{j}": v for j, v in enumerate(ids)}
            inlist = ",".join(f":i{j}" for j in range(len(ids)))
            cur.execute(f"update posts set visibility=:v where post_id in ({inlist})",
                        v=VIS[lab], **b)
            print(f"  tagged {cur.rowcount} -> {VIS[lab]}")
        conn.commit()
        print("done — business + off-topic are now hidden from the content brain.")
    else:
        print("dry-run — re-run with --apply to tag them.")
    conn.close()


if __name__ == "__main__":
    main()
