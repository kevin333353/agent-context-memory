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
        inp = r.get("input_tokens", 0)
        cc = r.get("cache_creation_tokens", 0)
        cr = r.get("cache_read_tokens", 0)
        total_in = inp + cc + cr
        r["total_input_tokens"] = total_in
        r["cache_hit_ratio"] = (cr / total_in) if total_in else 0.0
        r["cache_savings_pct"] = pricing.cache_savings_pct(inp, cc, cr)
        if r.get("source") == "claude":
            r["illustrative_cache_savings_usd"] = round(
                pricing.cache_savings_usd(None, cr), 4
            )
    return rows


def api_summary(store: UsageStore) -> dict:
    overall = store.summary()
    overall["illustrative_cache_savings_usd"] = round(
        pricing.cache_savings_usd(None, overall.get("cache_read_tokens", 0)), 4
    )
    overall["cache_savings_pct"] = pricing.cache_savings_pct(
        overall.get("input_tokens", 0),
        overall.get("cache_creation_tokens", 0),
        overall.get("cache_read_tokens", 0),
    )
    return {
        "overall": overall,
        "by_source": _augment_source_rows(store.by_source()),
        "savings": store.latest_savings(),
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
  :root{
    --bg:#e7ebf0; --panel:#fdfefe; --ink:#16202b; --muted:#616f7d; --line:#d2d9e1;
    --save:#159a70; --save2:#2fc79a; --track:#dfe4ea;
    --cache:#4f74c2; --cache2:#6d93e0;
    --claude:#cf8636; --codex:#3a7fd0;
    --mono:ui-monospace,"Cascadia Code","JetBrains Mono",Consolas,Menlo,monospace;
    --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  }
  @media (prefers-color-scheme:dark){
    :root{ --bg:#0e131a; --panel:#161d26; --ink:#e6ecf2; --muted:#8a97a6; --line:#232c38;
           --save:#1fb083; --save2:#3ad3a4; --track:#1d2530;
           --cache:#5b82d6; --cache2:#7ba0ef; }
  }
  :root[data-theme="dark"]{ --bg:#0e131a; --panel:#161d26; --ink:#e6ecf2; --muted:#8a97a6;
           --line:#232c38; --save:#1fb083; --save2:#3ad3a4; --track:#1d2530; }
  :root[data-theme="light"]{ --bg:#e7ebf0; --panel:#fdfefe; --ink:#16202b; --muted:#616f7d;
           --line:#d2d9e1; --save:#159a70; --save2:#2fc79a; --track:#dfe4ea; }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
       padding:28px 22px 48px;-webkit-font-smoothing:antialiased}
  .wrap{max-width:1040px;margin:0 auto}
  .mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
  header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;
         margin-bottom:26px;flex-wrap:wrap}
  .brand{font-family:var(--mono);font-size:12px;letter-spacing:.14em;color:var(--muted);
         text-transform:uppercase;display:flex;align-items:center;gap:9px}
  .dot{width:8px;height:8px;border-radius:50%;background:var(--save);
       box-shadow:0 0 0 0 var(--save);animation:pulse 2.6s infinite}
  @keyframes pulse{0%{box-shadow:0 0 0 0 rgba(31,176,131,.5)}70%{box-shadow:0 0 0 7px rgba(31,176,131,0)}100%{box-shadow:0 0 0 0 rgba(31,176,131,0)}}
  .updated{font-family:var(--mono);font-size:11px;color:var(--muted)}
  /* hero savings meters */
  .heroes{display:grid;gap:14px;grid-template-columns:repeat(auto-fit,minmax(270px,1fr));margin-bottom:18px}
  .hero{background:var(--panel);border:1px solid var(--line);border-radius:16px;
        padding:22px 24px 20px}
  .hero.primary{border-color:color-mix(in srgb,var(--save) 40%,var(--line))}
  .eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.1em;text-transform:uppercase;
           color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:8px}
  .eyebrow .tag{font-size:9.5px;padding:2px 6px;border-radius:5px;letter-spacing:.06em;
           background:var(--track);color:var(--ink)}
  .eyebrow .tag.you{background:color-mix(in srgb,var(--save) 22%,transparent);
           color:var(--save)}
  .meter-fill.alt{background:linear-gradient(90deg,var(--cache),var(--cache2))}
  .meter{position:relative;height:74px;border-radius:11px;background:var(--track);
         overflow:hidden;border:1px solid var(--line)}
  .meter-fill{position:absolute;inset:0 auto 0 0;width:0;
              background:linear-gradient(90deg,var(--save),var(--save2));
              transition:width 1.1s cubic-bezier(.2,.7,.2,1)}
  .meter-read{position:absolute;inset:0;display:flex;align-items:center;
              padding:0 22px;gap:8px;font-family:var(--mono)}
  .meter-read b{font-size:44px;font-weight:600;line-height:1;letter-spacing:-.02em;color:#fff;
                text-shadow:0 1px 2px rgba(0,0,0,.25)}
  .meter-read .pct{font-size:20px;color:#fff;opacity:.85;align-self:flex-start;margin-top:6px}
  .meter-read .cap{margin-left:auto;text-align:right;color:#fff;opacity:.9;font-size:12px;
                   line-height:1.4;text-shadow:0 1px 2px rgba(0,0,0,.3)}
  .legend{display:flex;gap:20px;flex-wrap:wrap;margin-top:14px;font-family:var(--mono);
          font-size:12px;color:var(--muted)}
  .legend b{color:var(--ink);font-weight:600}
  /* tiles */
  .tiles{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:26px}
  .tile{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:15px 16px}
  .tile .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
  .tile .v{font-family:var(--mono);font-size:26px;font-weight:600;margin-top:7px;letter-spacing:-.01em}
  .tile .v small{font-size:12px;color:var(--muted);font-weight:400}
  h2{font-family:var(--mono);font-size:11px;letter-spacing:.14em;text-transform:uppercase;
     color:var(--muted);margin:0 0 12px;font-weight:600}
  section.blk{margin-bottom:26px}
  /* source cards */
  .srcgrid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
  .src{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 17px;
       border-left:3px solid var(--c)}
  .src .name{font-family:var(--mono);font-size:13px;font-weight:600;text-transform:uppercase;
             letter-spacing:.06em;color:var(--c);display:flex;justify-content:space-between}
  .src .big{font-family:var(--mono);font-size:30px;font-weight:600;margin:8px 0 2px}
  .src .sub{font-family:var(--mono);font-size:11.5px;color:var(--muted)}
  .mini{height:6px;border-radius:3px;background:var(--track);overflow:hidden;margin:12px 0 4px}
  .mini>span{display:block;height:100%;background:var(--c);width:0;transition:width 1s ease}
  /* tables */
  .tablewrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--panel)}
  table{width:100%;border-collapse:collapse;font-family:var(--mono);font-size:12.5px}
  th,td{text-align:right;padding:9px 13px;border-bottom:1px solid var(--line);white-space:nowrap}
  th:first-child,td:first-child{text-align:left}
  th{color:var(--muted);font-weight:600;font-size:10.5px;letter-spacing:.08em;text-transform:uppercase}
  tr:last-child td{border-bottom:none}
  .pill{display:inline-block;padding:1px 7px;border-radius:5px;font-size:11px}
  footer{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:30px;line-height:1.6}
  @media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
</style></head>
<body><div class="wrap">
  <header>
    <div class="brand"><span class="dot"></span>Agent Context Memory · Live Telemetry</div>
    <div class="updated" id="updated"></div>
  </header>

  <div class="heroes">
    <div class="hero primary">
      <div class="eyebrow"><span class="tag you">context-memory</span>工具省下 · 估算</div>
      <div class="meter">
        <div class="meter-fill" id="simFill"></div>
        <div class="meter-read">
          <b id="simPct">–</b><span class="pct">%</span>
          <span class="cap" id="simCap"></span>
        </div>
      </div>
      <div class="legend">
        <span>完整 transcript <b id="simBase">–</b></span>
        <span>精簡 state <b id="simMem">–</b></span>
      </div>
    </div>
    <div class="hero primary">
      <div class="eyebrow"><span class="tag you">context-memory</span>工具省下 · 實測 A/B</div>
      <div class="meter">
        <div class="meter-fill" id="abFill"></div>
        <div class="meter-read">
          <b id="abPct">–</b><span class="pct">%</span>
          <span class="cap" id="abCap"></span>
        </div>
      </div>
      <div class="legend">
        <span id="abMeta">開/關工具同任務實測</span>
      </div>
    </div>
    <div class="hero">
      <div class="eyebrow"><span class="tag">Anthropic</span>prompt cache 效率（非本工具造成）</div>
      <div class="meter">
        <div class="meter-fill alt" id="fill"></div>
        <div class="meter-read">
          <b id="savePct">–</b><span class="pct">%</span>
          <span class="cap" id="heroCap"></span>
        </div>
      </div>
      <div class="legend">
        <span>served from cache <b id="crTok">–</b></span>
        <span>cache hit <b id="hitPct">–</b></span>
        <span>baseline input <b id="baseTok">–</b></span>
      </div>
    </div>
  </div>

  <div class="tiles" id="tiles"></div>

  <section class="blk"><h2>Sources · Claude vs Codex</h2><div class="srcgrid" id="srcgrid"></div></section>
  <section class="blk"><h2>By model</h2><div class="tablewrap"><table id="models"></table></div></section>
  <section class="blk"><h2>Recent requests</h2><div class="tablewrap"><table id="events"></table></div></section>

  <footer>
    <b>工具省下（估算）</b>＝context-memory 每回合注入精簡 state vs 塞完整 transcript 的 token 差（<code>simulate-token-savings</code>，離線上限估算，非帳單）。<br>
    <b>工具省下（實測 A/B）</b>＝同一任務開/關工具、真呼叫模型量到的 input token 差（<code>provider-ab-benchmark</code>，ground truth）。<br>
    <b>快取效率</b>＝Anthropic prompt cache 自動避免的 input 成本占比，<b>與本工具無關</b>（不裝也會有），僅作對照。<br>
    proxy 僅本機 loopback 自用；金額類數字皆為定價換算之參考值，非帳單。
  </footer>
</div>
<script>
const B="/__acm";
const cfmt=n=>{n=n||0;const a=Math.abs(n);
  if(a>=1e9)return (n/1e9).toFixed(2)+"B";
  if(a>=1e6)return (n/1e6).toFixed(1)+"M";
  if(a>=1e3)return (n/1e3).toFixed(1)+"k";
  return String(n);};
const pct=r=>((r||0)*100).toFixed(1);
const SRC={claude:"var(--claude)",codex:"var(--codex)"};
const j=async u=>(await fetch(B+u)).json();
const el=(t,h)=>{const e=document.createElement(t);e.innerHTML=h;return e;};

async function load(){
  const s=await j("/api/summary"), o=s.overall;

  // Tool savings — attributable to context-memory (compact state vs full transcript).
  const sv=s.savings||{simulate:null,ab:null};
  const setMeter=(pref,row)=>{
    const has=row && row.saved_percent!=null;
    const p=has?Number(row.saved_percent):0;
    document.getElementById(pref+"Pct").textContent=has?p.toFixed(1):"–";
    requestAnimationFrame(()=>{document.getElementById(pref+"Fill").style.width=
      (has?Math.max(2,Math.min(100,p)):0).toFixed(1)+"%";});
    return has;
  };
  const simHas=setMeter("sim",sv.simulate);
  document.getElementById("simCap").innerHTML=simHas
    ?"state vs 完整 transcript<br>離線估算 · 上限值"
    :"尚未量測<br>跑 simulator 產生";
  document.getElementById("simBase").textContent=simHas?cfmt(sv.simulate.baseline_tokens):"–";
  document.getElementById("simMem").textContent=simHas?cfmt(sv.simulate.memory_tokens):"–";
  const abHas=setMeter("ab",sv.ab);
  document.getElementById("abCap").innerHTML=abHas
    ?"開/關工具同任務<br>真呼叫模型實測"
    :"尚未量測<br>跑 provider A/B 產生";
  document.getElementById("abMeta").innerHTML=abHas
    ?`${sv.ab.provider||"?"} · ${sv.ab.task||"?"} · 品質${sv.ab.quality_pass?"通過":"未過"} · ${cfmt(sv.ab.baseline_tokens)}→${cfmt(sv.ab.memory_tokens)}`
    :"開/關工具同任務實測";

  const sp=(o.cache_savings_pct||0)*100;
  document.getElementById("savePct").textContent=sp.toFixed(1);
  requestAnimationFrame(()=>{document.getElementById("fill").style.width=Math.max(2,sp).toFixed(1)+"%";});
  document.getElementById("heroCap").innerHTML=
    "每 100 個 input token 的成本<br>快取省下 ~"+sp.toFixed(0)+" 個";
  document.getElementById("crTok").textContent=cfmt(o.cache_read_tokens);
  document.getElementById("hitPct").textContent=pct(o.cache_hit_ratio)+"%";
  document.getElementById("baseTok").textContent=cfmt(o.total_input_tokens);

  document.getElementById("tiles").innerHTML=[
    ["Requests",cfmt(o.requests),""],
    ["Input tokens",cfmt(o.total_input_tokens),"incl. cache"],
    ["Output tokens",cfmt(o.output_tokens),""],
    ["Illustrative saved","$"+((o.illustrative_cache_savings_usd||0)).toLocaleString(undefined,{maximumFractionDigits:0}),"ref, not a bill"],
  ].map(([k,v,x])=>`<div class="tile"><div class="k">${k}</div><div class="v">${v}${x?` <small>${x}</small>`:""}</div></div>`).join("");

  const sg=document.getElementById("srcgrid"); sg.innerHTML="";
  (s.by_source||[]).forEach(r=>{
    const c=SRC[r.source]||"var(--muted)", sv=((r.cache_savings_pct||0)*100);
    const card=el("div",
      `<div class="name"><span>${r.source}</span><span>${cfmt(r.requests)} req</span></div>
       <div class="big">${sv.toFixed(1)}<span style="font-size:15px">%</span></div>
       <div class="sub">cache cost saved</div>
       <div class="mini"><span></span></div>
       <div class="sub">cache hit ${pct(r.cache_hit_ratio)}% · input ${cfmt(r.total_input_tokens)}</div>`);
    card.className="src"; card.style.setProperty("--c",c);
    sg.appendChild(card);
    requestAnimationFrame(()=>{card.querySelector(".mini>span").style.width=Math.max(2,sv).toFixed(1)+"%";});
  });

  const models=await j("/api/models"), mt=document.getElementById("models");
  mt.innerHTML="<tr><th>Model</th><th>Src</th><th>Req</th><th>Input</th><th>Cache read</th><th>Saved</th></tr>";
  models.slice(0,20).forEach(r=>{
    const c=SRC[r.source]||"var(--muted)";
    mt.appendChild(el("tr",
      `<td>${r.model||"—"}</td>
       <td><span class="pill" style="color:${c}">${r.source}</span></td>
       <td>${cfmt(r.requests)}</td><td>${cfmt(r.input_tokens)}</td>
       <td>${cfmt(r.cache_read_tokens)}</td><td>${((r.cache_savings_pct||0)*100).toFixed(1)}%</td>`));
  });

  const ev=await j("/api/events?limit=50"), et=document.getElementById("events");
  et.innerHTML="<tr><th>Time (UTC)</th><th>Src</th><th>Model</th><th>Input</th><th>Cache read</th><th>Output</th></tr>";
  ev.forEach(r=>{
    const c=SRC[r.source]||"var(--muted)";
    et.appendChild(el("tr",
      `<td>${(r.ts_utc||"").replace("T"," ").slice(0,19)}</td>
       <td><span class="pill" style="color:${c}">${r.source}</span></td>
       <td>${r.model||"—"}</td><td>${cfmt(r.input_tokens)}</td>
       <td>${cfmt(r.cache_read_tokens)}</td><td>${cfmt(r.output_tokens)}</td>`));
  });

  const d=new Date();
  document.getElementById("updated").textContent="updated "+d.toTimeString().slice(0,8);
}
load().catch(e=>{document.getElementById("heroCap").textContent="load error: "+e;});
setInterval(()=>load().catch(()=>{}), 15000);
</script>
</body></html>"""
