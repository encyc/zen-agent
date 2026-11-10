#!/usr/bin/env python3
"""Import dependency graph for the ``zenagent`` package.

Walks ``zenagent/**.py``, parses imports with ``ast``, resolves them to
in-package modules and emits:
  * a JSON graph (nodes = files, links = import edges, plus fan-in/out stats
    and Tarjan SCC cycles), and
  * a self-contained interactive HTML view (force-directed canvas, file view
    and package-level aggregation, click-to-focus, search).

Stdlib only. ``python3 scripts/import_graph.py --json out.json --html out.html``
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PKG = "zenagent"
EXCLUDE_PARTS = {"__pycache__"}


# ---------------------------------------------------------------- scanning ---

def scan_modules() -> dict[str, Path]:
    """Map dotted module name -> file path for every .py under the package."""
    modules: dict[str, Path] = {}
    root = REPO / PKG
    for path in sorted(root.rglob("*.py")):
        if EXCLUDE_PARTS & set(path.parts):
            continue
        rel = path.relative_to(REPO)
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        modules[".".join(parts)] = path
    return modules


def resolve_import(module: str) -> str | None:
    """Resolve a dotted name to a known module, trying package __init__ too."""
    if module in MODULES:
        return module
    while "." in module:
        module = module.rsplit(".", 1)[0]
        if module in MODULES:
            return module
    return None


# ----------------------------------------------------------------- parsing ---

def parse_file(mod: str, path: Path) -> list[str]:
    """Return in-package modules imported by ``mod``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    targets: set[str] = set()

    def add(target: str) -> None:
        resolved = resolve_import(target)
        if resolved and resolved != mod:
            targets.add(resolved)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PKG or alias.name.startswith(PKG + "."):
                    add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:  # relative import: resolve against this file's package
                owner = mod.split(".")
                up = node.level - (1 if node.module else 0)
                base = ".".join(owner[: len(owner) - up] + ([node.module] if node.module else []))
            prefix = f"{PKG}."
            if base == PKG or base.startswith(prefix) or node.level:
                for alias in node.names:
                    if alias.name == "*":
                        add(base)
                    else:
                        add(f"{base}.{alias.name}" if base else alias.name)
    return sorted(targets)


# ------------------------------------------------------------------ cycles ---

def tarjan_scc(nodes: list[str], adj: dict[str, set[str]]) -> list[list[str]]:
    index, low, on_stack = {}, {}, set()
    stack, sccs, counter = [], [], 0

    def visit(v: str) -> None:
        nonlocal counter
        index[v] = low[v] = counter
        counter += 1
        stack.append(v)
        on_stack.add(v)
        for w in adj.get(v, ()):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    sys.setrecursionlimit(max(10000, len(nodes) * 4))
    for v in nodes:
        if v not in index:
            visit(v)
    return sccs


# -------------------------------------------------------------------- main ---

MODULES: dict[str, Path] = {}


def build_graph() -> dict:
    global MODULES
    MODULES = scan_modules()
    deps: dict[str, list[str]] = {}
    for mod, path in MODULES.items():
        try:
            deps[mod] = parse_file(mod, path)
        except SyntaxError as err:
            print(f"warn: skip {mod}: {err}", file=sys.stderr)
            deps[mod] = []

    fan_in: dict[str, int] = defaultdict(int)
    fan_out: dict[str, int] = defaultdict(int)
    links: list[dict] = []
    for src, targets in deps.items():
        fan_out[src] = len(targets)
        for dst in targets:
            fan_in[dst] += 1
            links.append({"source": src, "target": dst})

    sccs = [c for c in tarjan_scc(sorted(MODULES), {m: set(t) for m, t in deps.items()}) if len(c) > 1]
    sccs.sort(key=len, reverse=True)

    nodes = [
        {
            "id": mod,
            "group": group_of(mod),
            "fan_in": fan_in.get(mod, 0),
            "fan_out": fan_out.get(mod, 0),
            "loc": len(MODULES[mod].read_text(encoding="utf-8").splitlines()),
        }
        for mod in sorted(MODULES)
    ]
    return {
        "nodes": nodes,
        "links": links,
        "cycles": sccs,
        "stats": {
            "files": len(nodes),
            "edges": len(links),
            "cycle_groups": len(sccs),
            "no_fan_in": sorted(n["id"] for n in nodes if n["fan_in"] == 0),
        },
    }


def group_of(mod: str) -> str:
    """Second-level package, e.g. zenagent.agent; bare zenagent.* -> zenagent."""
    parts = mod.split(".")
    return ".".join(parts[:2]) if len(parts) > 2 else PKG


# ------------------------------------------------------------------- html ----

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>zenagent import graph</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; margin: 0; }
  body { font: 13px/1.5 -apple-system, "PingFang SC", sans-serif; background: #10141c; color: #dbe2ee; overflow: hidden; }
  #layout { display: flex; height: 100vh; }
  #side { width: 300px; padding: 16px; border-right: 1px solid #262d3d; overflow-y: auto; flex-shrink: 0; }
  #side h1 { font-size: 15px; margin-bottom: 4px; }
  #side .sub { color: #8a93a6; font-size: 12px; margin-bottom: 14px; }
  .stat { background: #1a202e; border-radius: 8px; padding: 10px 12px; margin-bottom: 12px; }
  .stat b { color: #fff; }
  .stat .row { display: flex; justify-content: space-between; padding: 1px 0; }
  h2 { font-size: 12px; color: #8a93a6; text-transform: uppercase; letter-spacing: .06em; margin: 14px 0 6px; }
  .bars .bar { display: flex; align-items: center; gap: 6px; padding: 2px 0; cursor: pointer; border-radius: 4px; }
  .bars .bar:hover { background: #20293a; }
  .bars .nm { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; direction: rtl; text-align: left; }
  .bars .w { height: 8px; border-radius: 4px; background: #3f8cff; }
  .bars .n { width: 22px; text-align: right; color: #8a93a6; font-size: 11px; }
  #legend { display: flex; flex-wrap: wrap; gap: 4px 10px; font-size: 11px; }
  #legend span i { display: inline-block; width: 9px; height: 9px; border-radius: 50%; margin-right: 4px; }
  #cycles .cyc { font-family: ui-monospace, monospace; font-size: 11px; color: #ffb454; padding: 2px 0; word-break: break-all; }
  #main { position: relative; flex: 1; }
  canvas { display: block; }
  #search { position: absolute; top: 12px; left: 12px; }
  #search input { background: #1a202e; border: 1px solid #2c3548; color: #fff; border-radius: 6px; padding: 6px 10px; width: 220px; outline: none; }
  #modes { position: absolute; top: 12px; right: 12px; display: flex; gap: 6px; }
  #modes button, #info .close { background: #1a202e; color: #dbe2ee; border: 1px solid #2c3548; border-radius: 6px; padding: 6px 12px; cursor: pointer; }
  #modes button.on { background: #2d5fd3; border-color: #2d5fd3; color: #fff; }
  #info { position: absolute; bottom: 12px; left: 12px; right: 12px; max-height: 42%; overflow-y: auto; background: rgba(20,25,36,.94); border: 1px solid #2c3548; border-radius: 10px; padding: 12px 14px; display: none; }
  #info h3 { font-size: 13px; margin-bottom: 6px; color: #fff; word-break: break-all; }
  #info .kv { color: #8a93a6; font-size: 12px; margin-bottom: 8px; }
  #info ul { font-family: ui-monospace, monospace; font-size: 11px; columns: 2; column-gap: 24px; }
  #info ul li { padding: 1px 0; cursor: pointer; word-break: break-all; }
  #info ul li:hover { color: #6fa8ff; }
  #info .close { position: absolute; top: 8px; right: 10px; padding: 2px 8px; }
  #tip { position: absolute; pointer-events: none; background: rgba(15,19,28,.95); border: 1px solid #2c3548; border-radius: 6px; padding: 5px 9px; font-size: 11px; display: none; white-space: nowrap; z-index: 5; }
</style>
</head>
<body>
<div id="layout">
  <div id="side">
    <h1>zenagent 依赖视图</h1>
    <div class="sub">文件级 import 关系（仅 zenagent 包内部）</div>
    <div class="stat" id="overview"></div>
    <h2>被依赖最多 (fan-in)</h2><div class="bars" id="fanin"></div>
    <h2>依赖最多 (fan-out)</h2><div class="bars" id="fanout"></div>
    <h2>循环依赖</h2><div id="cycles"></div>
    <h2>包图例</h2><div id="legend"></div>
  </div>
  <div id="main">
    <canvas id="cv"></canvas>
    <div id="search"><input id="q" placeholder="搜索模块，如 tools.bash" autocomplete="off"></div>
    <div id="modes">
      <button id="mFile" class="on">文件视图</button>
      <button id="mPkg">包视图</button>
    </div>
    <div id="info"><button class="close">✕</button><h3></h3><div class="kv"></div><h2>imports ↓</h2><ul id="iOut"></ul><h2>imported by ↑</h2><ul id="iIn"></ul></div>
    <div id="tip"></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const byId = Object.fromEntries(DATA.nodes.map(n => [n.id, n]));
const outAdj = {}, inAdj = {};
DATA.links.forEach(l => { (outAdj[l.source] ??= []).push(l.target); (inAdj[l.target] ??= []).push(l.source); });

const PALETTE = ["#4f8cff","#43c39a","#e2b23e","#e2705e","#b07ce8","#5ecfe2","#d277b8","#8fc74f","#c9905e","#7d8ea8"];
const groupColor = {};
[...new Set(DATA.nodes.map(n => n.group))].forEach((g, i) => groupColor[g] = PALETTE[i % PALETTE.length]);

// ------- stats sidebar -------
const ov = DATA.stats;
document.getElementById('overview').innerHTML =
  `<div class="row"><span>文件</span><b>${ov.files}</b></div>
   <div class="row"><span>内部依赖边</span><b>${ov.edges}</b></div>
   <div class="row"><span>循环组</span><b>${ov.cycle_groups}</b></div>
   <div class="row"><span>无人引用</span><b>${ov.no_fan_in.length}</b></div>`;
document.getElementById('fanin').innerHTML = top10('fan_in').map(n => rowHtml(n, 'fan_in')).join('');
document.getElementById('fanout').innerHTML = top10('fan_out').map(n => rowHtml(n, 'fan_out')).join('');
document.getElementById('cycles').innerHTML =
  ov.cycle_groups === 0 ? '<div style="color:#43c39a">无循环依赖 ✓</div>' :
  DATA.cycles.slice(0, 8).map(c => `<div class="cyc">⭮ ${c.length}: ${c.join(' → ')}</div>`).join('') +
  (DATA.cycles.length > 8 ? `<div class="cyc">…共 ${DATA.cycles.length} 组</div>` : '');
document.getElementById('legend').innerHTML = Object.keys(groupColor).map(g =>
  `<span><i style="background:${groupColor[g]}"></i>${g}</span>`).join('');
function top10(key) { return [...DATA.nodes].sort((a,b) => b[key]-a[key]).slice(0,10); }
function rowHtml(n, key) {
  const max = Math.max(...DATA.nodes.map(x => x[key]), 1);
  return `<div class="bar" data-id="${n.id}"><div class="nm" title="${n.id}">${n.id.replace('zenagent.','')}</div>
    <div class="w" style="width:${Math.max(2, n[key] / max * 60)}px"></div><div class="n">${n[key]}</div></div>`;
}
document.querySelectorAll('.bars').forEach(el => el.addEventListener('click', e => {
  const bar = e.target.closest('.bar');
  if (bar && mode === 'file') select(bar.dataset.id);
}));

// ------- graph state -------
let mode = 'file';
let nodes = [], links = [];
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
let W = 0, H = 0, dpr = window.devicePixelRatio || 1;
function resize() {
  const r = cv.parentElement.getBoundingClientRect();
  W = r.width; H = r.height;
  cv.width = W * dpr; cv.height = H * dpr;
  cv.style.width = W + 'px'; cv.style.height = H + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
window.addEventListener('resize', resize);

let view = { x: 0, y: 0, k: 1 };
let alpha = 1, hovered = null, selected = null, query = '';
let pkgOf = m => { const p = m.split('.'); return p.length > 2 ? p.slice(0,2).join('.') : 'zenagent'; };

function build(fileMode) {
  mode = fileMode ? 'file' : 'pkg';
  if (fileMode) {
    nodes = DATA.nodes.map(n => ({ id: n.id, g: n.group, fi: n.fan_in, fo: n.fan_out }));
    links = DATA.links.map(l => ({ s: l.source, t: l.target }));
  } else {
    const agg = {}, deg = {};
    DATA.links.forEach(l => {
      const a = pkgOf(l.source), b = pkgOf(l.target);
      if (a === b) return;
      const key = a + '|' + b;
      (agg[key] ??= { s: a, t: b, w: 0 }).w++;
      deg[a] = (deg[a] || 0) + 1; deg[b] = (deg[b] || 0) + 1;
    });
    const groups = [...new Set(DATA.nodes.map(n => n.group))];
    nodes = groups.map(g => ({ id: g, g, fi: DATA.nodes.filter(n => n.group === g).reduce((s,n) => s+n.fan_in,0), fo: 0 }));
    links = Object.values(agg);
  }
  const R = Math.min(W, H) * 0.38;
  nodes.forEach((n, i) => {
    const a = i / nodes.length * Math.PI * 2;
    n.x = W/2 + Math.cos(a) * R * (0.4 + Math.random()*0.6);
    n.y = H/2 + Math.sin(a) * R * (0.4 + Math.random()*0.6);
    n.vx = 0; n.vy = 0;
    n.r = 4 + Math.sqrt(Math.min(n.fi, 60)) * 1.4;
  });
  view = { x: 0, y: 0, k: 1 };
  alpha = 1; selected = null; hideInfo();
}

function tick() {
  const rep = mode === 'file' ? 1600 : 9000, rest = mode === 'file' ? 70 : 220;
  for (let i = 0; i < nodes.length; i++) for (let j = i+1; j < nodes.length; j++) {
    const a = nodes[i], b = nodes[j];
    let dx = b.x - a.x, dy = b.y - a.y, d2 = dx*dx + dy*dy || 1;
    if (d2 > 480*480) continue;
    const f = rep / d2 / Math.sqrt(d2) * alpha, d = Math.sqrt(d2);
    a.vx -= dx * f * d * 0.001; a.vy -= dy * f * d * 0.001;
    b.vx += dx * f * d * 0.001; b.vy += dy * f * d * 0.001;
  }
  links.forEach(l => {
    const a = node(l.s), b = node(l.t); if (!a || !b) return;
    let dx = b.x - a.x, dy = b.y - a.y, d = Math.sqrt(dx*dx + dy*dy) || 1;
    const w = l.w || 1, f = (d - rest) * 0.004 * alpha * (mode === 'pkg' ? Math.min(w,4) : 1);
    a.vx += dx/d * f; a.vy += dy/d * f; b.vx -= dx/d * f; b.vy -= dy/d * f;
  });
  nodes.forEach(n => {
    n.vx += (W/2 - n.x) * 0.004 * alpha; n.vy += (H/2 - n.y) * 0.004 * alpha;
    n.vx *= 0.86; n.vy *= 0.86;
    n.x += n.vx * 2; n.y += n.vy * 2;
  });
  alpha *= 0.995;
}
const node = id => nodes.find(n => n.id === id);

function draw() {
  ctx.clearRect(0, 0, W, H);
  const tx = p => [p.x + view.x, p.y + view.y];
  const focus = selected;
  const neigh = focus ? new Set([focus.id, ...(outAdj[focus.id]||[]), ...(inAdj[focus.id]||[])]) : null;
  const q = query.toLowerCase();
  links.forEach(l => {
    const a = node(l.s), b = node(l.t); if (!a || !b) return;
    const [x1,y1] = tx(a), [x2,y2] = tx(b);
    const hot = focus && (l.s === focus.id || l.t === focus.id);
    let match = !q || l.s.toLowerCase().includes(q) || l.t.toLowerCase().includes(q);
    ctx.strokeStyle = hot ? 'rgba(110,168,255,.9)' : (focus ? 'rgba(80,90,110,.10)' : (match ? 'rgba(120,140,175,.22)' : 'rgba(80,90,110,.06)'));
    ctx.lineWidth = hot ? 1.6 : (mode === 'pkg' ? Math.min(3, 0.7 + (l.w||1)*0.35) : 0.8);
    ctx.beginPath(); ctx.moveTo(x1,y1); ctx.lineTo(x2,y2); ctx.stroke();
    if (hot && mode === 'pkg') { // arrow for aggregated weight
      const mx = (x1+x2)/2, my = (y1+y2)/2;
      ctx.fillStyle = '#6fa8ff'; ctx.font = '10px ui-monospace';
      ctx.fillText(l.w, mx+4, my-4);
    }
  });
  nodes.forEach(n => {
    const [x,y] = tx(n);
    n.sx = x; n.sy = y;
    const dim = (focus && !neigh.has(n.id)) || (q && !n.id.toLowerCase().includes(q));
    ctx.globalAlpha = dim ? 0.15 : 1;
    ctx.fillStyle = groupColor[n.g] || '#888';
    ctx.beginPath(); ctx.arc(x, y, n.r, 0, Math.PI*2); ctx.fill();
    if (focus && neigh.has(n.id) && n.id !== focus.id) {
      ctx.strokeStyle = '#6fa8ff'; ctx.lineWidth = 1.5;
      ctx.beginPath(); ctx.arc(x, y, n.r + 2.5, 0, Math.PI*2); ctx.stroke();
    }
    if (n.r > 6 || view.k > 1.4 || focus === n || (q && n.id.toLowerCase().includes(q))) {
      ctx.fillStyle = '#e8edf6'; ctx.font = '11px ui-monospace';
      const label = n.id.replace(/^zenagent\./, '');
      ctx.fillText(label, x + n.r + 4, y + 4);
    }
    ctx.globalAlpha = 1;
  });
}

function frame() {
  tick(); draw();
  requestAnimationFrame(frame);
}

// ------- interaction -------
const tip = document.getElementById('tip');
cv.addEventListener('mousemove', e => {
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  hovered = null;
  for (const n of nodes) if ((mx-n.sx-view.x)**2 + (my-n.sy-view.y)**2 < (n.r+3)**2) { hovered = n; break; }
  cv.style.cursor = hovered ? 'pointer' : 'grab';
  if (hovered && !dragging) {
    tip.style.display = 'block';
    tip.style.left = (mx + 14) + 'px'; tip.style.top = (my + 14) + 'px';
    tip.innerHTML = `<b style="color:${groupColor[hovered.g]}">${hovered.id}</b><br>被依赖 ${hovered.fi} · 依赖 ${mode==='file' ? hovered.fo : '—'}${mode==='file' ? ' · ' + byId[hovered.id].loc + ' 行' : ''}`;
  } else tip.style.display = 'none';
});
let dragging = null, panning = false, px = 0, py = 0;
cv.addEventListener('mousedown', e => {
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  if (hovered) { dragging = hovered; alpha = Math.max(alpha, 0.3); }
  else { panning = true; px = e.clientX; py = e.clientY; }
});
window.addEventListener('mousemove', e => {
  if (dragging) {
    const r = cv.getBoundingClientRect();
    dragging.x = e.clientX - r.left - view.x; dragging.y = e.clientY - r.top - view.y;
    dragging.vx = dragging.vy = 0;
  } else if (panning) { view.x += e.clientX - px; view.y += e.clientY - py; px = e.clientX; py = e.clientY; }
});
window.addEventListener('mouseup', () => { dragging = null; panning = false; });
cv.addEventListener('click', () => { if (hovered) select(hovered.id); });
cv.addEventListener('wheel', e => {
  e.preventDefault();
  const r = cv.getBoundingClientRect();
  const mx = e.clientX - r.left, my = e.clientY - r.top;
  const k = Math.min(6, Math.max(0.3, view.k * (e.deltaY < 0 ? 1.12 : 0.89)));
  view.x = mx - (mx - view.x) * k / view.k; view.y = my - (my - view.y) * k / view.k;
  view.k = k;
}, { passive: false });
window.addEventListener('keydown', e => { if (e.key === 'Escape') { selected = null; query = ''; qEl.value = ''; hideInfo(); } });

function select(id) {
  selected = id === (selected && selected.id) ? null : byId[id];
  if (!selected) { hideInfo(); return; }
  const outs = outAdj[id] || [], ins = inAdj[id] || [];
  const el = document.getElementById('info');
  el.style.display = 'block';
  el.querySelector('h3').textContent = id;
  el.querySelector('.kv').innerHTML =
    `被依赖 <b>${ins.length}</b> · 依赖 <b>${outs.length}</b> · ${byId[id].loc} 行 · 包 ${byId[id].group}`;
  document.getElementById('iOut').innerHTML = outs.map(m => `<li data-id="${m}">${m.replace('zenagent.','')}</li>`).join('');
  document.getElementById('iIn').innerHTML = ins.map(m => `<li data-id="${m}">${m.replace('zenagent.','')}</li>`).join('');
}
function hideInfo() { document.getElementById('info').style.display = 'none'; }
document.querySelector('#info .close').addEventListener('click', () => { selected = null; hideInfo(); });
document.getElementById('info').addEventListener('click', e => {
  const li = e.target.closest('li[data-id]');
  if (li && mode === 'file') select(li.dataset.id);
});

const qEl = document.getElementById('q');
qEl.addEventListener('input', () => { query = qEl.value.trim(); });
document.getElementById('mFile').addEventListener('click', () => { setMode(true); });
document.getElementById('mPkg').addEventListener('click', () => { setMode(false); });
function setMode(file) {
  document.getElementById('mFile').classList.toggle('on', file);
  document.getElementById('mPkg').classList.toggle('on', !file);
  build(file);
}

resize(); build(true); frame();
</script>
</body>
</html>
"""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, help="write the graph JSON here")
    ap.add_argument("--html", type=Path, help="write the interactive HTML view here")
    args = ap.parse_args()

    graph = build_graph()
    s = graph["stats"]
    print(f"files: {s['files']}  edges: {s['edges']}  cycle groups: {s['cycle_groups']}")
    print(f"no fan-in ({len(s['no_fan_in'])}): {', '.join(m.replace('zenagent.', '') for m in s['no_fan_in'][:15])}")

    if args.json:
        args.json.write_text(json.dumps(graph, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"json -> {args.json}")
    if args.html:
        args.html.write_text(
            HTML_TEMPLATE.replace("__DATA__", json.dumps(graph, ensure_ascii=False)),
            encoding="utf-8",
        )
        print(f"html -> {args.html}")


if __name__ == "__main__":
    main()
