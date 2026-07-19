"""Generate a self-contained viz.html graph viewer for an OKF bundle.

No external resources: the concept graph is embedded as JSON and rendered with a
dependency-free canvas force-directed layout, so the file works offline and
transmits nothing (matching the OKF example bundles' ``viz.html``).
"""

from __future__ import annotations

import json
import re

from .config import Settings
from .models import Concept
from .urls import normalize_url, resolve

_MD_LINK = re.compile(r"\]\(([^)\s]+)\)")


def _content_links(markdown: str, source_url: str, settings: Settings):
    """Yield the normalized targets of links that survive in the distilled body.

    These are the *content* links (nav/footer/menu were already stripped by the
    distiller), which is the linkage we actually want in the graph -- not the raw
    same-site link set, which is mostly boilerplate and produces a hairball.
    """
    for m in _MD_LINK.finditer(markdown):
        target = m.group(1)
        if target.startswith(("mailto:", "tel:", "#")):
            continue
        if target.startswith(("http://", "https://")):
            abs_url = target
        else:
            abs_url = resolve(source_url, target)
        yield normalize_url(abs_url, strip_query=settings.strip_query)


def build_graph(concepts: list[Concept], path_map: dict[str, str], settings: Settings) -> dict:
    nodes = []
    for c in concepts:
        nodes.append(
            {
                "id": c.path,
                "title": c.title or c.path,
                "description": c.description or "",
                "type": c.type or settings.concept_type,
                "tags": c.tags or [],
                "url": c.url,
            }
        )
    seen: set[tuple[str, str]] = set()
    edges = []
    for c in concepts:
        for norm in _content_links(c.markdown, c.url, settings):
            target = path_map.get(norm)
            if target and target != c.path and (c.path, target) not in seen:
                seen.add((c.path, target))
                edges.append({"source": c.path, "target": target})
    return {"nodes": nodes, "edges": edges}


def render_viz(concepts: list[Concept], path_map: dict[str, str], settings: Settings) -> str:
    graph = build_graph(concepts, path_map, settings)
    title = settings.bundle_title or "OKF bundle"
    data_json = json.dumps(graph, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__TITLE__", _escape(title)).replace("__DATA__", data_json)


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ - OKF graph</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; font-family: system-ui, sans-serif; }
  #app { display: flex; height: 100vh; }
  #graph { flex: 1; position: relative; background: #0e1116; }
  canvas { display: block; width: 100%; height: 100%; }
  #side { width: 360px; max-width: 45vw; overflow-y: auto; padding: 16px;
          background: #161b22; color: #e6edf3; border-left: 1px solid #30363d; }
  #side h1 { font-size: 15px; margin: 0 0 4px; }
  #side .muted { color: #8b949e; font-size: 12px; }
  #search { width: 100%; padding: 8px; margin: 10px 0; border-radius: 6px;
            border: 1px solid #30363d; background: #0d1117; color: #e6edf3; }
  .card { margin-top: 12px; padding: 12px; border: 1px solid #30363d;
          border-radius: 8px; background: #0d1117; }
  .card h2 { font-size: 14px; margin: 0 0 6px; }
  .type { display: inline-block; font-size: 11px; padding: 1px 7px; border-radius: 10px;
          background: #1f6feb33; color: #79c0ff; margin-bottom: 6px; }
  .desc { font-size: 13px; color: #c9d1d9; }
  .tags { margin-top: 8px; }
  .tag { display: inline-block; font-size: 11px; padding: 1px 7px; margin: 2px 3px 0 0;
         border-radius: 10px; background: #30363d; color: #adbac7; }
  a { color: #58a6ff; text-decoration: none; word-break: break-all; }
  a:hover { text-decoration: underline; }
  .lnk { display: block; font-size: 12px; padding: 2px 0; cursor: pointer; color: #58a6ff; }
  ul { padding-left: 18px; margin: 6px 0; }
  .hint { font-size: 11px; color: #6e7681; margin-top: 14px; line-height: 1.5; }
</style>
</head>
<body>
<div id="app">
  <div id="graph"><canvas id="cv"></canvas></div>
  <div id="side">
    <h1>__TITLE__</h1>
    <div class="muted" id="stats"></div>
    <input id="search" placeholder="Search concepts...">
    <div id="detail"><div class="hint">Click a node to inspect a concept.
      Scroll to zoom, drag the background to pan, drag a node to move it.</div></div>
  </div>
</div>
<script>
const DATA = __DATA__;
const nodes = DATA.nodes, edges = DATA.edges;
const byId = new Map(nodes.map(n => [n.id, n]));
const outMap = new Map(), inMap = new Map();
nodes.forEach(n => { outMap.set(n.id, []); inMap.set(n.id, []); });
edges.forEach(e => {
  if (outMap.has(e.source)) outMap.get(e.source).push(e.target);
  if (inMap.has(e.target)) inMap.get(e.target).push(e.source);
});
const deg = new Map(nodes.map(n =>
  [n.id, (outMap.get(n.id)||[]).length + (inMap.get(n.id)||[]).length]));

// distinct colors per type
const palette = ["#58a6ff","#3fb950","#d29922","#db61a2","#a371f7","#f85149","#39c5cf","#e3b341"];
const types = [...new Set(nodes.map(n => n.type))];
const typeColor = new Map(types.map((t,i) => [t, palette[i % palette.length]]));

const cv = document.getElementById("cv"), ctx = cv.getContext("2d");
let W=0, H=0, DPR = window.devicePixelRatio || 1;
function resize(){ const r = cv.parentElement.getBoundingClientRect();
  W=r.width; H=r.height; cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); }
window.addEventListener("resize", resize); resize();

// init positions on a circle + jitter (deterministic-ish)
let seed = 42; function rnd(){ seed = (seed*1103515245 + 12345) & 0x7fffffff; return seed/0x7fffffff; }
nodes.forEach((n,i) => { const a = (i/nodes.length)*Math.PI*2;
  n.x = W/2 + Math.cos(a)*Math.min(W,H)*0.35 + (rnd()-0.5)*40;
  n.y = H/2 + Math.sin(a)*Math.min(W,H)*0.35 + (rnd()-0.5)*40; n.vx=0; n.vy=0; });

let view = {x:0, y:0, k:1};
let selected = null, highlight = new Set(), dragNode = null, panning=false, last={x:0,y:0};

// Simulated-annealing cooling: run the O(n^2) layout hot at first, then idle
// once it settles so a large graph doesn't peg a CPU core forever. Interactions
// reheat it briefly.
let alpha = 1.0;
const ALPHA_MIN = 0.02, ALPHA_DECAY = 0.994;
function reheat(a){ alpha = Math.max(alpha, a); }

function tick(){
  const k = 0.02, rep = 1200;
  for (let i=0;i<nodes.length;i++){
    const a = nodes[i];
    for (let j=i+1;j<nodes.length;j++){
      const b = nodes[j]; let dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy+0.01;
      const f = rep/d2; const d=Math.sqrt(d2); const fx=dx/d*f, fy=dy/d*f;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
    }
    a.vx += (W/2 - a.x)*0.0009; a.vy += (H/2 - a.y)*0.0009; // gravity
  }
  edges.forEach(e => { const a=byId.get(e.source), b=byId.get(e.target); if(!a||!b) return;
    let dx=b.x-a.x, dy=b.y-a.y; const d=Math.sqrt(dx*dx+dy*dy)||1; const f=(d-90)*k;
    const fx=dx/d*f, fy=dy/d*f; a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy; });
  nodes.forEach(n => { if(n===dragNode) return;
    n.x += (n.vx *= 0.85) * alpha; n.y += (n.vy *= 0.85) * alpha; });
  alpha *= ALPHA_DECAY;
}

function toScreen(n){ return {x:(n.x+view.x)*view.k, y:(n.y+view.y)*view.k}; }
function draw(){
  ctx.clearRect(0,0,W,H);
  ctx.lineWidth = 1;
  edges.forEach(e => { const a=byId.get(e.source), b=byId.get(e.target); if(!a||!b) return;
    const pa=toScreen(a), pb=toScreen(b);
    const on = highlight.size===0 || highlight.has(e.source) && highlight.has(e.target);
    ctx.strokeStyle = on ? "rgba(139,148,158,0.5)" : "rgba(139,148,158,0.08)";
    ctx.beginPath(); ctx.moveTo(pa.x,pa.y); ctx.lineTo(pb.x,pb.y); ctx.stroke(); });
  nodes.forEach(n => { const p=toScreen(n);
    const r = Math.min(4 + (deg.get(n.id)||0)*1.2, 16) * Math.sqrt(view.k);
    const dim = highlight.size>0 && !highlight.has(n.id);
    ctx.globalAlpha = dim ? 0.2 : 1;
    ctx.fillStyle = typeColor.get(n.type) || "#58a6ff";
    ctx.beginPath(); ctx.arc(p.x,p.y,r,0,Math.PI*2); ctx.fill();
    if (n===selected){ ctx.strokeStyle="#fff"; ctx.lineWidth=2; ctx.stroke(); }
    if (view.k>0.8 || n===selected || (deg.get(n.id)||0)>=4){
      ctx.globalAlpha = dim?0.25:0.9; ctx.fillStyle="#e6edf3"; ctx.font="11px system-ui";
      ctx.fillText(n.title.slice(0,28), p.x+r+3, p.y+4); }
    ctx.globalAlpha=1; });
}
function loop(){ if (alpha > ALPHA_MIN || dragNode) tick(); draw(); requestAnimationFrame(loop); }
loop();

document.getElementById("stats").textContent =
  nodes.length + " concepts - " + edges.length + " links - " + types.length + " types";

function nodeAt(sx, sy){ let best=null, bd=1e9;
  nodes.forEach(n => { const p=toScreen(n); const d=(p.x-sx)**2+(p.y-sy)**2;
    const r = Math.min(4+(deg.get(n.id)||0)*1.2,16)*Math.sqrt(view.k)+4;
    if (d < r*r && d<bd){ bd=d; best=n; } }); return best; }

cv.addEventListener("mousedown", ev => { const n = nodeAt(ev.offsetX, ev.offsetY);
  if (n){ dragNode=n; select(n); reheat(0.4); } else { panning=true; } last={x:ev.offsetX,y:ev.offsetY}; });
window.addEventListener("mousemove", ev => {
  const rect = cv.getBoundingClientRect(); const sx=ev.clientX-rect.left, sy=ev.clientY-rect.top;
  if (dragNode){ dragNode.x = sx/view.k - view.x; dragNode.y = sy/view.k - view.y; dragNode.vx=dragNode.vy=0; }
  else if (panning){ view.x += (sx-last.x)/view.k; view.y += (sy-last.y)/view.k; }
  last={x:sx,y:sy}; });
window.addEventListener("mouseup", () => { dragNode=null; panning=false; });
cv.addEventListener("wheel", ev => { ev.preventDefault();
  const f = ev.deltaY<0 ? 1.1 : 0.9; const mx=ev.offsetX, my=ev.offsetY;
  view.x = mx/(view.k*f) - mx/view.k + view.x; view.y = my/(view.k*f) - my/view.k + view.y;
  view.k *= f; }, {passive:false});

function select(n){ selected=n;
  highlight = new Set([n.id, ...(outMap.get(n.id)||[]), ...(inMap.get(n.id)||[])]);
  const out=(outMap.get(n.id)||[]), inc=(inMap.get(n.id)||[]);
  const esc = s => (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;");
  const linkList = arr => arr.length ? "<ul>"+arr.map(id =>
    `<li class="lnk" data-id="${esc(id)}">${esc((byId.get(id)||{}).title||id)}</li>`).join("")+"</ul>"
    : '<div class="muted">none</div>';
  document.getElementById("detail").innerHTML =
    `<div class="card"><span class="type">${esc(n.type)}</span>
     <h2>${esc(n.title)}</h2>
     <div class="desc">${esc(n.description)}</div>
     <div class="tags">${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join("")}</div>
     <p class="muted" style="margin-top:10px">resource:<br><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.url)}</a></p>
     <p class="muted">file: ${esc(n.id)}</p>
     <h2 style="margin-top:12px">Links to (${out.length})</h2>${linkList(out)}
     <h2>Linked from (${inc.length})</h2>${linkList(inc)}</div>`;
  document.querySelectorAll(".lnk").forEach(el =>
    el.onclick = () => { const t=byId.get(el.dataset.id); if(t) select(t); });
}

document.getElementById("search").addEventListener("input", ev => {
  const q = ev.target.value.toLowerCase().trim();
  if (!q){ highlight = selected ? new Set([selected.id, ...(outMap.get(selected.id)||[]), ...(inMap.get(selected.id)||[])]) : new Set(); return; }
  highlight = new Set(nodes.filter(n =>
    n.title.toLowerCase().includes(q) || (n.tags||[]).join(" ").toLowerCase().includes(q)
    || n.id.toLowerCase().includes(q)).map(n => n.id));
});
</script>
</body>
</html>
"""
