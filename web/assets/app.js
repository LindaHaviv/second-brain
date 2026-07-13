/* App shell: token gate, API client, view router, and the search/wiki/feed/status views.
   All content flows through /api/* (bearer from localStorage). Zero personal defaults — the
   title and every value come from the API. */
(function () {
  "use strict";

  var TOKEN_KEY = "brain_token";
  var state = { token: localStorage.getItem(TOKEN_KEY) || "", authed: false, graphBooted: false };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function el(id) { return document.getElementById(id); }

  // ---- API client -------------------------------------------------------------
  var Auth = { needed: false };
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

  // ---- router -----------------------------------------------------------------
  var loaded = {};
  document.querySelectorAll("nav button").forEach(function (b) {
    b.addEventListener("click", function () { route(b.dataset.view); });
  });
  function route(view) {
    if (!state.authed) return;
    document.querySelectorAll("nav button").forEach(function (b) {
      b.classList.toggle("active", b.dataset.view === view);
    });
    document.querySelectorAll(".view").forEach(function (s) {
      s.classList.toggle("active", s.id === "view-" + view);
    });
    location.hash = view;
    if (view === "graph") bootGraph();
    else if (!loaded[view]) { loaded[view] = true; loadView(view); }
    if (view === "graph" && window.BrainGraph && BrainGraph.__g) BrainGraph.__g.width(el("graph").clientWidth).height(el("graph").clientHeight);
  }

  function loadView(view) {
    if (view === "wiki") loadWiki();
    else if (view === "memory") loadMemory();
    else if (view === "feed") loadFeed();
    else if (view === "status") loadStatus();
    else if (view === "search") el("q").focus();
  }

  // ---- graph ------------------------------------------------------------------
  async function bootGraph() {
    if (state.graphBooted) return;
    state.graphBooted = true;
    // Empty-brain onboarding: if nothing is loaded, greet instead of showing a bare canvas.
    try {
      var ov = await api("/api/overview");
      if (!ov.total_items) { el("onboard").hidden = false; el("focus-toggle").style.display = "none"; return; }
    } catch (e) { if (e.message === "unauthorized") return; }
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

  // ---- shared panels ----------------------------------------------------------
  function panelShell(title, kind) {
    return '<button class="close" aria-label="close">×</button><h2>' + esc(title) +
      '</h2><div class="kind">' + esc(kind || "") + '</div><div class="pbody"><div class="loading">loading…</div></div>';
  }
  function wirePanel(p) {
    p.classList.add("open");
    p.querySelector(".close").addEventListener("click", function () { p.classList.remove("open"); });
  }

  async function openItemPanel(p, pid) {
    p.innerHTML = panelShell("", "item " + pid); wirePanel(p);
    try {
      var it = await api("/api/item?id=" + encodeURIComponent(pid));
      p.querySelector("h2").textContent = it.title || "(untitled)";
      p.querySelector(".kind").textContent = [it.platform_id, it.kind].filter(Boolean).join(" · ");
      var html = '<div class="body">' + esc(it.caption || "") + "</div>";
      html += '<div class="actions">';
      if (it.url) html += '<a class="chip" href="' + esc(it.url) + '" target="_blank" rel="noopener">Open source ↗</a>';
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
          html += '<a class="cite" ' + (c.url ? 'href="' + esc(c.url) + '" target="_blank" rel="noopener"' : "") +
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

  // very light markdown: headings + keep line breaks (body is trusted-but-escaped user content)
  function renderBody(body) {
    return esc(body || "").split("\n").map(function (line) {
      var m = line.match(/^(#{1,3})\s+(.*)/);
      if (m) return "<h" + (m[1].length + 1) + ">" + m[2] + "</h" + (m[1].length + 1) + ">";
      return line;
    }).join("\n");
  }

  // ---- search -----------------------------------------------------------------
  el("q-go").addEventListener("click", runSearch);
  el("q").addEventListener("keydown", function (e) { if (e.key === "Enter") runSearch(); });
  async function runSearch() {
    var q = el("q").value.trim();
    if (!q) return;
    var box = el("search-results");
    box.innerHTML = '<div class="loading">searching…</div>';
    try {
      var d = await api("/api/search?q=" + encodeURIComponent(q) + "&k=15");
      if (!d.results.length) { box.innerHTML = '<div class="empty">Nothing found for “' + esc(q) + "”.</div>"; return; }
      box.innerHTML = d.results.map(function (r) {
        var found = (r.found_by || []).join("+");
        return '<div class="card" data-id="' + esc(r.id) + '"><div class="t">' + esc(r.title || "(untitled)") +
          '</div><div class="m">' + esc(r.source || "") + " · " + esc(r.match) + (found ? " · " + esc(found) : "") +
          '</div><div class="snip">' + esc(r.text || "") + "</div></div>";
      }).join("");
      box.querySelectorAll(".card").forEach(function (c) {
        c.addEventListener("click", function () {
          var id = c.dataset.id;
          if (id.indexOf("wiki:") === 0) { route("graph"); openGraphNode({ type: "topic", label: id.slice(5) }); }
          else openItemPanel(el("graph-panel"), id.replace("item:", "")), route("graph");
        });
      });
    } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  // ---- wiki topic list --------------------------------------------------------
  async function loadWiki() {
    var box = el("topic-chips");
    box.innerHTML = '<div class="loading">loading topics…</div>';
    try {
      var topics = await api("/api/topics");
      if (!topics.length) { box.innerHTML = '<div class="empty">No wiki topics compiled yet.</div>'; return; }
      box.innerHTML = "";
      topics.forEach(function (t) {
        var c = document.createElement("div");
        c.className = "chip"; c.textContent = t;
        c.addEventListener("click", function () { openWikiPanel(el("wiki-panel"), t); });
        box.appendChild(c);
      });
    } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
  }

  // ---- feed -------------------------------------------------------------------
  async function loadFeed() {
    var box = el("feed-list");
    box.innerHTML = '<div class="loading">loading…</div>';
    try {
      var items = await api("/api/recent?k=30");
      if (!items.length) { box.innerHTML = '<div class="empty">No items yet.</div>'; return; }
      box.innerHTML = items.map(function (r) {
        return '<div class="card" data-id="' + esc(r.post_id) + '"><div class="t">' + esc(r.title || "(untitled)") +
          '</div><div class="m">' + esc(r.platform_id || "") + " · " + esc(r.kind || "") +
          (r.published ? " · " + esc(r.published) : "") + "</div></div>";
      }).join("");
      box.querySelectorAll(".card").forEach(function (c) {
        c.addEventListener("click", function () { route("graph"); openItemPanel(el("graph-panel"), c.dataset.id); });
      });
    } catch (e) { box.innerHTML = '<div class="empty">' + esc(e.message) + "</div>"; }
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
  async function loadMemory() {
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

  // ---- status -----------------------------------------------------------------
  async function loadStatus() {
    try {
      var ov = await api("/api/overview");
      var mem = ov.memory || {};
      var tiles = [
        ["items", ov.total_items], ["platforms", (ov.by_platform || []).length],
        ["wiki topics", ov.wiki_topics], ["series", (ov.series || []).length],
        ["facts", mem.semantic], ["memories", mem.episodic]
      ];
      el("overview-tiles").innerHTML = tiles.map(function (t) {
        return '<div class="tile"><div class="n">' + esc(t[1] == null ? "–" : t[1]) + '</div><div class="l">' + esc(t[0]) + "</div></div>";
      }).join("");
    } catch (e) {}
    try {
      var st = await api("/api/status");
      el("status-panel").textContent = st.panel || "(no status)";
    } catch (e) { el("status-panel").textContent = e.message; }
  }

  window.addEventListener("DOMContentLoaded", boot);
})();
