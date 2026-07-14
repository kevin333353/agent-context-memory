"""Dashboard: embedded HTML page + JSON API, served under /__acm/.

Pure stdlib. ``handle(path, method, store)`` returns
``(status_code, content_type, body_bytes)`` so any server (the proxy, or a
standalone http.server) can dispatch to it. The HTML is fully self-contained —
inline CSS/JS, no external requests — matching the project's offline posture.
"""

from __future__ import annotations

import json
from typing import Optional
from urllib.parse import urlparse, parse_qs

from .store import UsageStore
from . import pricing

PREFIX = "/__acm"


def _json(obj) -> tuple[int, str, bytes]:
    return 200, "application/json; charset=utf-8", json.dumps(obj).encode("utf-8")


def _augment_source_rows(rows: list[dict]) -> list[dict]:
    for r in rows:
        if r.get("source") == "claude":
            r["illustrative_cache_savings_usd"] = round(
                pricing.cache_savings_usd(None, r.get("cache_read_tokens", 0)), 4
            )
        total_in = (r.get("input_tokens", 0) + r.get("cache_creation_tokens", 0)
                    + r.get("cache_read_tokens", 0))
        r["total_input_tokens"] = total_in
        r["cache_hit_ratio"] = (r.get("cache_read_tokens", 0) / total_in) if total_in else 0.0
    return rows


def api_summary(store: UsageStore) -> dict:
    overall = store.summary()
    overall["illustrative_cache_savings_usd"] = round(
        pricing.cache_savings_usd(None, overall.get("cache_read_tokens", 0)), 4
    )
    return {
        "overall": overall,
        "by_source": _augment_source_rows(store.by_source()),
    }


def handle(path: str, method: str, store: UsageStore) -> Optional[tuple[int, str, bytes]]:
    """Return a response tuple, or None if this path is not ours to serve."""
    parsed = urlparse(path)
    p = parsed.path
    if not (p == PREFIX or p.startswith(PREFIX + "/") or p == PREFIX + "/"):
        return None
    if method not in ("GET", "HEAD"):
        return 405, "text/plain; charset=utf-8", b"method not allowed"

    route = p[len(PREFIX):].rstrip("/") or "/"
    try:
        if route == "/":
            return 200, "text/html; charset=utf-8", INDEX_HTML.encode("utf-8")
        if route == "/api/summary":
            return _json(api_summary(store))
        if route == "/api/sources":
            return _json(_augment_source_rows(store.by_source()))
        if route == "/api/models":
            return _json(_augment_source_rows(store.by_model()))
        if route == "/api/events":
            qs = parse_qs(parsed.query)
            limit = int((qs.get("limit") or ["100"])[0])
            offset = int((qs.get("offset") or ["0"])[0])
            return _json(store.recent(limit=min(limit, 1000), offset=offset))
    except Exception as exc:  # dashboard must never take down the server
        return 500, "application/json; charset=utf-8", json.dumps({"error": str(exc)}).encode()
    return 404, "text/plain; charset=utf-8", b"not found"


INDEX_HTML = """<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Agent Context Memory — Usage</title>
<style>
  :root { color-scheme: light dark; --fg:#1a1a1a; --bg:#fbfaf7; --mut:#6b6b6b;
          --card:#ffffff; --line:#e6e3dc; --accent:#c96a3f; --claude:#c96a3f; --codex:#3f7cc9; }
  @media (prefers-color-scheme: dark) {
    :root { --fg:#e8e6e1; --bg:#17181a; --mut:#9a9a9a; --card:#212225; --line:#33353a; } }
  * { box-sizing:border-box; }
  body { margin:0; font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; color:var(--fg);
         background:var(--bg); padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; } .sub { color:var(--mut); font-size:13px; margin:0 0 20px; }
  .grid { display:grid; gap:14px; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); margin-bottom:22px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; }
  .k { color:var(--mut); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }
  .v { font-size:24px; font-weight:600; margin-top:4px; } .v small { font-size:13px; color:var(--mut); font-weight:400; }
  table { width:100%; border-collapse:collapse; background:var(--card); border:1px solid var(--line);
          border-radius:10px; overflow:hidden; margin-bottom:22px; font-size:13px; }
  th,td { text-align:right; padding:8px 12px; border-bottom:1px solid var(--line); }
  th:first-child,td:first-child { text-align:left; } th { color:var(--mut); font-weight:600; }
  tr:last-child td { border-bottom:none; }
  .bar { height:8px; border-radius:4px; background:var(--line); overflow:hidden; }
  .bar > span { display:block; height:100%; background:var(--accent); }
  .tag { display:inline-block; width:9px; height:9px; border-radius:2px; margin-right:6px; vertical-align:middle; }
  .note { color:var(--mut); font-size:12px; margin-top:-12px; margin-bottom:22px; }
  h2 { font-size:15px; margin:0 0 10px; }
</style></head>
<body>
  <h1>Agent Context Memory — 真實用量</h1>
  <p class="sub">Claude 走 proxy、Codex 讀本機 log。數字為實際觀測的 token；金額僅為 API 定價換算，非帳單。</p>
  <div id="cards" class="grid"></div>
  <h2>來源對照 (Claude vs Codex)</h2>
  <table id="sources"></table>
  <h2>依模型</h2>
  <table id="models"></table>
  <h2>最近請求</h2>
  <table id="events"></table>
<script>
const B = "/__acm";
const fmt = n => (n||0).toLocaleString();
const pct = r => ((r||0)*100).toFixed(1)+"%";
const usd = n => "$"+(n||0).toFixed(2);
async function j(u){ const r=await fetch(B+u); return r.json(); }
function el(tag, html){ const e=document.createElement(tag); e.innerHTML=html; return e; }
function card(k,v,sub){ return `<div class="card"><div class="k">${k}</div><div class="v">${v}${sub?` <small>${sub}</small>`:""}</div></div>`; }
async function load(){
  const s = await j("/api/summary");
  const o = s.overall;
  document.getElementById("cards").innerHTML =
    card("Requests", fmt(o.requests)) +
    card("Total input", fmt(o.total_input_tokens), "tokens") +
    card("Output", fmt(o.output_tokens), "tokens") +
    card("Cache hit", pct(o.cache_hit_ratio)) +
    card("Cache read", fmt(o.cache_read_tokens), "tokens") +
    card("Illustrative saved", usd(o.illustrative_cache_savings_usd), "ref only");

  const st = document.getElementById("sources");
  st.innerHTML = "<tr><th>Source</th><th>Req</th><th>Input</th><th>Cache read</th><th>Output</th><th>Cache hit</th></tr>";
  s.by_source.forEach(r=>{
    const c = r.source==="codex" ? "var(--codex)" : "var(--claude)";
    st.appendChild(el("tr",`<td><span class="tag" style="background:${c}"></span>${r.source}</td>`+
      `<td>${fmt(r.requests)}</td><td>${fmt(r.input_tokens)}</td><td>${fmt(r.cache_read_tokens)}</td>`+
      `<td>${fmt(r.output_tokens)}</td><td>${pct(r.cache_hit_ratio)}</td>`));
  });

  const models = await j("/api/models");
  const mt = document.getElementById("models");
  mt.innerHTML = "<tr><th>Model</th><th>Source</th><th>Req</th><th>Input</th><th>Cache read</th><th>Cache hit</th></tr>";
  models.slice(0,20).forEach(r=>{
    mt.appendChild(el("tr",`<td>${r.model||"—"}</td><td>${r.source}</td><td>${fmt(r.requests)}</td>`+
      `<td>${fmt(r.input_tokens)}</td><td>${fmt(r.cache_read_tokens)}</td><td>${pct(r.cache_hit_ratio)}</td>`));
  });

  const ev = await j("/api/events?limit=50");
  const et = document.getElementById("events");
  et.innerHTML = "<tr><th>Time (UTC)</th><th>Source</th><th>Model</th><th>Input</th><th>Cache read</th><th>Output</th></tr>";
  ev.forEach(r=>{
    et.appendChild(el("tr",`<td>${(r.ts_utc||"").replace('T',' ').slice(0,19)}</td><td>${r.source}</td>`+
      `<td>${r.model||"—"}</td><td>${fmt(r.input_tokens)}</td><td>${fmt(r.cache_read_tokens)}</td><td>${fmt(r.output_tokens)}</td>`));
  });
}
load().catch(e=>{ document.body.appendChild(el("p", "load error: "+e)); });
</script>
</body></html>"""
