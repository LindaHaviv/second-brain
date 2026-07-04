"""Transcript -> Excalidraw diagram engine (generic, shared).

The LLM designs a SIMPLE SPEC (zones, nodes, arrows, notes, icon art, reveal beats); an
accuracy-review pass checks the spec against the transcript (invented causality, false
grouping, overclaiming) and corrects it; deterministic code converts the result into a valid
.excalidraw scene. The model does storytelling and layout; Python guarantees the format and
enforces layout guards (no duplicate nodes, no arrows crossing boxes, no emoji — Excalidraw's
hand-drawn font can't render them).

Callers supply the LLM (llm.structured) and optional style/feedback strings, so the same
engine serves a local CLI agent and a hosted endpoint. See private agents / deploy for usage.
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
FONT_SIZES = {"s": 16, "m": 20, "l": 28}

_NODE = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string"},
        "shape": {"type": "string", "enum": ["box", "ellipse", "diamond", "zone"]},
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
_BEAT = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "name": {"type": "string"},
        "nodes": {"type": "array", "items": _NODE},
        "arrows": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"from": {"type": "string"}, "to": {"type": "string"},
                           "label": {"type": "string"}},
            "required": ["from", "to"]}},
        "notes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {"x": {"type": "number"}, "y": {"type": "number"},
                           "text": {"type": "string"},
                           "size": {"type": "string", "enum": ["s", "m", "l"]}},
            "required": ["x", "y", "text", "size"]}},
        "art": {"type": "array", "items": _ART},
    },
    "required": ["name", "nodes", "arrows", "notes", "art"],
}
SPEC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"title": {"type": "string"},
                   "beats": {"type": "array", "items": _BEAT}},
    "required": ["title", "beats"],
}

SYS = (
    "You design an Excalidraw diagram for a creator's VIDEO, from their transcript. Output a "
    "spec of zones/nodes/arrows/notes/art on one canvas (origin top-left, ~1500 wide).\n"
    "DESIGN RULES:\n"
    "1. LEAD WITH THE PROBLEM/OBJECTIVE, then the flow, then the payoff. Never open with raw steps.\n"
    "2. A CLEAR VISUAL METAPHOR when one fits. Metaphors must ILLUMINATE the concept, never "
    "distort it — accuracy beats cute.\n"
    "3. PANELS: for comparisons/sections use 'zone' shapes — large soft containers (filled=true) "
    "with a short title label; place their content nodes INSIDE with 30px+ margins.\n"
    "4. SIMPLE: fewest boxes that tell the story; labels 2-4 words + one short sublabel max.\n"
    "5. ICONS from primitives via 'art' (Excalidraw cannot render emoji): compose small "
    "recognizable objects from 2-5 ellipses/boxes/lines — a sun (ellipse + 8 short lines), a car "
    "(box + 2 ellipse wheels), a flag (line + small box), a person (ellipse head + box body). "
    "Place art near the node it decorates, never overlapping text. Art draws LAST.\n"
    "6. BEATS = filming reveal order, 3-5, each one idea, all on ONE canvas. Every node appears "
    "EXACTLY ONCE, in the beat where it is first revealed (beats are additive, not slides). "
    "Arrows may reference earlier-beat nodes by id.\n"
    "7. Color with intent: one accent for the key path/answer; zones in soft neutrals; "
    "filled=true only where it matters. 40px+ gaps; arrows connect ADJACENT elements only and "
    "never pass through or over boxes. Notes sit in clear whitespace, overlapping nothing.\n"
    "8. Size boxes to labels (~11px per char + padding, min 140x60)."
)

ACC_SYS = (
    "You are a rigorous technical reviewer. You get a video TRANSCRIPT and a diagram SPEC "
    "(labels, arrows, groupings, metaphor). Find every way the diagram could MISREPRESENT the "
    "content: arrows implying wrong causality or direction, groupings implying false "
    "equivalence or hierarchy, labels stating as fact what the transcript hedges or attributes, "
    "anything in the diagram NOT supported by the transcript, and metaphors that distort the "
    "concept. Then return the corrected spec (same schema): fix labels/arrows/groupings, cut "
    "unsupported elements. Keep layout and style; change only what accuracy requires. If nothing "
    "is wrong, return the spec unchanged with an empty issues list."
)


def make_spec(llm, transcript, style_notes="", feedback=(), max_tokens=8000):
    """Design + accuracy-review a diagram spec. `llm` = a module/obj with .structured(sys,
    prompt, schema, max_tokens=). Returns (spec, issues) — issues already corrected in spec."""
    prompt = (
        "TRANSCRIPT (design the diagram this video needs):\n" + transcript[:9000] +
        (f"\n\nCREATOR'S STYLE NOTES (follow them):\n{style_notes}" if style_notes else "") +
        ("\n\nCREATOR'S DESIGN FEEDBACK FROM PAST DIAGRAMS (apply these):\n" +
         "\n".join(f"- {f}" for f in feedback) if feedback else "") +
        "\n\nProduce the diagram spec."
    )
    spec = llm.structured(SYS, prompt, SPEC_SCHEMA, max_tokens=max_tokens)
    review = llm.structured(
        ACC_SYS,
        "TRANSCRIPT:\n" + transcript[:9000] + "\n\nDIAGRAM SPEC:\n" + json.dumps(spec),
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


def _text(x, y, text, size, color="#1e1e1e", group=None):
    text = clean_text(text)
    fs = FONT_SIZES.get(size, 20)
    w = max(len(line) for line in text.split("\n")) * fs * 0.55
    h = len(text.split("\n")) * fs * 1.25
    return _el(type="text", x=x, y=y, width=w, height=h, text=text, originalText=text,
               fontSize=fs, fontFamily=1, textAlign="center", verticalAlign="middle",
               strokeColor=color, containerId=None, lineHeight=1.25, baseline=fs,
               groupIds=[group] if group else [], autoResize=True)


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
        if n["id"] in exclude or n["shape"] == "zone":
            continue
        for t in (0.15, 0.3, 0.45, 0.6, 0.75, 0.9):
            px, py = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            if n["x"] - 4 < px < n["x"] + n["w"] + 4 and n["y"] - 4 < py < n["y"] + n["h"] + 4:
                return n["label"]
    return None


def spec_to_excalidraw(spec, log=lambda s: None):
    """Deterministic spec -> valid .excalidraw scene, with layout guards."""
    elements, art_elements = [], []
    nodes_by_id, seen_arrows = {}, set()
    shape_type = {"box": "rectangle", "zone": "rectangle",
                  "ellipse": "ellipse", "diamond": "diamond"}
    for bi, beat in enumerate(spec["beats"]):
        group = f"beat{bi + 1}"
        for n in sorted(beat["nodes"], key=lambda n: 0 if n["shape"] == "zone" else 1):
            if n["id"] in nodes_by_id:      # models sometimes re-draw nodes per beat; keep first
                continue
            nodes_by_id[n["id"]] = n
            stroke, fill, dark = COLORS[n["color"]]
            if n["shape"] == "zone":
                elements.append(_el(type="rectangle", x=n["x"], y=n["y"],
                                    width=n["w"], height=n["h"], strokeColor=stroke,
                                    backgroundColor=fill, opacity=35, strokeWidth=1,
                                    roundness={"type": 3}, groupIds=[group]))
                elements.append(_text(n["x"] + 18, n["y"] + 12, n["label"], "m", dark, group))
                continue
            elements.append(_el(
                type=shape_type[n["shape"]], x=n["x"], y=n["y"], width=n["w"], height=n["h"],
                strokeColor=stroke, backgroundColor=(fill if n["filled"] else "transparent"),
                roundness=({"type": 3} if n["shape"] == "box" else None), groupIds=[group]))
            label = clean_text(n["label"] + (f"\n{n['sublabel']}" if n.get("sublabel") else ""))
            fs = 20 if len(n["label"]) <= 22 else 16
            th = len(label.split("\n")) * fs * 1.25
            elements.append(_text(
                n["x"] + n["w"] / 2 - max(len(x) for x in label.split("\n")) * fs * 0.275,
                n["y"] + n["h"] / 2 - th / 2, label, "m" if fs == 20 else "s",
                dark if n["filled"] else "#1e1e1e", group))
        for a in beat["arrows"]:
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
                startBinding=None, endBinding=None, startArrowhead=None, endArrowhead="arrow",
                groupIds=[group]))
            if a.get("label"):
                elements.append(_text((x1 + x2) / 2 - len(a["label"]) * 4.4,
                                      (y1 + y2) / 2 - 24, a["label"], "s", "#495057", group))
        for note in beat["notes"]:
            elements.append(_text(note["x"], note["y"], note["text"], note["size"],
                                  "#495057", group))
        for art in beat.get("art", []):
            stroke, fill, _ = COLORS[art["color"]]
            if art["kind"] == "line":
                art_elements.append(_el(
                    type="arrow", x=art["x"], y=art["y"], width=art["w"], height=art["h"],
                    points=[[0, 0], [art["w"], art["h"]]], lastCommittedPoint=None,
                    startBinding=None, endBinding=None, startArrowhead=None, endArrowhead=None,
                    strokeColor=stroke, groupIds=[group]))
            else:
                art_elements.append(_el(
                    type=("ellipse" if art["kind"] == "ellipse" else "rectangle"),
                    x=art["x"], y=art["y"], width=art["w"], height=art["h"],
                    strokeColor=stroke,
                    backgroundColor=(fill if art["filled"] else "transparent"),
                    roundness=({"type": 3} if art["kind"] == "box" else None),
                    groupIds=[group]))
    return {"type": "excalidraw", "version": 2, "source": "diagram-engine",
            "elements": elements + art_elements,   # art draws last, on top
            "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}
