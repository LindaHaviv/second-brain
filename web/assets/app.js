/* App shell: token gate, API client, view router, and the Home instruments — the search
   command bar, the at-a-glance / latest / memory widgets, and their detail panels. All
   content flows through /api/* (bearer from localStorage). Zero personal defaults — the
   title and every value come from the API. */
(function () {
  "use strict";

  var TOKEN_KEY = "brain_token";
  var state = { token: localStorage.getItem(TOKEN_KEY) || "", authed: false,
                graphBooted: false, widgetsLoaded: false, ov: null };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(id) { return document.getElementById(id); }
  // href values come from ingested content — allow only web schemes so a poisoned
  // url (javascript:, data:) in a post can never execute on click.
  function safeUrl(u) { return /^https?:\/\//i.test(String(u || "")) ? u : ""; }

  // ---- API client -------------------------------------------------------------
  async function api(path) {
    var headers = state.token ? { authorization: "Bearer " + state.token } : {};
    var r = await fetch(path, { headers: headers });
    if (r.status === 401) { showOverlay(); throw new Error("unauthorized"); }
    if (!r.ok) {
      var msg = r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r.json();
  }

  // ---- token overlay ----------------------------------------------------------
  function showOverlay(errMsg) {
    el("overlay").classList.add("show");
    el("tok-err").textContent = errMsg || "";
    el("tok").focus();
  }
  function hideOverlay() { el("overlay").classList.remove("show"); }

  async function tryToken(tok) {
    var headers = tok ? { authorization: "Bearer " + tok } : {};
    var r = await fetch("/api/ping", { headers: headers });
    if (r.status === 401) return null;
    if (!r.ok) throw new Error("server error");
    return r.json();
  }

  el("tok-go").addEventListener("click", submitToken);
  el("tok").addEventListener("keydown", function (e) { if (e.key === "Enter") submitToken(); });
  async function submitToken() {
    var tok = el("tok").value.trim();
    if (!tok) return;
    try {
      var ping = await tryToken(tok);
      if (!ping) { showOverlay("That token didn't work. Check and try again."); return; }
      state.token = tok; localStorage.setItem(TOKEN_KEY, tok);
      hideOverlay(); onAuthed(ping);
    } catch (e) { showOverlay("Couldn't reach the brain. Try again shortly."); }
  }

  // ---- boot -------------------------------------------------------------------
  async function boot() {
    var ping = null;
    try { ping = await tryToken(state.token); } catch (e) {}
    if (!ping) { showOverlay(state.token ? "Your saved token was rejected. Paste a fresh one." : ""); return; }
    onAuthed(ping);
  }

  function onAuthed(ping) {
    state.authed = true;
    if (ping && ping.title) {
      el("brand-title").textContent = ping.title;
      document.title = ping.title;
    }
    if (ping && ping.auth === "public") el("header-meta").textContent = "public view";
    route(location.hash.replace("#", "") || "graph");
  }

  // ---- router (Home / Memory / Agents) -----------------------------------------
  var VIEWS = { graph: 1, memory: 1, agents: 1 };
  var loaded = {};
  document.querySelectorAll("nav button").forEach(function (b) {
    b.addEventListener("click", function () { route(b.dataset.view); });
  });
  function route(view) {
    if (!state.authed) return;
    if (!VIEWS[view]) view = "graph";   // legacy hashes (#search, #status, …) land Home
    document.querySelectorAll("nav button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.view === view);
    });
    document.querySelectorAll(".view").forEach(function (s) {
      s.classList.toggle("active", s.id === "view-" + view);
    });
    location.hash = view;
    if (view === "graph") bootGraph();
    else if (!loaded[view]) { loaded[view] = true; view === "memory" ? loadMemory() : loadAgents(); }
    if (view === "graph" && window.BrainGraph && BrainGraph.__g) BrainGraph.__g.width(el("graph").clientWidth).height(el("graph").clientHeight);
  }

  // ---- agents & tools registry ------------------------------------------------
  async function loadAgents() {
    var box = el("agents-body");
    box.innerHTML = '<div class="loading">loading…</div>';
    try {
      var reg = await api("/api/agents");
      var cats = (reg.categories || []).filter(function (c) { return c.items && c.items.length; });
      box.innerHTML = cats.map(function (c) {
        var cards = c.items.map(function (it) {
          var scope = it.scope === "private" ? "private" : "generic";
          return '<div class="reg-card"><div class="reg-top"><span class="reg-name">' + esc(it.name) +
            '</span><span class="scope ' + scope + '">' + scope + '</span></div>' +
            '<div class="reg-desc">' + esc(it.desc || "") + '</div>' +
            (it.where ? '<div class="reg-where">' + esc(it.where) + "</div>" : "") + "</div>";
        }).join("");
        return '<div class="reg-cat"><div class="reg-cat-h"><h2>' + esc(c.label) +
          '<span class="reg-count">' + c.items.length + "</span></h2>" +
          (c.desc ? '<p>' + esc(c.desc) + "</p>" : "") + '</div><div class="reg-grid">' + cards + "</div></div>";
      }).join("") || '<div class="empty">No agents registered yet.</div>';
    } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  // ---- graph (the Home canvas) --------------------------------------------------
  async function bootGraph() {
    if (state.graphBooted) return;
    state.graphBooted = true;
    // Empty-brain onboarding: if nothing is loaded, greet instead of showing bare instruments.
    try {
      var ov = await api("/api/overview");
      state.ov = ov;
      if (!ov.total_items) {
        el("onboard").hidden = false;
        ["focus-toggle", "rail-toggle", "hud-col", "hud-search"].forEach(function (id) {
          el(id).style.display = "none";
        });
        return;
      }
    } catch (e) { if (e.message === "unauthorized") { state.graphBooted = false; return; } }
    loadWidgets();
    var g = BrainGraph.init(el("graph"), openGraphNode, expandNode);
    BrainGraph.__g = g;
    try {
      var d = await api("/api/graph");
      if (!d.nodes.length) { el("graph").innerHTML = '<div class="empty">Content is loaded, but no wiki topics are compiled yet. Compile the wiki and the graph fills in.</div>'; el("focus-toggle").style.display = "none"; return; }
      BrainGraph.setData(d);
    } catch (e) { if (e.message !== "unauthorized") el("graph").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  // Focus toggle: local-graph mode. When on, clicking a node shows only its neighborhood.
  el("focus-toggle").addEventListener("click", function () {
    var on = BrainGraph.toggleFocus();
    this.classList.toggle("on", on);
    this.textContent = "Focus: " + (on ? "on" : "off");
    flash(on ? "Focus on: click a node to see just its neighbors" : "Focus off: full graph");
  });

  async function expandNode(n) {
    if (!n) return;
    try {
      var d = await api("/api/related?id=" + encodeURIComponent(n.id) + "&k=8");
      var added = BrainGraph.merge(n.id, d);
      flash(added ? "+" + added + " related by meaning" : "no new neighbors");
    } catch (e) {}
  }

  function openGraphNode(n) {
    var p = el("graph-panel");
    if (!n) { p.classList.remove("open"); return; }
    if (n.type === "topic") openWikiPanel(p, n.label);
    else openItemPanel(p, n.id.replace("item:", ""));
  }

  var flashT;
  function flash(msg) {
    var h = document.querySelector(".graph-hint");
    if (!h) return;
    h.textContent = msg; clearTimeout(flashT);
    flashT = setTimeout(function () { h.textContent = "Click a node to read it · double-click to grow the graph by meaning"; }, 2200);
  }

  // ---- Home instruments ---------------------------------------------------------
  function gTile(n, l) {
    return '<div class="g-tile"><div class="n">' + esc(n == null ? "–" : n) + '</div><div class="l">' + esc(l) + "</div></div>";
  }

  async function loadWidgets() {
    if (state.widgetsLoaded) return;
    state.widgetsLoaded = true;
    var ov = state.ov || {};
    var mem = ov.memory || {};
    el("glance-tiles").innerHTML =
      gTile(ov.total_items, "Items") + gTile(ov.wiki_topics, "Topics") +
      gTile(mem.episodic, "Memories") + gTile((ov.by_platform || []).length, "Sources");
    el("mem-pulse").innerHTML =
      gTile(mem.episodic, "Episodic") + gTile(mem.semantic, "Semantic") +
      gTile(mem.procedural, "Tools") + gTile(mem.conversational, "Convo");
    try {
      var items = await api("/api/recent?k=5");
      el("latest-rows").innerHTML = items.length ? items.map(function (r) {
        return '<div class="lrow" data-id="' + esc(r.post_id) + '"><div class="t">' + esc(r.title || "(untitled)") +
          '</div><div class="m">' + esc(r.platform_id || "") + (r.published ? " · " + esc(r.published) : "") + "</div></div>";
      }).join("") : '<div class="empty">No items yet.</div>';
      el("latest-rows").querySelectorAll(".lrow").forEach(function (c) {
        c.addEventListener("click", function () { openItemPanel(el("graph-panel"), c.dataset.id); });
        // widgets and map are one instrument: hovering a row lights its star
        c.addEventListener("mouseenter", function () { BrainGraph.highlight("item:" + c.dataset.id); });
        c.addEventListener("mouseleave", function () { BrainGraph.highlight(null); });
      });
    } catch (e) { el("latest-rows").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  el("glance-more").addEventListener("click", function (e) { e.preventDefault(); openOverviewPanel(el("graph-panel")); });
  el("latest-more").addEventListener("click", function (e) { e.preventDefault(); openFeedPanel(el("graph-panel")); });
  el("mem-more").addEventListener("click", function (e) { e.preventDefault(); route("memory"); });

  // pure-graph mode: hide the instruments (nice on camera, nice on small screens)
  el("rail-toggle").addEventListener("click", function () {
    var hidden = el("hud-col").classList.toggle("collapsed");
    el("hud-search").classList.toggle("collapsed", hidden);
    this.textContent = hidden ? "Show instruments" : "Hide instruments";
  });

  // ---- detail panels for the widgets -------------------------------------------
  function panelShell(title, kind) {
    return '<button class="close" aria-label="close">×</button><h2>' + esc(title) +
      '</h2><div class="kind">' + esc(kind || "") + '</div><div class="pbody"><div class="loading">loading…</div></div>';
  }
  function wirePanel(p) {
    p.classList.add("open");
    p.querySelector(".close").addEventListener("click", function () { p.classList.remove("open"); });
  }

  function barsHTML(rows, labelKey, amber) {
    if (!rows || !rows.length) return '<div class="empty">Nothing here yet.</div>';
    var max = Math.max.apply(null, rows.map(function (r) { return r.count; })) || 1;
    return '<div class="bars">' + rows.slice(0, 12).map(function (r) {
      var w = Math.max(2, Math.round(r.count / max * 100));
      return '<div class="bar-row"><div class="bar-label" title="' + esc(r[labelKey]) + '">' + esc(r[labelKey]) +
        '</div><div class="bar-track"><div class="bar-fill' + (amber ? " amber" : "") + '" style="width:' + w + '%"></div></div>' +
        '<div class="bar-val">' + esc(r.count) + "</div></div>";
    }).join("") + "</div>";
  }

  async function openOverviewPanel(p) {
    p.innerHTML = panelShell("Your brain at a glance", "overview"); wirePanel(p);
    try {
      var ov = state.ov || (state.ov = await api("/api/overview"));
      var pr = ov.published_range || {};
      var html = "";
      if (pr.from && pr.to) {
        html += '<div class="kind">' + esc((ov.total_items || 0) + " items · " + pr.from + " to " + pr.to) + "</div>";
      }
      html += '<h3 class="ov-h">By source</h3>' + barsHTML(ov.by_platform, "platform", false);
      html += '<h3 class="ov-h" style="margin-top:18px">By type</h3>' + barsHTML(ov.by_kind, "kind", true);
      if (ov.series && ov.series.length) html += '<h3 class="ov-h" style="margin-top:18px">Series</h3>' + barsHTML(ov.series, "series", false);
      html += '<h3 class="ov-h" style="margin-top:18px">Source health</h3><pre class="panel-block" id="status-pre">loading…</pre>';
      p.querySelector(".pbody").innerHTML = html;
      try { var st = await api("/api/status"); el("status-pre").textContent = st.panel || "(no status)"; }
      catch (e2) { el("status-pre").textContent = e2.message; }
    } catch (e) { p.querySelector(".pbody").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  async function openFeedPanel(p) {
    p.innerHTML = panelShell("Latest", "recent items"); wirePanel(p);
    try {
      var items = await api("/api/recent?k=30");
      p.querySelector(".pbody").innerHTML = items.length ? items.map(function (r) {
        return '<div class="card" data-id="' + esc(r.post_id) + '"><div class="t">' + esc(r.title || "(untitled)") +
          '</div><div class="m">' + esc(r.platform_id || "") + " · " + esc(r.kind || "") +
          (r.published ? " · " + esc(r.published) : "") + "</div></div>";
      }).join("") : '<div class="empty">No items yet.</div>';
      p.querySelectorAll(".card").forEach(function (c) {
        c.addEventListener("click", function () { openItemPanel(p, c.dataset.id); });
      });
    } catch (e) { p.querySelector(".pbody").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  async function openItemPanel(p, pid) {
    p.innerHTML = panelShell("", "item " + pid); wirePanel(p);
    try {
      var it = await api("/api/item?id=" + encodeURIComponent(pid));
      p.querySelector("h2").textContent = it.title || "(untitled)";
      p.querySelector(".kind").textContent = [it.platform_id, it.kind].filter(Boolean).join(" · ");
      var html = '<div class="body">' + esc(it.caption || "") + "</div>";
      html += '<div class="actions">';
      if (safeUrl(it.url)) html += '<a class="chip" href="' + esc(safeUrl(it.url)) + '" target="_blank" rel="noopener">Open source ↗</a>';
      html += '<button class="ghost" data-rel="item:' + esc(pid) + '">Show related in graph</button></div>';
      p.querySelector(".pbody").innerHTML = html;
      wireRelated(p);
    } catch (e) { p.querySelector(".pbody").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  async function openWikiPanel(p, topic) {
    p.innerHTML = panelShell(topic, "wiki topic"); wirePanel(p);
    try {
      var w = await api("/api/wiki?topic=" + encodeURIComponent(topic));
      var html = '<div class="body">' + renderBody(w.body) + "</div>";
      if (w.citations && w.citations.length) {
        html += '<div class="cites"><h3>' + w.citations.length + " citations</h3>";
        w.citations.forEach(function (c) {
          html += '<a class="cite" ' + (safeUrl(c.url) ? 'href="' + esc(safeUrl(c.url)) + '" target="_blank" rel="noopener"' : "") +
            "><span>" + esc(c.title || "(untitled)") + '</span><br><span class="src">' + esc(c.platform || "") + "</span></a>";
        });
        html += "</div>";
      }
      html += '<div class="actions"><button class="ghost" data-rel="wiki:' + esc(topic) + '">Show related in graph</button></div>';
      p.querySelector(".pbody").innerHTML = html;
      wireRelated(p);
    } catch (e) { p.querySelector(".pbody").innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  function wireRelated(p) {
    var b = p.querySelector("[data-rel]");
    if (!b) return;
    b.addEventListener("click", async function () {
      route("graph");
      if (!BrainGraph.has(b.dataset.rel)) {
        // ensure anchor exists in the graph before expanding
        try { var d = await api("/api/related?id=" + encodeURIComponent(b.dataset.rel) + "&k=8"); BrainGraph.merge(b.dataset.rel, d); } catch (e) {}
      } else { await expandNode({ id: b.dataset.rel }); }
      BrainGraph.focus(b.dataset.rel);
    });
  }

  // light markdown: headings, bullets, bold, inline code. Escape first, so the compiled wiki
  // body (user content) can never inject HTML; then reinstate a safe subset.
  function mdInline(s) {
    return s.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>").replace(/`([^`]+)`/g, "<code>$1</code>");
  }
  function renderBody(body) {
    return esc(body || "").split("\n").map(function (line) {
      var h = line.match(/^(#{1,6})\s+(.*)/);
      if (h) { var lvl = Math.min(h[1].length + 1, 6); return "<h" + lvl + ">" + mdInline(h[2]) + "</h" + lvl + ">"; }
      var b = line.match(/^\s*[-*]\s+(.*)/);
      if (b) return "•  " + mdInline(b[1]);
      return mdInline(line);
    }).join("\n");
  }

  // ---- search: a command bar over the graph, not a separate place ---------------
  el("q").addEventListener("keydown", function (e) {
    if (e.key === "Enter") runSearch();
    else if (e.key === "Escape") hideResults();
  });
  document.addEventListener("keydown", function (e) {
    if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
      e.preventDefault(); route("graph"); el("q").focus(); el("q").select();
    }
  });
  document.addEventListener("click", function (e) {
    if (!el("hud-search").contains(e.target)) hideResults();
  });
  function hideResults() { el("hud-results").hidden = true; }

  async function runSearch() {
    var q = el("q").value.trim();
    if (!q) return;
    var box = el("hud-results");
    box.hidden = false;
    box.innerHTML = '<div class="loading" style="padding:12px 16px">searching…</div>';
    try {
      var d = await api("/api/search?q=" + encodeURIComponent(q) + "&k=12");
      if (!d.results.length) { box.innerHTML = '<div class="empty" style="padding:16px">Nothing found for “' + esc(q) + "”.</div>"; return; }
      box.innerHTML = d.results.map(function (r) {
        var found = (r.found_by || []).join("+");
        return '<div class="r" data-id="' + esc(r.id) + '"><div class="t">' + esc(r.title || "(untitled)") +
          '</div><div class="m">' + esc(r.source || "") + " · " + esc(r.match) + (found ? " · " + esc(found) : "") +
          '</div><div class="snip">' + esc((r.text || "").slice(0, 140)) + "</div></div>";
      }).join("");
      box.querySelectorAll(".r").forEach(function (c) {
        var id = c.dataset.id;
        c.addEventListener("click", function () {
          hideResults();
          if (id.indexOf("wiki:") === 0) openWikiPanel(el("graph-panel"), id.slice(5));
          else openItemPanel(el("graph-panel"), id.replace("item:", ""));
          if (BrainGraph.has(id)) BrainGraph.focus(id);   // fly the map to the result
        });
        c.addEventListener("mouseenter", function () { BrainGraph.highlight(id); });
        c.addEventListener("mouseleave", function () { BrainGraph.highlight(null); });
      });
    } catch (e) { box.innerHTML = '<div class="empty" style="padding:16px">' + esc(e.message) + "</div>"; }
  }

  // ---- memory -----------------------------------------------------------------
  var MEM_KINDS = [
    ["semantic", "Semantic", "What your brain learned about your content. The durable facts it distilled: recurring themes, audience questions, formats, gaps."],
    ["episodic", "Episodic", "What the agent did and how it turned out: a timeline of research runs, each with its outcome."],
    ["procedural", "Procedural", "What the agent can do: its tools, stored as memory so it can pull the relevant ones by meaning."],
    ["conversational", "Conversational", "The running dialogue it keeps as working memory."]
  ];
  function tileHTML(n, l) {
    return '<div class="tile"><div class="n">' + esc(n == null ? "–" : n) + '</div><div class="l">' + esc(l) + "</div></div>";
  }

  // ---- memory lifecycle flow (the "how it works" strip) ----
  // Optional doc link: set localStorage 'brain_memory_doc' to a URL to reveal a "learn more".
  var MEMORY_DOC_URL = localStorage.getItem("brain_memory_doc") || "";
  var FLOW = [
    { t: "Ask", dot: "var(--text-faint)", l: "A question or task arrives.",
      d: "Everything starts with a prompt, from you or a scheduled job." },
    { t: "Recall", dot: "var(--topic)", l: "Pull relevant past experience + tools, by meaning.",
      d: "Before acting, the agent semantic-searches its own memory: what worked on similar tasks (<b>episodic</b> and <b>semantic</b>) and which tools fit (<b>procedural</b>). It starts informed, not blank." },
    { t: "Act", dot: "var(--accent)", l: "Research and answer.",
      d: "It runs the task with the recalled tools, grounded in your content and wiki." },
    { t: "Record", dot: "var(--item)", l: "Write an episodic memory of what happened.",
      d: "One row per run: the task, what it did, the outcome, a reward. Auditable in plain SQL, so you can literally query its <b>success rate per tool</b>." },
    { t: "Consolidate", dot: "var(--topic)", l: "Distill runs into durable facts.",
      d: "Periodically an LLM reads the episodic log and updates the <b>semantic</b> facts (themes, audience, formats, gaps). Experience compounds into reusable knowledge." }
  ];
  var flowDone = false;
  function renderFlow() {
    if (flowDone) return; flowDone = true;
    el("flow-track").innerHTML = FLOW.map(function (s, i) {
      var arrow = i < FLOW.length - 1 ? '<span class="flow-arrow">→</span>' : "";
      return '<div class="flow-stage" data-i="' + i + '"><div class="fs-i">step ' + (i + 1) +
        '</div><div class="fs-t"><span class="kdot" style="background:' + s.dot + '"></span>' + esc(s.t) +
        '</div><div class="fs-l">' + esc(s.l) + "</div></div>" + arrow;
    }).join("");
    var stages = el("flow-track").querySelectorAll(".flow-stage");
    function activate(i) {
      stages.forEach(function (n, j) { n.classList.toggle("active", j === i); n.classList.remove("pulse"); });
      stages[i].classList.add("pulse");
      el("flow-detail").innerHTML = FLOW[i].d;   // trusted static copy (no user input)
    }
    stages.forEach(function (n) { n.addEventListener("click", function () { activate(+n.dataset.i); }); });
    activate(1);   // open on Recall — the most "aha" step for explaining memory
    if (MEMORY_DOC_URL) { var a = el("flow-doc"); a.href = MEMORY_DOC_URL; a.hidden = false; }
  }

  async function loadMemory() {
    renderFlow();
    var body = el("memory-body");
    body.innerHTML = '<div class="loading">loading memory…</div>';
    try {
      var m = await api("/api/memory");
      el("memory-tiles").innerHTML = MEM_KINDS.map(function (k) {
        return tileHTML((m.counts || {})[k[0]], k[1]);
      }).join("");
      var html = "";
      // Semantic — grouped by category
      html += memSection("Semantic", "semantic memory", MEM_KINDS[0][2], (function () {
        if (!m.facts || !m.facts.length) return '<div class="empty">No facts distilled yet.</div>';
        var byCat = {}; m.facts.forEach(function (f) { (byCat[f.category || "other"] = byCat[f.category || "other"] || []).push(f.fact); });
        return Object.keys(byCat).map(function (cat) {
          return '<div class="fact-cat"><h3>' + esc(cat) + "</h3>" +
            byCat[cat].map(function (f) { return '<div class="fact">' + esc(f) + "</div>"; }).join("") + "</div>";
        }).join("");
      })());
      // Episodic — recent actions
      html += memSection("Episodic", "episodic memory", MEM_KINDS[1][2], (function () {
        if (!m.episodic || !m.episodic.length) return '<div class="empty">No runs recorded yet.</div>';
        return m.episodic.map(function (e) {
          var out = (e.outcome || "").toLowerCase();
          return '<div class="ep"><div class="task">' + esc(e.task || "") + '</div><div class="meta">' +
            (e.tool ? "<span>" + esc(e.tool) + "</span>" : "") +
            '<span class="out ' + esc(out) + '">' + esc(e.outcome || "") + "</span>" +
            (e.reward != null ? "<span>reward " + esc(e.reward) + "</span>" : "") +
            (e.created_at ? "<span>" + esc(e.created_at) + "</span>" : "") + "</div></div>";
        }).join("");
      })());
      // Procedural — tools
      html += memSection("Procedural", "procedural memory", MEM_KINDS[2][2], (function () {
        if (!m.tools || !m.tools.length) return '<div class="empty">No tools registered yet.</div>';
        return m.tools.map(function (t) {
          return '<div class="tool"><div class="n">' + esc(t.name) + '<span class="kind">' + esc(t.kind || "") +
            '</span></div><div class="d">' + esc((t.description || "").slice(0, 200)) + "</div></div>";
        }).join("");
      })());
      // Conversational — recent turns (light)
      html += memSection("Conversational", "working memory", MEM_KINDS[3][2], (function () {
        if (!m.conversational || !m.conversational.length) return '<div class="empty">No dialogue recorded yet.</div>';
        return m.conversational.slice(0, 12).map(function (t) {
          return '<div class="turn"><span class="role">' + esc(t.role || "") + "</span>" + esc((t.content || "").slice(0, 220)) + "</div>";
        }).join("");
      })());
      body.innerHTML = html;
    } catch (e) { body.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }
  function memSection(title, tag, whatis, inner) {
    return '<div class="mem-section"><h2>' + esc(title) + '<span class="kindtag">' + esc(tag) +
      '</span></h2><p class="whatis">' + esc(whatis) + "</p>" + inner + "</div>";
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
