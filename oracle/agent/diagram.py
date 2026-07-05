"""Transcript -> Excalidraw VIDEO ASSETS (generic, shared engine).

A video doesn't get one dense mega-diagram — it gets SEVERAL small, clean visual assets,
each shown at a different moment. The LLM designs 2-5 separate assets (each ONE idea on its
own canvas); an accuracy-review pass checks them against the transcript (invented causality,
false grouping, overclaiming); deterministic code converts each into a valid .excalidraw
scene. The model does storytelling and layout; Python guarantees the format and enforces
guards (no duplicate nodes, no arrows crossing boxes, no emoji — Excalidraw's hand-drawn
font can't render them).

Callers supply the LLM (llm.structured) and optional style/feedback strings, so the same
engine serves a local CLI agent and a hosted endpoint.
"""
import json
import random

# (stroke, pastel fill, dark text-on-fill) — the excalidraw-native palette
COLORS = {
    "black":  ("#1e1e1e", "transparent", "#1e1e1e"),
    "blue":   ("#4a9eed", "#a5d8ff", "#2563eb"),
    "green":  ("#22c55e", "#b2f2bb", "#15803d"),
    "orange": ("#f59e0b", "#ffd8a8", "#9a5b0b"),
    "red":    ("#ef4444", "#ffc9c9", "#b91c1c"),
    "purple": ("#8b5cf6", "#d0bfff", "#6d28d9"),
    "yellow": ("#f59e0b", "#fff3bf", "#92700c"),
    "teal":   ("#06b6d4", "#c3fae8", "#0e7490"),
    "pink":   ("#ec4899", "#eebefa", "#be185d"),
    "gray":   ("#495057", "#dee2e6", "#343a40"),
}
FONT_SIZES = {"s": 16, "m": 20, "l": 28, "xl": 36}

_NODE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "shape": {"type": "string", "enum": ["box", "ellipse", "diamond", "zone", "card"]},
        "x": {"type": "number"}, "y": {"type": "number"},
        "w": {"type": "number"}, "h": {"type": "number"},
        "label": {"type": "string"},
        "sublabel": {"type": "string"},
        "color": {"type": "string", "enum": list(COLORS)},
        "filled": {"type": "boolean"},
    },
    "required": ["id", "shape", "x", "y", "w", "h", "label", "color", "filled"],
}
_ART = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "kind": {"type": "string", "enum": ["ellipse", "box", "line"]},
        "x": {"type": "number"}, "y": {"type": "number"},
        "w": {"type": "number"}, "h": {"type": "number"},
        "color": {"type": "string", "enum": list(COLORS)},
        "filled": {"type": "boolean"},
    },
    "required": ["kind", "x", "y", "w", "h", "color", "filled"],
}
_TEXT = {
    "type": "object", "additionalProperties": False,
    "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                   "text": {"type": "string"},
                   "size": {"type": "string", "enum": ["s", "m", "l", "xl"]},
                   "color": {"type": "string", "enum": list(COLORS)}},
    "required": ["x", "y", "text", "size", "color"],
}
_ASSET = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "moment": {"type": "string"},
        "nodes": {"type": "array", "items": _NODE},
        "arrows": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                           "label": {"type": "string"}},
            "required": ["from", "to"]}},
        "texts": {"type": "array", "items": _TEXT},
        "art": {"type": "array", "items": _ART},
    },
    "required": ["name", "moment", "nodes", "arrows", "texts", "art"],
}
SPEC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"title": {"type": "string"},
                   "assets": {"type": "array", "items": _ASSET}},
    "required": ["title", "assets"],
}

SYS = (
    "You design EXCALIDRAW VIDEO ASSETS for a creator's short video, from their transcript. "
    "NOT one big diagram: 2-5 SEPARATE small assets, each shown at a different moment of the "
    "video ('moment' = when, e.g. 'when I introduce the two workloads'). Each asset is ONE "
    "idea on its OWN canvas (1200x900, origin 0,0; center the content, leave GENEROUS "
    "whitespace — at least 100px margins).\n"
    "THE STYLE (mimic faithfully — reference: hand-drawn explainers like 'Evaluating a Model "
    "= Theory Test vs Evaluating an Agent = Road Test'):\n"
    "1. BIG handwritten TITLES: one 'xl' text top-center per asset, or one 'l' title above "
    "EACH panel for comparisons. Titles are the voice; keep them punchy (2-5 words).\n"
    "2. THE COMPARISON PATTERN (the favorite): two large rounded 'zone' panels side by side "
    "(filled, soft pastel, ~480x520 each), each with a short caption inside its top "
    "('MODEL = Theory Test' style, as the zone label) and ONE white inner 'card' holding a "
    "tiny ILLUSTRATION, not text: a dots-grid quiz, a winding path with stops, a bar trio. "
    "Build illustrations from art primitives (8-20 small shapes is fine).\n"
    "3. MINIMAL TEXT: labels <= 5 words; at most 2 small captions per asset beyond titles. "
    "NEVER paragraph boxes. The icon/illustration carries the meaning.\n"
    "4. ICONS ARE THE STARS: composed from art primitives, LARGE and central (a car = body "
    "box + cabin box + 2 wheel ellipses; flags = line + small box; a checklist = circles in "
    "rows with one filled). Place them inside cards/panels, never overlapping text.\n"
    "5. ARROWS: at most 1-2 per asset, only when the idea IS a flow. Prefer none.\n"
    "6. One accent color per asset; soft pastel fills; 'filled' zones at most 2 per asset.\n"
    "7. ASSET LINEUP for a typical short video: (1) the hook/problem as a single bold visual, "
    "(2) the core comparison or concept, (3) optionally one step/flow or payoff card. Each "
    "must stand alone on screen for 3-10 seconds and be instantly readable on a phone.\n"
    "8. ACCURACY BEATS CUTE: a metaphor must illuminate the concept, never distort it."
)

ACC_SYS = (
    "You are a rigorous technical reviewer. You get a video TRANSCRIPT and a set of diagram "
    "ASSET specs (titles, labels, arrows, groupings, metaphors). Find every way any asset "
    "could MISREPRESENT the content: arrows implying wrong causality or direction, groupings "
    "implying false equivalence or hierarchy, labels stating as fact what the transcript "
    "hedges or attributes, anything NOT supported by the transcript, and metaphors that "
    "distort the concept. Also flag typos/transcription stutters in labels and fix them. "
    "Then return the corrected spec (same schema): fix labels/arrows/groupings, cut "
    "unsupported elements. Keep layout and style; change only what accuracy requires. If "
    "nothing is wrong, return the spec unchanged with an empty issues list."
)


def make_spec(llm, transcript, style_notes="", feedback=(), max_tokens=12000):
    """Design + accuracy-review the asset set. `llm` = a module/obj with .structured(sys,
    prompt, schema, max_tokens=). Returns (spec, issues) — issues already corrected in spec."""
    prompt = (
        "TRANSCRIPT (design the video's diagram assets):\n" + transcript[:9000] +
        (f"\n\nCREATOR'S STYLE NOTES (follow them):\n{style_notes}" if style_notes else "") +
        ("\n\nCREATOR'S DESIGN FEEDBACK FROM PAST DIAGRAMS (apply these):\n" +
         "\n".join(f"- {f}" for f in feedback) if feedback else "") +
        "\n\nProduce the asset specs."
    )
    spec = llm.structured(SYS, prompt, SPEC_SCHEMA, max_tokens=max_tokens)
    review = llm.structured(
        ACC_SYS,
        "TRANSCRIPT:\n" + transcript[:9000] + "\n\nASSET SPECS:\n" + json.dumps(spec),
        {"type": "object", "additionalProperties": False,
         "properties": {"issues": {"type": "array", "items": {"type": "string"}},
                        "spec": SPEC_SCHEMA},
         "required": ["issues", "spec"]},
        max_tokens=max_tokens + 2000)
    return (review["spec"] if review["issues"] else spec), review["issues"]


def _seed():
    return random.randint(1, 2 ** 31)


def _el(**kw):
    base = dict(
        id=f"el{_seed()}", angle=0, strokeColor="#1e1e1e", backgroundColor="transparent",
        fillStyle="solid", strokeWidth=2, strokeStyle="solid", roughness=1, opacity=100,
        groupIds=[], frameId=None, roundness=None, seed=_seed(), version=1,
        versionNonce=_seed(), isDeleted=False, boundElements=None, updated=1, link=None,
        locked=False,
    )
    base.update(kw)
    return base


def clean_text(s):
    """Excalidraw's hand-drawn font can't render emoji — strip them (models sneak them in)."""
    return "".join(ch for ch in s if ord(ch) < 0x2200).strip()   # keeps text + arrows (→)


def _text(x, y, text, size, color="#1e1e1e"):
    text = clean_text(text)
    fs = FONT_SIZES.get(size, 20)
    w = max(len(line) for line in text.split("\n")) * fs * 0.55
    h = len(text.split("\n")) * fs * 1.25
    return _el(type="text", x=x, y=y, width=w, height=h, text=text, originalText=text,
               fontSize=fs, fontFamily=1, textAlign="center", verticalAlign="middle",
               strokeColor=color, containerId=None, lineHeight=1.25, baseline=fs,
               autoResize=True)


def _edge_point(n, other):
    cx, cy = n["x"] + n["w"] / 2, n["y"] + n["h"] / 2
    ox, oy = other["x"] + other["w"] / 2, other["y"] + other["h"] / 2
    dx, dy = ox - cx, oy - cy
    if abs(dx) * n["h"] > abs(dy) * n["w"]:
        return (n["x"] + (n["w"] if dx > 0 else 0), cy)
    return (cx, n["y"] + (n["h"] if dy > 0 else 0))


def _crosses(x1, y1, x2, y2, nodes, exclude):
    """Does the segment pass through any non-zone node it doesn't connect? (sampled)"""
    for n in nodes.values():
        if n["id"] in exclude or n["shape"] in ("zone", "card"):
            continue
        for t in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            if n["x"] - 4 < px < n["x"] + n["w"] + 4 and n["y"] - 4 < py < n["y"] + n["h"] + 4:
                return n["label"]
    return None


def _asset_to_scene(asset, log):
    elements, art_elements = [], []
    nodes_by_id, seen_arrows = {}, set()
    shape_type = {"box": "rectangle", "zone": "rectangle", "card": "rectangle",
                  "ellipse": "ellipse", "diamond": "diamond"}
    order = {"zone": 0, "card": 1}
    for n in sorted(asset["nodes"], key=lambda n: order.get(n["shape"], 2)):
        if n["id"] in nodes_by_id:
            continue
        nodes_by_id[n["id"]] = n
        stroke, fill, dark = COLORS[n["color"]]
        if n["shape"] == "zone":
            elements.append(_el(type="rectangle", x=n["x"], y=n["y"],
                                width=n["w"], height=n["h"], strokeColor=stroke,
                                backgroundColor=(fill if n["filled"] else "transparent"),
                                opacity=45, strokeWidth=1.5, roundness={"type": 3}))
            if n.get("label"):
                lbl = clean_text(n["label"])
                elements.append(_text(n["x"] + n["w"] / 2 - len(lbl) * 20 * 0.275,
                                      n["y"] + 22, lbl, "m", dark))
            continue
        if n["shape"] == "card":   # white inner card that holds an illustration
            elements.append(_el(type="rectangle", x=n["x"], y=n["y"],
                                width=n["w"], height=n["h"], strokeColor="#1e1e1e",
                                backgroundColor="#ffffff", strokeWidth=2,
                                roundness={"type": 3}))
            if n.get("label"):
                lbl = clean_text(n["label"])
                elements.append(_text(n["x"] + n["w"] / 2 - len(lbl) * 16 * 0.275,
                                      n["y"] + 14, lbl, "s", "#495057"))
            continue
        elements.append(_el(
            type=shape_type[n["shape"]], x=n["x"], y=n["y"], width=n["w"], height=n["h"],
            strokeColor=stroke, backgroundColor=(fill if n["filled"] else "transparent"),
            roundness=({"type": 3} if n["shape"] == "box" else None)))
        label = clean_text(n["label"] + (f"\n{n['sublabel']}" if n.get("sublabel") else ""))
        if label:
            fs = 20 if len(n["label"]) <= 22 else 16
            th = len(label.split("\n")) * fs * 1.25
            elements.append(_text(
                n["x"] + n["w"] / 2 - max(len(x) for x in label.split("\n")) * fs * 0.275,
                n["y"] + n["h"] / 2 - th / 2, label, "m" if fs == 20 else "s",
                COLORS[n["color"]][2] if n["filled"] else "#1e1e1e"))
    for a in asset["arrows"]:
        src, dst = nodes_by_id.get(a["from"]), nodes_by_id.get(a["to"])
        if not src or not dst or (a["from"], a["to"]) in seen_arrows:
            continue
        seen_arrows.add((a["from"], a["to"]))
        (x1, y1), (x2, y2) = _edge_point(src, dst), _edge_point(dst, src)
        hit = _crosses(x1, y1, x2, y2, nodes_by_id, {a["from"], a["to"]})
        if hit:
            log(f"dropped arrow {a['from']}->{a['to']}: would cross '{hit}'")
            continue
        elements.append(_el(
            type="arrow", x=x1, y=y1, width=x2 - x1, height=y2 - y1,
            points=[[0, 0], [x2 - x1, y2 - y1]], lastCommittedPoint=None,
            startBinding=None, endBinding=None, startArrowhead=None, endArrowhead="arrow"))
        if a.get("label"):
            elements.append(_text((x1 + x2) / 2 - len(a["label"]) * 4.4,
                                  (y1 + y2) / 2 - 24, a["label"], "s", "#495057"))
    for t in asset["texts"]:
        elements.append(_text(t["x"], t["y"], t["text"], t["size"], COLORS[t["color"]][0]))
    for art in asset.get("art", []):
        stroke, fill, _ = COLORS[art["color"]]
        if art["kind"] == "line":
            art_elements.append(_el(
                type="arrow", x=art["x"], y=art["y"], width=art["w"], height=art["h"],
                points=[[0, 0], [art["w"], art["h"]]], lastCommittedPoint=None,
                startBinding=None, endBinding=None, startArrowhead=None, endArrowhead=None,
                strokeColor=stroke))
        else:
            art_elements.append(_el(
                type=("ellipse" if art["kind"] == "ellipse" else "rectangle"),
                x=art["x"], y=art["y"], width=art["w"], height=art["h"],
                strokeColor=stroke,
                backgroundColor=(fill if art["filled"] else "transparent"),
                roundness=({"type": 3} if art["kind"] == "box" else None)))
    return {"type": "excalidraw", "version": 2, "source": "diagram-engine",
            "elements": elements + art_elements,
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}


def spec_to_scenes(spec, log=lambda s: None):
    """Deterministic spec -> [(asset_name, moment, scene)], one scene per video asset."""
    out = []
    for i, asset in enumerate(spec["assets"], 1):
        safe = "".join(c if c.isalnum() or c in "-_" else "-"
                       for c in asset["name"].lower())[:40] or f"asset-{i}"
        out.append((f"{i:02d}-{safe}", asset.get("moment", ""), _asset_to_scene(asset, log)))
    return out
