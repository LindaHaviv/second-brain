/* The Map view: the agent architecture drawn live. Nodes come from /api/agents (the
   auto-detected registry); relationships come from /api/map (declared, because delegation
   cannot be honestly inferred from code). Bands: you -> chief + studio -> reports ->
   skills shelf -> clockwork -> the brain. Edges are typed: solid = delegates/invokes,
   dashed = runs on a schedule. Click a node for its connections; hover to trace. */
(function () {
  "use strict";

  var api = null, esc = null, loaded = false;
  var REG = null, MAP = null;
  var EDGES = [];   // {a, b, label, dash} where a/b are node ids present in the DOM

  function el(id) { return document.getElementById(id); }
  function norm(s) { return String(s || "").trim().toLowerCase(); }

  function cat(reg, key, label) {
    var cs = (reg.categories || []);
    return (cs.filter(function (c) { return c.key === key; })[0] ||
            cs.filter(function (c) { return (c.label || "").toLowerCase() === label; })[0] || { items: [] }).items || [];
  }

  function nodeHTML(id, cls, title, sub, cad) {
    return '<div class="mnode ' + cls + '" data-nid="' + esc(id) + '" tabindex="0">' +
      (cad ? '<span class="mcad">' + esc(cad) + "</span>" : "") +
      '<div class="t">' + esc(title) + '</div>' +
      (sub ? '<div class="d">' + esc(sub) + "</div>" : "") + "</div>";
  }

  function short(s, n) { s = String(s || ""); return s.length > n ? s.slice(0, n - 1) + "…" : s; }

  function cadenceOf(it, name) {
    var c = (MAP.clock || []).filter(function (e) { return norm(e.job) === norm(name); })[0];
    if (c && c.cadence) return c.cadence;
    var m = String(it.desc || "").match(/^(hourly|daily|weekly|monthly)/i);
    return m ? m[1].toLowerCase() : "";
  }

  function build() {
    var agents = cat(REG, "agents", "agents").slice();
    var skills = cat(REG, "skills", "skills");
    var jobs = cat(REG, "scheduled", "scheduled");
    var hide = (MAP.hide || []).map(norm);
    agents = agents.filter(function (a) { return hide.indexOf(norm(a.name)) < 0; });

    var chiefName = MAP.chief || "";
    var chief = agents.filter(function (a) { return norm(a.name) === norm(chiefName); })[0] || null;
    var reportNames = (MAP.reports || []).map(function (e) { return norm(e.to); });
    var reports = agents.filter(function (a) { return reportNames.indexOf(norm(a.name)) >= 0; });
    var studio = agents.filter(function (a) { return a !== chief && reports.indexOf(a) < 0; });
    var doorFor = {};   // agent name -> skill name
    (MAP.doors || []).forEach(function (d) { doorFor[norm(d.agent)] = d.skill; });

    var sheet = el("map-sheet");
    var html = '<svg class="map-edges" id="map-edges" aria-hidden="true"></svg>';

    // -- you: surfaces auto-detected from the integrations the repo actually has --
    var surfaces = cat(REG, "integrations", "integrations").map(function (i) { return i.name; });
    var surfLine = surfaces.length ? "reaches you via: " + surfaces.slice(0, 5).join(" · ")
                                   : "every surface: Claude Code, claude.ai, chat";
    html += '<div class="map-row"><div class="mnode blue myou" data-nid="you" tabindex="0">' +
      '<div class="tag">you</div><div class="t hand" id="map-you-name">You</div>' +
      '<div class="d">' + esc(surfLine) + "</div></div></div>";

    // -- inputs + chief + studio --
    html += '<div class="map-row map-mid">';
    if (chief && chief.inputs && chief.inputs.length) {
      html += '<div class="mgroup teal minputs" id="map-inputs"><h3 class="hand">Inputs</h3>' +
        '<p class="gd">what the chief reads, detected from code</p><div class="mchips">' +
        chief.inputs.map(function (i) { return '<span class="mchip">' + esc(i) + "</span>"; }).join("") +
        "</div></div>";
    }
    if (chief) {
      html += '<div class="mnode gold mchief" data-nid="agent:' + esc(chief.name) + '" tabindex="0">' +
        '<div class="tag">chief of staff · agent</div><div class="t hand">' + esc(chief.name) + '</div>' +
        '<div class="d">' + esc(short(chief.desc, 110)) + "</div></div>";
    }
    if (studio.length) {
      html += '<div class="mgroup mstudio" id="map-studio"><h3 class="hand">The studio</h3>' +
        '<p class="gd">flat specialists: called directly, nothing owns them</p><div class="mgrid">' +
        studio.map(function (a) {
          return nodeHTML("agent:" + a.name, "", a.name, short(a.desc, 54));
        }).join("") + "</div></div>";
    }
    html += "</div>";

    // -- reports --
    if (reports.length) {
      html += '<div class="map-row" id="map-reports">' + reports.map(function (a) {
        return nodeHTML("agent:" + a.name, "gold", a.name, short(a.desc, 48));
      }).join("") + "</div>";
    }

    // -- skills shelf --
    if (skills.length) {
      html += '<div class="mgroup sage mshelf"><h3 class="hand">The skills shelf</h3>' +
        '<p class="gd">a skill is a named procedure invoked from any surface; it is not an agent. Gold chips are doors that open agents.</p><div class="mchips">' +
        skills.map(function (s) {
          var opens = (MAP.doors || []).filter(function (d) { return norm(d.skill) === norm(s.name); })[0];
          var lbl = "/" + s.name + (opens ? " → opens " + opens.agent : "");
          return '<span class="mchip' + (opens ? " door" : "") + '" data-nid="skill:' + esc(s.name) + '" tabindex="0">' + esc(lbl) + "</span>";
        }).join("") + "</div></div>";
    }

    // -- clockwork --
    if (jobs.length) {
      html += '<div class="mgroup terra mclock" id="map-clock"><h3 class="hand">The clockwork</h3>' +
        '<p class="gd">triggers on a cadence; runs without you, silence means healthy</p><div class="mgrid">' +
        jobs.map(function (j) {
          return nodeHTML("job:" + j.name, "", j.name, short(j.desc, 44), cadenceOf(j, j.name));
        }).join("") + "</div></div>";
    }

    // -- brain --
    html += '<div class="map-down"><svg viewBox="0 0 24 30" width="24" height="30">' +
      '<path d="M12 2 C 10 10, 14 18, 12 24" fill="none" stroke="var(--ink)" stroke-width="1.5" stroke-dasharray="5 4"/>' +
      '<path d="M6 18 L12 27 L18 18" fill="none" stroke="var(--ink)" stroke-width="1.5"/></svg>' +
      '<span class="hand">everything reads + writes the brain</span></div>';
    html += '<div class="mgroup mbrain" id="map-brain"><h3 class="hand">The Brain · Oracle 26ai</h3><div class="mchips">' +
      ["your content", "self-compiled wiki", "four memories", "hosted MCP", "this web UI"].map(function (c) {
        return '<span class="mchip">' + c + "</span>";
      }).join("") + "</div></div>";

    // -- legend --
    html += '<div class="map-legend"><span><svg width="34" height="10"><path d="M2 5 L32 5" stroke="var(--ink)" stroke-width="1.4"/></svg> delegates / invokes</span>' +
      '<span><svg width="34" height="10"><path d="M2 5 L32 5" stroke="var(--ink)" stroke-width="1.4" stroke-dasharray="5 4"/></svg> runs on a schedule</span>' +
      '<span class="hand cap">Agents think. Skills are doors. Jobs tick. The brain remembers.</span></div>';

    sheet.innerHTML = html;
    var bt = document.getElementById("brand-title");
    if (bt) el("map-you-name").textContent = bt.textContent;

    // edges (a -> b by node id)
    EDGES = [];
    if (chief) {
      var doorSkill = doorFor[norm(chief.name)];
      EDGES.push({ a: "you", b: "agent:" + chief.name, label: doorSkill ? "/" + doorSkill : "delegates" });
      (MAP.reports || []).forEach(function (e) {
        if (norm(e.from) !== norm(chief.name)) return;
        EDGES.push({ a: "agent:" + chief.name, b: "agent:" + e.to, label: e.label || "" });
      });
    }
    if (studio.length) EDGES.push({ a: "you", b: "#map-studio", label: "you call directly" });
    if (chief && chief.inputs && chief.inputs.length) EDGES.push({ a: "#map-inputs", b: "agent:" + chief.name, label: "reads" });
    (MAP.clock || []).forEach(function (e) {
      EDGES.push({ a: "job:" + e.job, b: "agent:" + e.to, dash: true, label: "" });
    });

    sheet.querySelectorAll("[data-nid]").forEach(function (n) {
      n.addEventListener("click", function () { openNode(n.dataset.nid); });
      n.addEventListener("keydown", function (e) { if (e.key === "Enter") openNode(n.dataset.nid); });
      n.addEventListener("mouseenter", function () { trace(n.dataset.nid); });
      n.addEventListener("mouseleave", function () { trace(null); });
    });
    draw();
  }

  function anchor(id) {
    var sheet = el("map-sheet");
    var n = id.charAt(0) === "#" ? sheet.querySelector(id) : sheet.querySelector('[data-nid="' + id.replace(/"/g, '\\"') + '"]');
    if (!n) return null;
    var r = n.getBoundingClientRect(), s = sheet.getBoundingClientRect();
    return { top: { x: r.left - s.left + r.width / 2, y: r.top - s.top },
             bot: { x: r.left - s.left + r.width / 2, y: r.bottom - s.top },
             left: { x: r.left - s.left, y: r.top - s.top + r.height / 2 },
             right: { x: r.right - s.left, y: r.top - s.top + r.height / 2 } };
  }

  function draw() {
    var svg = el("map-edges");
    if (!svg) return;
    var sheet = el("map-sheet");
    svg.setAttribute("viewBox", "0 0 " + sheet.clientWidth + " " + sheet.clientHeight);
    var out = "";
    EDGES.forEach(function (e, i) {
      var A = anchor(e.a), B = anchor(e.b);
      if (!A || !B) return;
      var x1, y1, x2, y2;
      if (Math.abs(B.top.y - A.top.y) < 40) {          // same band: connect side to side
        if (B.left.x >= A.right.x) { x1 = A.right.x; y1 = A.right.y; x2 = B.left.x; y2 = B.left.y; }
        else { x1 = A.left.x; y1 = A.left.y; x2 = B.right.x; y2 = B.right.y; }
      } else if (B.top.y >= A.bot.y) {                 // downward edge
        x1 = A.bot.x; y1 = A.bot.y; x2 = B.top.x; y2 = B.top.y;
      } else {                                         // upward edge
        x1 = A.top.x; y1 = A.top.y; x2 = B.bot.x; y2 = B.bot.y;
      }
      var cx = (x1 + x2) / 2 + (x2 - x1 !== 0 ? 0 : 14), cy = (y1 + y2) / 2;
      var dash = e.dash ? ' stroke-dasharray="6 5"' : "";
      out += '<path class="medge" data-a="' + esc(e.a) + '" data-b="' + esc(e.b) + '" d="M' + x1 + " " + y1 +
        " Q " + cx + " " + cy + ", " + x2 + " " + y2 + '" fill="none" stroke="var(--ink)" stroke-width="1.4"' + dash + "/>";
      var ang = Math.atan2(y2 - cy, x2 - cx), r = 9;
      [153, -153].forEach(function (d) {
        var a = ang + d * Math.PI / 180;
        out += '<path class="medge" data-a="' + esc(e.a) + '" data-b="' + esc(e.b) + '" d="M' + (x2 + r * Math.cos(a)).toFixed(1) + " " +
          (y2 + r * Math.sin(a)).toFixed(1) + " L" + x2 + " " + y2 + '" stroke="var(--ink)" stroke-width="1.4" fill="none"/>';
      });
      if (e.label) {
        var lw = e.label.length * 6.4 + 14;
        out += '<rect x="' + (cx - lw / 2) + '" y="' + (cy - 14) + '" width="' + lw + '" height="17" rx="8" fill="var(--paper)"/>' +
          '<text x="' + cx + '" y="' + (cy - 2) + '" text-anchor="middle" font-size="11" fill="var(--ink-soft)" class="hand-svg">' + esc(e.label) + "</text>";
      }
    });
    svg.innerHTML = out;
  }

  function trace(id) {
    var svg = el("map-edges");
    if (!svg) return;
    svg.classList.toggle("tracing", !!id);
    svg.querySelectorAll(".medge").forEach(function (p) {
      p.classList.toggle("lit", !!id && (p.dataset.a === id || p.dataset.b === id));
    });
  }

  function relationsOf(name, kind) {
    var out = [];
    (MAP.reports || []).forEach(function (e) {
      if (norm(e.from) === norm(name)) out.push("delegates to " + e.to + (e.label ? " (" + e.label + ")" : ""));
      if (norm(e.to) === norm(name)) out.push("a subtask of " + e.from + (e.label ? " (" + e.label + ")" : ""));
    });
    (MAP.doors || []).forEach(function (d) {
      if (kind === "skill" && norm(d.skill) === norm(name)) out.push("a door: opens the " + d.agent + " agent");
      if (kind === "agent" && norm(d.agent) === norm(name)) out.push("opened by the /" + d.skill + " skill");
    });
    (MAP.clock || []).forEach(function (c) {
      if (kind === "job" && norm(c.job) === norm(name)) out.push("triggers " + c.to + (c.cadence ? " (" + c.cadence + ")" : ""));
      if (kind === "agent" && norm(c.to) === norm(name)) out.push("also run by the clock: " + c.job + (c.cadence ? " (" + c.cadence + ")" : ""));
    });
    return out;
  }

  function findItem(kind, name) {
    var key = { agent: ["agents", "agents"], skill: ["skills", "skills"], job: ["scheduled", "scheduled"] }[kind];
    if (!key) return null;
    return cat(REG, key[0], key[1]).filter(function (i) { return norm(i.name) === norm(name); })[0] || null;
  }

  function openNode(nid) {
    var p = el("map-panel");
    if (nid === "you") { p.classList.remove("open"); return; }
    var kind = nid.split(":")[0], name = nid.slice(kind.length + 1);
    var it = findItem(kind, name) || {};
    var rels = relationsOf(name, kind);
    p.innerHTML = '<button class="close" aria-label="close">×</button><h2>' + esc(name) +
      '</h2><div class="kind">' + esc(kind) + (it.scope ? " · " + esc(it.scope) : "") + '</div>' +
      '<div class="pbody"><div class="body">' + esc(it.desc || "") + "</div>" +
      (it.inputs && it.inputs.length ? '<div class="kind" style="margin-top:10px">reads: ' + esc(it.inputs.join(" · ")) + "</div>" : "") +
      (it.where ? '<div class="kind" style="margin-top:10px">' + esc(it.where) + "</div>" : "") +
      (rels.length ? '<div class="cites"><h3>connections</h3>' + rels.map(function (r) {
        return '<div class="cite">' + esc(r) + "</div>";
      }).join("") + "</div>" : '<div class="empty" style="text-align:left;padding:14px 0">No declared connections. Edges live in map.json.</div>') +
      "</div>";
    p.classList.add("open");
    p.querySelector(".close").addEventListener("click", function () { p.classList.remove("open"); });
  }

  var timer;
  window.addEventListener("resize", function () { clearTimeout(timer); timer = setTimeout(draw, 150); });

  window.BrainMap = {
    load: async function (apiFn, escFn) {
      api = apiFn; esc = escFn;
      if (loaded) { draw(); return; }
      loaded = true;
      try {
        var r = await Promise.all([api("/api/agents"), api("/api/map")]);
        REG = r[0]; MAP = r[1] || {};
        build();
        setTimeout(draw, 450);   // settle pass: re-anchor edges once webfonts finish sizing
      } catch (e) {
        el("map-sheet").innerHTML = '<div class="empty">' + esc(e.message) + "</div>";
      }
    },
    redraw: draw
  };
})();
