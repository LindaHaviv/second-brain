"""Tag the creator's **Tech Walks** — her signature series where she interviews a guest while
walking. Notion episodes are titled "Tech Walks:" (tagged directly); the published video posts
(Instagram/YouTube/LinkedIn) often DON'T say "Tech Walks", so this classifies them by style and
sets posts.series='tech_walk'.

  ../.venv/bin/python scripts/classify_series.py            # preview
  ../.venv/bin/python scripts/classify_series.py --apply    # tag series='tech_walk'
"""
import sys, os, json, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "oracle", "agent"))
import db
import anthropic

MODEL = "claude-haiku-4-5"
RUBRIC = """You label a tech creator's published posts as her "Tech Walks" series or not.

The ONE defining criterion: a Tech Walk is her **interviewing or featuring ANOTHER PERSON (a guest)**.
It is always her IN CONVERSATION WITH / INTERVIEWING someone else — never solo.

A TECH WALK (label "tech_walk") = she is interviewing / featuring a **named guest** (founder / CEO /
exec / engineer / creator). Signs: "with <name>", "<name> explains…", "join us as <name>…",
"<topic> by <guest>", a specific person + their company (e.g. "<Name> (Company)", "with <Name>",
"<Name> explains…"). ANY named guest counts — adapt these cues to the guests in your own series.

NOT a tech walk (label "other") = anything that is JUST HER, with no guest — even if she's walking
or it's a vlog: solo explainers ("AI Engineer vs ML Engineer", "<X> in 60 seconds", "crash course"),
personal vlogs ("come with me to re:Invent", "exploring the Spheres", "visiting the Summit"),
motivational one-liners, product-news, promos/CTAs. Being a "walk" or vlog does NOT make it a tech
walk — only a GUEST does. If no other person is clearly being interviewed/featured, choose "other".

Return STRICT JSON list of {"id":<int>,"label":"tech_walk"|"other"}."""


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
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    client = anthropic.Anthropic()
    conn = db.connect(); cur = conn.cursor()
    cur.execute("alter session disable parallel dml")
    rows = [{"id": int(r[0]), "title": (r[1] or "")[:90], "snip": (r[2] or "").replace("\n", " ")[:200]}
            for r in cur.execute("select post_id, title, dbms_lob.substr(caption,240,1) from posts "
                                 "where platform_id in ('instagram','youtube','linkedin') "
                                 "and nvl(visibility,'content')='content' and series is null")]
    if not rows:
        print("nothing to classify"); return
    print(f"classifying {len(rows)} video posts ({MODEL})...")
    hits = []
    for i in range(0, len(rows), 25):
        try:
            hits += [r for r in classify(client, rows[i:i+25]) if r.get("label") == "tech_walk"]
        except Exception as e:
            print(f"  batch {i}: {str(e)[:70]}")
    byid = {r["id"]: r["title"] for r in rows}
    ids = [int(h["id"]) for h in hits]
    print(f"\n{len(ids)} classified TECH WALK (of {len(rows)}):")
    for i in ids:
        print(f"  - {byid.get(i,'')[:65]}")
    if args.apply and ids:
        b = {f"i{j}": v for j, v in enumerate(ids)}
        inlist = ",".join(f":i{j}" for j in range(len(ids)))
        cur.execute(f"update posts set series='tech_walk' where post_id in ({inlist})", **b)
        conn.commit()
        print(f"\ntagged {cur.rowcount} posts series='tech_walk'.")
    elif ids:
        print("\ndry-run — re-run with --apply to tag them.")
    conn.close()


if __name__ == "__main__":
    main()
