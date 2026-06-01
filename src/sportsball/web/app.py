"""FastAPI app for the sportsball dashboard.

``create_app(provider)`` wires a data provider to two endpoints — ``/`` (the HTML
page) and ``/api/snapshot`` (the JSON the page polls). The ``model`` panel is added
here from the real on-disk artifacts so it's honest regardless of data source.
"""
from __future__ import annotations

import argparse

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..logging_conf import get_logger
from .providers import DataProvider, DemoProvider, get_provider, model_status

log = get_logger("webui")


def create_app(provider: DataProvider | None = None) -> FastAPI:
    provider = provider or DemoProvider()
    app = FastAPI(title="Sportsball Dashboard", docs_url="/api/docs")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_HTML

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "source": provider.source}

    @app.get("/api/snapshot")
    def snapshot() -> dict:
        snap = provider.snapshot()
        snap["model"] = model_status()
        return snap

    return app


def main() -> None:
    ap = argparse.ArgumentParser(description="Sportsball web dashboard")
    ap.add_argument("--mode", choices=["auto", "demo", "duckdb", "postgres"], default="auto",
                    help="data source (auto: postgres -> duckdb -> demo)")
    ap.add_argument("--duckdb", default="data/sportsball.duckdb", help="path for --mode duckdb/auto")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    args = ap.parse_args()

    provider = get_provider(mode=args.mode, duckdb_path=args.duckdb)
    log.info("Dashboard data source: %s | http://%s:%d", provider.source, args.host, args.port)
    import uvicorn
    uvicorn.run(create_app(provider), host=args.host, port=args.port, log_level="warning")


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Sportsball Dashboard</title>
<style>
  :root{--bg:#0c0f17;--panel:#141a26;--panel2:#1b2333;--line:#27314a;--ink:#e8eefc;
        --mut:#8aa0c6;--pos:#33d6a6;--neg:#ff6b6b;--warn:#ffcf5c;--accent:#5b9dff}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.45 ui-sans-serif,system-ui,Segoe UI,Roboto,Arial;background:var(--bg);color:var(--ink)}
  header{display:flex;align-items:center;gap:14px;padding:16px 22px;border-bottom:1px solid var(--line);
         background:linear-gradient(180deg,#121826,#0c0f17)}
  header h1{font-size:17px;margin:0;letter-spacing:.4px}
  header .sub{color:var(--mut);font-size:12px}
  .spacer{flex:1}
  .badge{padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid var(--line)}
  .badge.live{color:var(--pos);border-color:#1d5a48;background:#0f2a22}
  .badge.stale{color:var(--warn);border-color:#5a4a1d;background:#2a2410}
  .badge.absent{color:var(--neg);border-color:#5a1d1d;background:#2a1010}
  .badge.src{color:var(--accent);border-color:#1d3a5a;background:#0f1c2a}
  main{padding:18px 22px;max-width:1200px;margin:0 auto}
  .grid{display:grid;gap:14px}
  .kpis{grid-template-columns:repeat(4,1fr)}
  .cols{grid-template-columns:1.4fr 1fr;margin-top:14px}
  .tables{grid-template-columns:1fr 1fr;margin-top:14px}
  @media(max-width:900px){.kpis{grid-template-columns:repeat(2,1fr)}.cols,.tables{grid-template-columns:1fr}}
  .card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px 16px}
  .card h2{font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);margin:0 0 10px}
  .kpi .v{font-size:26px;font-weight:700;letter-spacing:.3px}
  .kpi .l{color:var(--mut);font-size:12px;margin-top:2px}
  .pos{color:var(--pos)} .neg{color:var(--neg)} .warn{color:var(--warn)} .mut{color:var(--mut)}
  table{width:100%;border-collapse:collapse;font-size:12.5px}
  th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
  th{color:var(--mut);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.4px}
  td.num{text-align:right;font-variant-numeric:tabular-nums}
  .kv{display:grid;grid-template-columns:auto 1fr;gap:6px 14px}
  .kv .k{color:var(--mut)} .kv .val{text-align:right;font-variant-numeric:tabular-nums}
  svg.spark{width:100%;height:140px;display:block}
  .foot{color:var(--mut);font-size:11px;margin-top:16px;text-align:center}
  .pill{font-size:11px;padding:1px 7px;border-radius:999px;border:1px solid var(--line);color:var(--mut)}
  .ctl{background:var(--panel2);color:var(--ink);border:1px solid var(--line);border-radius:8px;
       padding:4px 8px;font-size:12px}
  .eqwrap{position:relative;height:140px}
  canvas#eqchart{position:absolute;inset:0}
</style>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
</head>
<body>
<header>
  <h1>⚾ Sportsball</h1><span class="sub">paper-trading dashboard</span>
  <span class="spacer"></span>
  <span id="src" class="badge src">…</span>
  <span id="mstatus" class="badge">model …</span>
  <select id="refresh" class="ctl" title="auto-refresh interval">
    <option value="2000">2s</option>
    <option value="5000" selected>5s</option>
    <option value="10000">10s</option>
    <option value="30000">30s</option>
    <option value="0">Pause</option>
  </select>
</header>
<main>
  <div class="grid kpis">
    <div class="card kpi"><div id="pnl" class="v">—</div><div class="l">Realized PnL (units)</div></div>
    <div class="card kpi"><div id="roi" class="v">—</div><div class="l">ROI (profit / turnover)</div></div>
    <div class="card kpi"><div id="win" class="v">—</div><div class="l">Win rate</div></div>
    <div class="card kpi"><div id="clv" class="v">—</div><div class="l">Avg CLV · beat-rate</div></div>
  </div>

  <div class="grid cols">
    <div class="card">
      <h2>Equity curve <span id="eqn" class="pill"></span></h2>
      <div class="eqwrap">
        <canvas id="eqchart"></canvas>
        <svg id="spark" class="spark" preserveAspectRatio="none" style="display:none"></svg>
      </div>
    </div>
    <div class="card">
      <h2>Edge diagnostics</h2>
      <div class="kv" id="edge"></div>
    </div>
  </div>

  <div class="grid cols">
    <div class="card">
      <h2>Model status</h2>
      <div class="kv" id="model"></div>
    </div>
    <div class="card">
      <h2>Open book</h2>
      <div class="kv" id="book"></div>
    </div>
  </div>

  <div class="grid tables">
    <div class="card"><h2>Recent signals</h2><table id="sigs"></table></div>
    <div class="card"><h2>Recent trades</h2><table id="trades"></table></div>
  </div>

  <div class="foot" id="foot">loading…</div>
</main>
<script>
const $=id=>document.getElementById(id);
const pct=x=>x==null?"—":(x*100).toFixed(2)+"%";
const sgnPct=x=>x==null?"—":(x>=0?"+":"")+(x*100).toFixed(2)+"%";
const cls=x=>x==null?"mut":(x>0?"pos":x<0?"neg":"");
const num=(x,d=3)=>x==null?"—":(+x).toFixed(d);

function spark(curve){
  const el=$("spark"); el.innerHTML="";
  if(!curve||curve.length<2){el.innerHTML='<text x="8" y="20" fill="#8aa0c6" font-size="12">no settled trades</text>';return;}
  const W=600,H=140,p=6, ys=curve.map(c=>c.bankroll);
  const lo=Math.min(...ys),hi=Math.max(...ys),span=(hi-lo)||1;
  const X=i=>p+i*(W-2*p)/(curve.length-1), Y=v=>H-p-(v-lo)*(H-2*p)/span;
  const base=curve[0].bankroll, up=ys[ys.length-1]>=base;
  el.setAttribute("viewBox",`0 0 ${W} ${H}`);
  const d=curve.map((c,i)=>(i?"L":"M")+X(i).toFixed(1)+" "+Y(c.bankroll).toFixed(1)).join(" ");
  const col=up?"#33d6a6":"#ff6b6b";
  const area=`${d} L ${X(curve.length-1).toFixed(1)} ${H-p} L ${X(0).toFixed(1)} ${H-p} Z`;
  el.innerHTML=`<path d="${area}" fill="${col}22"/><path d="${d}" fill="none" stroke="${col}" stroke-width="2"/>`
    +`<line x1="${p}" y1="${Y(base).toFixed(1)}" x2="${W-p}" y2="${Y(base).toFixed(1)}" stroke="#27314a" stroke-dasharray="4 4"/>`;
}

let chart=null;
function drawEquity(curve){
  const cv=$("eqchart"), sv=$("spark");
  if(!window.Chart){ cv.style.display="none"; sv.style.display="block"; return spark(curve); }
  cv.style.display="block"; sv.style.display="none";
  const ys=curve.map(c=>c.bankroll);
  const up=ys.length>1?ys[ys.length-1]>=curve[0].bankroll:true;
  const col=up?"#33d6a6":"#ff6b6b";
  const data={labels:curve.map((_,i)=>i),
    datasets:[{data:ys,borderColor:col,backgroundColor:col+"22",fill:true,
               tension:.15,pointRadius:0,borderWidth:2}]};
  const opts={animation:false,responsive:true,maintainAspectRatio:false,
    plugins:{legend:{display:false}},
    scales:{x:{display:false},y:{ticks:{color:"#8aa0c6"},grid:{color:"#1b2333"}}}};
  if(chart){ chart.data=data; chart.options=opts; chart.update(); }
  else { chart=new Chart(cv.getContext("2d"),{type:"line",data,options:opts}); }
}

function rows(t,cols,data,fmt){
  t.innerHTML="<tr>"+cols.map(c=>`<th>${c}</th>`).join("")+"</tr>"+
    (data.length?data.map(r=>"<tr>"+fmt(r)+"</tr>").join(""):`<tr><td colspan="${cols.length}" class="mut">none</td></tr>`);
}

async function tick(){
  let s; try{ s=await (await fetch("api/snapshot")).json(); }
  catch(e){ $("foot").textContent="fetch failed: "+e; return; }
  const p=s.performance,e=s.edge,m=s.model,l=s.live;
  $("src").textContent="source: "+s.source;
  $("mstatus").textContent="model: "+m.status; $("mstatus").className="badge "+m.status;

  $("pnl").innerHTML=`<span class="${cls(p.realized_pnl)}">${(p.realized_pnl>=0?"+":"")+num(p.realized_pnl,3)}</span>`;
  $("roi").innerHTML=`<span class="${cls(p.roi)}">${sgnPct(p.roi)}</span>`;
  $("win").textContent=pct(p.win_rate);
  $("clv").innerHTML=`<span class="${cls(e.avg_clv)}">${sgnPct(e.avg_clv)}</span> <span class="mut" style="font-size:14px">· ${pct(e.clv_beat_rate)}</span>`;

  $("eqn").textContent=p.settled+" settled · "+p.open+" open";
  drawEquity(p.equity_curve);

  $("edge").innerHTML=[
    ["Avg CLV", `<span class="${cls(e.avg_clv)}">${sgnPct(e.avg_clv)}</span> (n=${e.n_clv})`],
    ["CLV beat-rate", pct(e.clv_beat_rate)],
    ["Favorite hit-rate", pct(e.favorite_hit_rate)+` <span class="mut">(${e.events_graded} graded)</span>`],
    ["Signals evaluated", e.signals_evaluated],
    ["Signals bet / abstain", `${e.signals_bet} / <span class="warn">${pct(e.abstain_rate)}</span>`],
    ["Turnover", num(p.turnover,3)+" units"],
  ].map(([k,v])=>`<div class="k">${k}</div><div class="val">${v}</div>`).join("");

  $("model").innerHTML=[
    ["Status", `<span class="${m.status==='live'?'pos':m.status==='stale'?'warn':'neg'}">${m.status}</span>`],
    ["Schema", `v${m.schema_version??"—"} <span class="mut">(code v${m.code_schema_version})</span>`],
    ["Features", `${m.n_features??"—"} / ${m.code_n_features}`],
    ["Calibration T", num(m.temperature,3)],
    ["HFA · K", `${num(m.hfa,1)} · ${num(m.k_factor,2)}`],
    ["Last retrain", m.last_retrain?m.last_retrain.replace("T"," ").slice(0,16):"—"],
    ["Note", `<span class="mut">${m.reason||"matches code; Engine serves P_true"}</span>`],
  ].map(([k,v])=>`<div class="k">${k}</div><div class="val">${v}</div>`).join("");

  $("book").innerHTML=[
    ["Open exposure", num(l.open_exposure,4)+" units"],
    ["Open positions", l.open_positions.length],
    ["Arb opps", l.arb_count],
    ["Total trades", p.total_trades],
  ].map(([k,v])=>`<div class="k">${k}</div><div class="val">${v}</div>`).join("");

  rows($("sigs"),["Match","Side","Src","Odds","EV","Bet"],l.recent_signals,r=>
    `<td>${r.event}</td><td>${r.side}</td><td>${r.source}</td><td class="num">${num(r.odds)}</td>`+
    `<td class="num ${cls(r.ev)}">${sgnPct(r.ev)}</td><td>${r.bet?"✓":"<span class='mut'>—</span>"}</td>`);
  rows($("trades"),["Match","Side","Odds","Stake","Status","PnL"],l.recent_trades,r=>
    `<td>${r.event}</td><td>${r.side}</td><td class="num">${num(r.odds)}</td><td class="num">${num(r.stake,4)}</td>`+
    `<td>${r.status}</td><td class="num ${cls(r.pnl)}">${r.pnl?((r.pnl>=0?"+":"")+num(r.pnl,4)):"—"}</td>`);

  const rv=+$("refresh").value;
  $("foot").textContent="source: "+s.source+" · updated "+new Date(s.generated_at).toLocaleTimeString()+
    (rv>0?(" · auto-refresh "+(rv/1000)+"s"):" · paused");
}

let timer=null;
function schedule(){
  if(timer) clearInterval(timer);
  const ms=+$("refresh").value;
  timer = ms>0 ? setInterval(tick,ms) : null;
}
$("refresh").addEventListener("change",()=>{ schedule(); if(+$("refresh").value>0) tick(); });
tick(); schedule();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
