"""The registry the web UI reads — "one place to see everything built on the brain."

The catalog is AUTO-DETECTED, not hand-maintained: `scripts/build_registry.py` scans the
codebase and writes web/registry.json (generic) + private/server/registry.private.json
(private). This module just reads and merges those, so the UI never depends on a list
someone remembered to update. Regenerate via the scanner (the daily sync runs it).

Generic JSON ships in the public image (web/ is copied); the private JSON ships only in a
private deployment (copied next to this module), so the public template never lists your
personal agents.
"""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
# web/registry.json: /app/web on the image, <repo>/web locally
_WEB = next((p / "web" / "registry.json" for p in (HERE.parent, HERE.parent.parent, HERE.parent.parent.parent)
             if (p / "web" / "registry.json").is_file()), None)
# private JSON: copied beside this module on a private deploy, or in private/server locally
_PRIV = next((p for p in (HERE / "registry.private.json",
                          HERE.parent.parent / "private" / "server" / "registry.private.json",
                          HERE.parent.parent.parent / "private" / "server" / "registry.private.json")
              if p.is_file()), None)


def _load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("categories", []) if path else []
    except Exception:
        return []


def registry():
    """The merged catalog {categories:[{key,label,desc,items:[{name,desc,where,scope}]}]}.
    Generic base + private items (where a private deployment provides them), private items
    folded into the matching category so the UI shows one list."""
    cats = [dict(c, items=list(c["items"])) for c in _load(_WEB)]   # copy so we can extend
    by_key = {c["key"]: c for c in cats}
    for pc in _load(_PRIV):
        c = by_key.get(pc["key"])
        if c is None:
            c = {"key": pc["key"], "label": pc["label"], "desc": pc.get("desc", ""), "items": []}
            by_key[pc["key"]] = c
            cats.append(c)
        c["items"].extend(pc["items"])
    return {"categories": cats}


# ---- the map: DECLARED relationships over the auto-detected nodes ----------------------
# Nodes auto-detect (the registry); edges cannot be honestly inferred from code, so they are
# declared: web/map.json (generic template, ships empty) + map.private.json (a private
# deployment's real structure, copied beside this module exactly like the registry overlay).
_WEB_MAP = next((p / "web" / "map.json" for p in (HERE.parent, HERE.parent.parent, HERE.parent.parent.parent)
                 if (p / "web" / "map.json").is_file()), None)
_PRIV_MAP = next((p for p in (HERE / "map.private.json",
                              HERE.parent.parent / "private" / "server" / "map.private.json",
                              HERE.parent.parent.parent / "private" / "server" / "map.private.json")
                  if p.is_file()), None)


def _load_map(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path else {}
    except Exception:
        return {}


def scheduled():
    """The scheduled-jobs category (name + cadence text), for the Health view's clockwork
    list. Empty when a deployment has no schedules declared."""
    for c in registry().get("categories", []):
        if c.get("key") == "scheduled":
            return [{"name": i.get("name", ""), "desc": i.get("desc", "")} for i in c.get("items", [])]
    return []


def map_data():
    """Merged relationship declarations for the Map view:
    {chief, hide:[names], reports:[{from,to,label}], doors:[{skill,agent}],
     clock:[{job,to,cadence}]}. Private declarations extend (lists) or override (chief)."""
    base = _load_map(_WEB_MAP)
    priv = _load_map(_PRIV_MAP)
    out = {
        "chief": priv.get("chief") or base.get("chief"),
        "hide": list(base.get("hide", [])) + list(priv.get("hide", [])),
        "reports": list(base.get("reports", [])) + list(priv.get("reports", [])),
        "doors": list(base.get("doors", [])) + list(priv.get("doors", [])),
        "clock": list(base.get("clock", [])) + list(priv.get("clock", [])),
    }
    return out
