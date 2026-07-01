"""Tag private/business chats so they stay OUT of the content brain and the self-improving loop.

Chat sources (Claude, ChatGPT, code sessions) can carry the *business* side of brand deals —
rates, earnings, contracts, invoices, negotiations. Content creation, analytics/performance, and
interview research are NOT business, even when they name a brand or a sponsored post. This scans
recently-ingested chats and sets posts.visibility='business' on the financial ones.

Run it AFTER importing any chat export. Dry-run by default; add --apply to write.
    ../.venv/bin/python scripts/classify_private.py            # preview
    ../.venv/bin/python scripts/classify_private.py --apply    # tag business
Only the money/terms are separated — reach/engagement and collab associations stay in content.
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oracle", "agent"))
import db
import anthropic

MODEL = "claude-haiku-4-5"
RUBRIC = """Split a creator's AI-chat history into CONTENT (keep in the brain) vs BUSINESS
(private financial/deal side — separate out). The deciding question: is the chat about the
MONEY / LEGAL TERMS / TRACKING of a brand deal, or about MAKING or RESEARCHING content?

BUSINESS only if primarily about: rates/fees/pricing/quotes/earnings/income/revenue/invoices/
payments/budgets/per-diem/expenses; contracts/SOW/agreements/NDAs/exclusivity terms; negotiating,
accepting, closing or managing a paid deal; or maintaining a brand-deal / financial tracker.

CONTENT (keep) — everything else, EVEN WHEN for a paid/sponsored campaign or naming a brand:
scripts/captions/hooks/concepts/edits; analytics & performance (impressions/views/engagement/reach/
metrics) — a content signal; the fact a post is a brand collab (association, not money); technical
accuracy/research; interview prep/questions; content strategy about a topic; personal non-financial.
When unsure, choose content. Return STRICT JSON list of {"id":<int>,"label":"business"|"content"}."""


def classify(client, batch):
    lines = "\n".join(f'{p["id"]}: {p["title"]} :: {p["snip"]}' for p in batch)
    msg = client.messages.create(model=MODEL, max_tokens=2000, system=RUBRIC,
        messages=[{"role": "user", "content": f"Classify each:\n{lines}\n\nJSON list only."}])
    t = msg.content[0].text.strip()
    if t.startswith("```"):
        t = t.split("```")[1].replace("json", "", 1).strip()
    return json.loads(t)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write visibility='business' (else dry-run)")
    ap.add_argument("--all", action="store_true", help="reclassify all chats, not just untagged")
    args = ap.parse_args()
    client = anthropic.Anthropic()
    conn = db.connect(); cur = conn.cursor()
    scope = "" if args.all else "and nvl(visibility,'content')='content'"
    rows = [{"id": int(r[0]), "title": (r[1] or "")[:90],
             "snip": (r[2] or "").replace("\n", " ")[:180]}
            for r in cur.execute("select post_id, title, dbms_lob.substr(caption,220,1) from posts "
                                 f"where platform_id in ('claude','claude_code','chatgpt') {scope}")]
    if not rows:
        print("no chats to classify"); return
    print(f"classifying {len(rows)} chats ({MODEL})...")
    biz = []
    for i in range(0, len(rows), 25):
        try:
            biz += [r for r in classify(client, rows[i:i+25]) if r.get("label") == "business"]
        except Exception as e:
            print(f"  batch {i}: {str(e)[:70]} (kept as content)")
    ids = [int(r["id"]) for r in biz]
    print(f"\n{len(ids)} chats classified BUSINESS of {len(rows)}")
    if args.apply and ids:
        b = {f"i{j}": v for j, v in enumerate(ids)}
        inlist = ",".join(f":i{j}" for j in range(len(ids)))
        cur.execute(f"update posts set visibility='business' where post_id in ({inlist})", **b)
        conn.commit()
        print(f"tagged {cur.rowcount} posts business (hidden from the content brain).")
    elif ids:
        print("dry-run — re-run with --apply to tag them.")
    conn.close()


if __name__ == "__main__":
    main()
