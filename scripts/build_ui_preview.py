#!/usr/bin/env python3
"""Build a self-contained, clickable PREVIEW of the web UI with generic sample data.

One HTML file, no server, no database: the real web/ assets (CSS, graph engine, map
renderer, fonts inlined as data URIs) over hand-written sample content. Useful for demos,
screenshots, and filming without pointing a camera at real data.

  ./.venv/bin/python scripts/build_ui_preview.py [out.html]

The output holds ONLY the generic sample data written below — nothing is read from your
database or private files, so the file is safe to share.
"""
import base64
import json
import pathlib
import random
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"


def rd(p):
    return (WEB / p).read_text(encoding="utf-8")


def build():
    css = rd("assets/style.css")
    graphjs = rd("assets/graph.js")
    mapjs = rd("assets/map.js")
    fg = rd("assets/vendor/force-graph.min.js").replace("</script", "<\\/script")

    # fonts inline so the single file needs no other hosts
    for fname in ("orbitron", "fraunces-roman", "fraunces-italic", "excalifont"):
        b64 = base64.b64encode((WEB / f"assets/fonts/{fname}.woff2").read_bytes()).decode()
        css = css.replace(f'url("/assets/fonts/{fname}.woff2")', f"url(data:font/woff2;base64,{b64})")

    idx = rd("index.html")

    def section(id_):
        return re.search(r'(<section class="view[^"]*" id="%s">.*?</section>)' % id_, idx, re.S).group(1)

    sections = "\n".join(section(s) for s in ["view-graph", "view-memory", "view-map", "view-agents"])

    # ---- generic sample data ----
    random.seed(7)
    topics = ["Getting Started", "Core Concepts", "Design Principles", "Tools & Workflows",
              "Project Notes", "Data & Analytics", "Fundamentals", "Productivity Systems",
              "Book Notes", "Career Growth", "Community & Events", "Web Development"]
    plats = ["blog", "video", "notes", "chat", "talk"]
    nodes = [{"id": f"wiki:{t}", "type": "topic", "label": t} for t in topics]
    links = []
    pid = 0
    for t in topics:
        for _ in range(random.randint(4, 9)):
            pid += 1
            p = random.choice(plats)
            nodes.append({"id": f"item:{pid}", "type": "item", "label": f"{t} note {pid}",
                          "platform": p, "kind": p})
            links.append({"source": f"wiki:{t}", "target": f"item:{pid}", "type": "citation"})
    for a, b in [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (2, 7), (0, 10), (1, 11), (4, 8), (9, 10)]:
        links.append({"source": f"wiki:{topics[a]}", "target": f"wiki:{topics[b]}", "type": "citation"})
    GRAPH = json.dumps({"nodes": nodes, "links": links})

    STATUS = json.dumps("\n".join([
        "SOURCE            LAST SYNC     ITEMS   STATUS",
        "blog              2d ago          88    ok",
        "video             2d ago          61    ok",
        "notes             5h ago          44    ok",
        "chat              1d ago          34    ok",
        "talk              9d ago          20    stale"]))

    REG = json.dumps({"categories": [
        {"key": "agents", "label": "Agents", "items": [
            {"name": "Chief of staff", "desc": "The on-demand brief that owns the morning: one thing now, top three, health, fresh reports.", "scope": "private", "where": "agents/chief.py",
             "inputs": ["Backlog engine", "Followup engine", "Slack", "Telegram", "NOW.md", "BACKLOG.md", "Pipeline reports"]},
            {"name": "Backlog", "desc": "The to-do that actually prioritizes.", "scope": "private", "where": "agents/backlog.py"},
            {"name": "Freshness", "desc": "Source + loop health, one pager.", "scope": "private", "where": "agents/freshness.py"},
            {"name": "Digest", "desc": "What the brain did last week.", "scope": "generic", "where": "agents/digest.py"},
            {"name": "Inbox fetch", "desc": "Supervised mail fetch, feeds the chaser only.", "scope": "private", "where": "agents/inbox.py"},
            {"name": "Interview prep", "desc": "Brief before a recorded conversation.", "scope": "private", "where": "agents/interview.py"},
            {"name": "Caption drafter", "desc": "The next post's caption, in your tone.", "scope": "private", "where": "agents/captions.py"},
            {"name": "Diagram maker", "desc": "Transcript in, editable diagram out.", "scope": "private", "where": "agents/diagram.py"},
            {"name": "Review gate", "desc": "One model polishes prose, another verifies facts.", "scope": "private", "where": "agents/gate.py"}]},
        {"key": "skills", "label": "Skills", "items": [
            {"name": "chief", "desc": "Run the chief of staff brief.", "scope": "private"},
            {"name": "backlog", "desc": "Capture and review the backlog.", "scope": "private"},
            {"name": "script", "desc": "Draft a shoot-ready short-form script.", "scope": "private"},
            {"name": "spark", "desc": "Turn any source into content angles.", "scope": "private"},
            {"name": "deslop", "desc": "Strip AI patterns from a draft.", "scope": "private"}]},
        {"key": "scheduled", "label": "Scheduled", "items": [
            {"name": "Chief of staff", "desc": "Weekly job.", "scope": "private"},
            {"name": "Backlog-Drain", "desc": "Hourly job.", "scope": "private"},
            {"name": "Sync", "desc": "Daily job.", "scope": "generic"},
            {"name": "Watchdog", "desc": "Weekly job.", "scope": "generic"}]},
        {"key": "integrations", "label": "Integrations", "items": [
            {"name": "Hosted MCP", "desc": "The brain's tools over HTTP.", "scope": "generic"},
            {"name": "Web UI", "desc": "This read-only view.", "scope": "generic"},
            {"name": "Telegram", "desc": "Idea capture + push.", "scope": "generic"},
            {"name": "Slack", "desc": "Brain-dump channel.", "scope": "generic"}]}]})

    MAP = json.dumps({"chief": "Chief of staff", "hide": [],
                      "reports": [{"from": "Chief of staff", "to": "Backlog", "label": "Top-3"},
                                  {"from": "Chief of staff", "to": "Freshness", "label": "health"},
                                  {"from": "Chief of staff", "to": "Digest", "label": "last week"},
                                  {"from": "Chief of staff", "to": "Inbox fetch", "label": "chaser"}],
                      "doors": [{"skill": "chief", "agent": "Chief of staff"},
                                {"skill": "backlog", "agent": "Backlog"}],
                      "clock": [{"job": "Chief of staff", "to": "Chief of staff", "cadence": "weekly"},
                                {"job": "Backlog-Drain", "to": "Backlog", "cadence": "hourly"}]})

    app = """
(function(){
  function el(id){ return document.getElementById(id); }
  function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g,function(c){return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c];}); }
  var SAMPLE = { "/api/agents": __REG__, "/api/map": __MAP__ };
  function fakeApi(p){ return Promise.resolve(SAMPLE[p.split('?')[0]]); }

  // tab switching
  var btns = document.querySelectorAll('nav button');
  btns.forEach(function(b){ b.addEventListener('click', function(){
    btns.forEach(function(x){ x.classList.remove('active'); });
    b.classList.add('active');
    document.querySelectorAll('.view').forEach(function(v){ v.classList.remove('active'); });
    var v = el('view-' + b.dataset.view); if (v) v.classList.add('active');
    if (b.dataset.view === 'graph' && window.__g) window.__g.width(el('graph').clientWidth).height(el('graph').clientHeight);
    if (b.dataset.view === 'map') BrainMap.load(fakeApi, esc);
  }); });

  // graph
  window.__g = BrainGraph.init(el('graph'), function(){}, function(){});
  BrainGraph.setData(__GRAPH__);
  var ft = el('focus-toggle');
  ft.addEventListener('click', function(){ ft.textContent = 'Focus: ' + (BrainGraph.toggleFocus() ? 'on' : 'off'); ft.classList.toggle('on'); });
  el('rail-toggle').addEventListener('click', function(){
    var hidden = el('hud-col').classList.toggle('collapsed');
    el('hud-search').classList.toggle('collapsed', hidden);
    this.textContent = hidden ? 'Show instruments' : 'Hide instruments';
  });

  // widgets
  function gTile(n,l){ return '<div class="g-tile"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>'; }
  el('glance-tiles').innerHTML = gTile(247,'Items')+gTile(14,'Topics')+gTile(41,'Memories')+gTile(5,'Sources');
  el('mem-pulse').innerHTML = gTile(41,'Episodic')+gTile(24,'Semantic')+gTile(5,'Tools')+gTile(8,'Convo');
  var LATEST = [['Field Notes 09','talk · 2026-07-13'],['Build Logs 14','video · 2026-07-12'],
                ['A short on tool choice','blog · 2026-07-10'],['Design principles worth keeping','notes · 2026-07-08'],
                ['On building alongside your AI','chat · 2026-07-06']];
  el('latest-rows').innerHTML = LATEST.map(function(r){ return '<div class="lrow"><div class="t">'+r[0]+'</div><div class="m">'+r[1]+'</div></div>'; }).join('');

  function bars(rows, amber){
    var max = Math.max.apply(null, rows.map(function(r){ return r[1]; }));
    return '<div class="bars">'+rows.map(function(r){ return '<div class="bar-row"><div class="bar-label">'+r[0]+'</div><div class="bar-track"><div class="bar-fill'+(amber?' amber':'')+'" style="width:'+Math.round(r[1]/max*100)+'%"></div></div><div class="bar-val">'+r[1]+'</div></div>'; }).join('')+'</div>';
  }
  function openPanel(title, kind, body){
    var p = el('graph-panel');
    p.innerHTML = '<button class="close" aria-label="close">×</button><h2>'+title+'</h2><div class="kind">'+kind+'</div><div class="pbody">'+body+'</div>';
    p.classList.add('open');
    p.querySelector('.close').addEventListener('click', function(){ p.classList.remove('open'); });
  }
  el('glance-more').addEventListener('click', function(e){
    e.preventDefault();
    openPanel('Your brain at a glance','overview',
      '<h3 class="ov-h">By source</h3>'+bars([['blog',88],['video',61],['notes',44],['chat',34],['talk',20]],false)+
      '<h3 class="ov-h" style="margin-top:18px">By type</h3>'+bars([['post',96],['short',72],['note',44],['chat',23],['talk',12]],true)+
      '<h3 class="ov-h" style="margin-top:18px">Source health</h3><pre class="panel-block">'+__STATUS__+'</pre>');
  });
  el('latest-more').addEventListener('click', function(e){
    e.preventDefault();
    openPanel('Latest','recent items', LATEST.map(function(r){ return '<div class="card"><div class="t">'+r[0]+'</div><div class="m">'+r[1]+'</div></div>'; }).join(''));
  });
  el('mem-more').addEventListener('click', function(e){
    e.preventDefault(); document.querySelector('nav button[data-view="memory"]').click(); window.scrollTo(0,0);
  });

  // search (sample results)
  var RESULTS = [
    ['A short on tool choice','blog · 0.91 · vector','Choosing tools by the problem, not the hype cycle.'],
    ['Design Principles','wiki · 0.88 · vector+keyword','Synthesized page on recurring design principles.'],
    ['Design principles worth keeping','notes · 0.85 · vector','Airy layouts, color as meaning.']];
  el('q').addEventListener('keydown', function(e){
    if (e.key === 'Enter' && el('q').value.trim()){
      var box = el('hud-results'); box.hidden = false;
      box.innerHTML = RESULTS.map(function(r){ return '<div class="r"><div class="t">'+r[0]+'</div><div class="m">'+r[1]+'</div><div class="snip">'+r[2]+'</div></div>'; }).join('');
    } else if (e.key === 'Escape') el('hud-results').hidden = true;
  });
  document.addEventListener('click', function(e){ if (!el('hud-search').contains(e.target)) el('hud-results').hidden = true; });

  // memory page: wire the static loop + tabbed live data
  var DETAILS=['<b>Ask.</b> Everything starts with a prompt, from you or a scheduled job.',
   '<b>Recall.</b> Before acting, the agent semantic-searches its own memory: what worked on similar tasks (<b>episodic</b> and <b>semantic</b>) and which tools fit (<b>procedural</b>). It starts informed, not blank.',
   '<b>Act.</b> It runs the task with the recalled tools, grounded in your content and wiki.',
   '<b>Record.</b> One row per run: the task, what it did, the outcome, a reward. Auditable in plain SQL.',
   '<b>Consolidate.</b> Periodically an LLM reads the episodic log and updates the <b>semantic</b> facts. Experience compounds.'];
  var stages=document.querySelectorAll('#mem-flow .flow-stage');
  stages.forEach(function(d,i){
    d.addEventListener('click', function(){
      stages.forEach(function(x){x.classList.remove('active');});
      d.classList.add('active');
      var fd=el('flow-detail'); fd.classList.remove('swap'); void fd.offsetWidth;
      fd.innerHTML=DETAILS[i]; fd.classList.add('swap');
    });
  });
  if (stages[0]) stages[0].classList.add('active');
  el('flow-detail').innerHTML=DETAILS[0];
  var MSECT={
   semantic:'<div class="mem-section"><h2><span class="kdot" style="background:var(--k-sem)"></span>Semantic <span class="kindtag">semantic memory</span></h2><div class="whatis">The durable facts your brain distilled.</div><div class="fact-cat"><h3>Theme</h3><div class="fact">Reader questions cluster around getting started and tool choice.</div></div></div>',
   episodic:'<div class="mem-section"><h2><span class="kdot" style="background:var(--k-epi)"></span>Episodic <span class="kindtag">episodic memory</span></h2><div class="whatis">What the agent did and how it turned out.</div><div class="ep"><div class="task">Answer: which topics have thin wiki coverage?</div><div class="meta"><span class="out success">success</span><span>tool: search_hybrid</span></div></div></div>',
   procedural:'<div class="mem-section"><h2><span class="kdot" style="background:var(--k-pro)"></span>Procedural <span class="kindtag">procedural memory</span></h2><div class="whatis">What the agent can do.</div><div class="tool"><div class="n">search_hybrid<span class="kind">retrieval</span></div><div class="d">Semantic + keyword search across the brain.</div></div></div>',
   conversational:'<div class="mem-section"><h2><span class="kdot" style="background:var(--k-con)"></span>Conversational <span class="kindtag">conversational memory</span></h2><div class="whatis">The running dialogue.</div><div class="turn"><span class="role">user</span>What did I say about tool choice?</div></div>'};
  el('memory-tiles').innerHTML=[['semantic','24','Semantic','var(--k-sem)'],['episodic','41','Episodic','var(--k-epi)'],['procedural','5','Procedural','var(--k-pro)'],['conversational','8','Conversational','var(--k-con)']].map(function(t){return '<button class="tile mem-tab" data-kind="'+t[0]+'" style="--tab-c:'+t[3]+'; box-shadow: inset 0 2px 0 '+t[3]+'"><div class="n">'+t[1]+'</div><div class="l">'+t[2]+'</div></button>';}).join('');
  var mtabs=document.querySelectorAll('#memory-tiles .mem-tab'), mbody=el('memory-body');
  function mshow(kind){
    mtabs.forEach(function(t){t.classList.toggle('active', t.dataset.kind===kind);});
    mbody.classList.remove('swap'); void mbody.offsetWidth;
    mbody.innerHTML=MSECT[kind]; mbody.classList.add('swap');
  }
  mtabs.forEach(function(t){t.addEventListener('click',function(){mshow(t.dataset.kind);});});
  mshow('semantic');

  // agents page
  el('agents-body').innerHTML='<div class="reg-cat"><div class="reg-cat-h"><h2>Agents <span class="reg-count">2</span></h2><p>Long-running processes built on the brain.</p></div><div class="reg-grid"><div class="reg-card"><div class="reg-top"><span class="reg-name">research loop</span><span class="scope generic">generic</span></div><div class="reg-desc">Answers a question by searching memory, then records what it did.</div><div class="reg-where">agent/research.py</div></div><div class="reg-card"><div class="reg-top"><span class="reg-name">wiki compiler</span><span class="scope generic">generic</span></div><div class="reg-desc">Synthesizes topic pages from raw items on a schedule.</div><div class="reg-where">agent/wiki.py</div></div></div></div>';
})();
"""
    app = (app.replace("__GRAPH__", GRAPH).replace("__STATUS__", STATUS)
              .replace("__REG__", REG).replace("__MAP__", MAP))

    return f"""<title>Second Brain — UI preview (sample data)</title>
<style>
{css}
</style>
<div id="app">
<header><span class="brand"><span class="dot"></span><span id="brand-title">Second Brain</span></span>
<nav>
<button data-view="graph" class="active">Home</button>
<button data-view="memory">Memory</button>
<button data-view="map">Map</button>
<button data-view="agents">Agents</button>
</nav>
<span class="spacer"></span><span class="meta">generic sample data</span></header>
<main>
{sections}
</main>
</div>
<script>{fg}</script>
<script>{graphjs}</script>
<script>{mapjs}</script>
<script>{app}</script>"""


if __name__ == "__main__":
    out = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "ui_preview.html"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size} bytes) — self-contained, sample data only")
