/* The graph view: a force-directed map of the brain. Topic hubs (violet) + the content
   items they cite (amber). Citation edges are solid-faint; semantic edges (added on demand
   via /api/related) are dashed. Renders with the vendored force-graph UMD global. */
(function () {
  "use strict";

  var CSS = getComputedStyle(document.documentElement);
  function v(name, fallback) { return (CSS.getPropertyValue(name).trim() || fallback); }
  var C_TOPIC = v('--topic', '#a78bfa'),
      C_ITEM = v('--item', '#f5b971'),
      C_EDGE = v('--topic-dim', '#6d5bd0'),
      C_BG = v('--bg', '#16161c'),
      C_TEXT = v('--text', '#d7d7e0');

  // stable per-platform hue so the same source is always the same tint (no hardcoded list)
  function platformColor(p) {
    if (!p) return C_ITEM;
    var h = 0, s = String(p);
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) % 360;
    return 'hsl(' + h + ',55%,66%)';
  }

  var Graph = null, DATA = { nodes: [], links: [] }, IDS = {}, DEG = {};
  var FOCUS = false, FOCUS_ID = null;   // local-graph mode + the node it's centered on
  var HOVER = null, HOVERN = null;      // hovered node + its neighbor id set (for highlight)

  function recomputeDegree() {
    DEG = {};
    DATA.links.forEach(function (l) {
      var s = l.source.id || l.source, t = l.target.id || l.target;
      DEG[s] = (DEG[s] || 0) + 1; DEG[t] = (DEG[t] || 0) + 1;
    });
  }

  function neighborIds(id) {
    var set = {};
    DATA.links.forEach(function (l) {
      var s = l.source.id || l.source, t = l.target.id || l.target;
      if (s === id) set[t] = true; else if (t === id) set[s] = true;
    });
    return set;
  }

  // render the full graph, or just one node's neighborhood when Focus mode is centered on it
  function render() {
    if (FOCUS && FOCUS_ID) {
      var keep = neighborIds(FOCUS_ID); keep[FOCUS_ID] = true;
      Graph.graphData({
        nodes: DATA.nodes.filter(function (n) { return keep[n.id]; }),
        links: DATA.links.filter(function (l) {
          return keep[l.source.id || l.source] && keep[l.target.id || l.target]; }),
      });
    } else {
      Graph.graphData(DATA);
    }
  }

  function nodeRadius(n) {
    var base = n.type === 'topic' ? 4.5 : 2.8;
    return base + Math.min(6, Math.sqrt(DEG[n.id] || 0) * 1.3);
  }
  function nodeColor(n) { return n.type === 'topic' ? C_TOPIC : platformColor(n.platform); }

  function draw(node, ctx, scale) {
    var r = nodeRadius(node);
    // hover-highlight: dim everything that isn't the hovered node or a direct neighbor
    var dim = HOVER && node.id !== HOVER.id && !(HOVERN && HOVERN[node.id]);
    ctx.globalAlpha = dim ? 0.16 : 1;
    ctx.beginPath();
    ctx.arc(node.x, node.y, r, 0, 2 * Math.PI);
    ctx.fillStyle = nodeColor(node);
    ctx.fill();
    if (node.type === 'topic' && !dim) {
      ctx.shadowColor = C_TOPIC; ctx.shadowBlur = 12; ctx.fill(); ctx.shadowBlur = 0;
    }
    // labels: topics always (when zoomed enough); items only when zoomed in close
    var show = node.type === 'topic' ? scale > 0.7 : scale > 2.4;
    if (show && node.label && !dim) {
      var fs = Math.max(3, (node.type === 'topic' ? 5 : 3.6));
      ctx.font = fs + 'px -apple-system, sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'top';
      ctx.fillStyle = node.type === 'topic' ? C_TEXT : 'rgba(215,215,224,.7)';
      var label = node.label.length > 34 ? node.label.slice(0, 33) + '…' : node.label;
      ctx.fillText(label, node.x, node.y + r + 1);
    }
    ctx.globalAlpha = 1;
  }

  function linkColor(l) {
    if (HOVER) {   // on hover, only the hovered node's edges stay lit
      var s = l.source.id || l.source, t = l.target.id || l.target;
      if (s !== HOVER.id && t !== HOVER.id) return 'rgba(120,120,140,.05)';
      return l.type === 'semantic' ? 'rgba(167,139,250,.7)' : 'rgba(140,120,230,.5)';
    }
    return l.type === 'semantic' ? 'rgba(167,139,250,.45)' : 'rgba(109,91,208,.28)';
  }

  function init(container, onNode, onExpand) {
    Graph = ForceGraph()(container)
      .backgroundColor(C_BG)
      .nodeId('id')
      .nodeLabel(function (n) { return n.label + (n.type === 'item' && n.platform ? '  ·  ' + n.platform : ''); })
      .nodeRelSize(1)
      .nodeCanvasObject(draw)
      .nodePointerAreaPaint(function (node, color, ctx) {
        ctx.fillStyle = color;
        ctx.beginPath(); ctx.arc(node.x, node.y, nodeRadius(node) + 2, 0, 2 * Math.PI); ctx.fill();
      })
      .linkColor(linkColor)
      .linkWidth(function (l) { return l.type === 'semantic' ? 1.2 : 0.7; })
      .linkLineDash(function (l) { return l.type === 'semantic' ? [2, 2] : null; })
      .onNodeClick(function (n) {
        if (FOCUS) { FOCUS_ID = n.id; render(); }   // local-graph mode: center on this node
        onNode(n);
      })
      .onNodeRightClick(function (n) { onExpand(n); })
      .onBackgroundClick(function () { onNode(null); });
    // double-click grows the graph by meaning (force-graph zooms-to-fit on dblclick by
    // default; override it to our expand action — the on-camera moment)
    Graph.onNodeDrag(function () {}); // no-op keeps drag enabled
    container.addEventListener('dblclick', function () {
      var n = Graph.__lastHover; if (n) onExpand(n);
    });
    Graph.onNodeHover(function (n) {
      Graph.__lastHover = n;
      HOVER = n; HOVERN = n ? neighborIds(n.id) : null;
      container.style.cursor = n ? 'pointer' : 'default';
    });
    window.addEventListener('resize', function () {
      Graph.width(container.clientWidth).height(container.clientHeight);
    });
    Graph.width(container.clientWidth).height(container.clientHeight);
    return Graph;
  }

  function setData(d) {
    DATA = { nodes: d.nodes.slice(), links: d.links.slice() };
    IDS = {}; DATA.nodes.forEach(function (n) { IDS[n.id] = true; });
    recomputeDegree();
    render();
  }

  function toggleFocus() {
    FOCUS = !FOCUS;
    if (!FOCUS) FOCUS_ID = null;   // leaving focus mode restores the full graph
    render();
    return FOCUS;
  }

  // merge in nodes/links from /api/related without disturbing the running simulation
  function merge(anchorId, d) {
    var added = 0;
    d.nodes.forEach(function (n) { if (!IDS[n.id]) { IDS[n.id] = true; DATA.nodes.push(n); added++; } });
    var seen = {};
    DATA.links.forEach(function (l) { seen[(l.source.id || l.source) + '>' + (l.target.id || l.target)] = true; });
    d.links.forEach(function (l) {
      var key = l.source + '>' + l.target;
      if (!seen[key] && IDS[l.source] && IDS[l.target]) { seen[key] = true; DATA.links.push(l); }
    });
    recomputeDegree();
    render();
    return added;
  }

  function focus(id) {
    var n = DATA.nodes.filter(function (x) { return x.id === id; })[0];
    if (n && Graph) { Graph.centerAt(n.x, n.y, 600); Graph.zoom(3.5, 600); }
  }

  window.BrainGraph = { init: init, setData: setData, merge: merge, focus: focus,
    toggleFocus: toggleFocus, has: function (id) { return !!IDS[id]; } };
})();
