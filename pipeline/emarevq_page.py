#!/usr/bin/env python3
"""Assemble an EMArevQ eye-test page: blind charts + 3-state marking UI, marks persisted to
the artifact `db`. build_page(charts_dir, doc_id, title, out_path) writes body-content HTML
for the Artifact tool (no doctype/head/body; the tool wraps it)."""
import base64, glob
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "study" / "emarevq"


def build_page(charts_dir, doc_id, title, out_path, lead_extra=""):
    imgs = sorted(glob.glob(str(Path(charts_dir) / "*.png")))
    uris = []
    for p in imgs:
        b = base64.b64encode(open(p, "rb").read()).decode()
        uris.append(f"data:image/png;base64,{b}")
    arr = ",\n".join(f'"{u}"' for u in uris)
    html = (PAGE.replace("__TITLE__", title).replace("__DOCID__", doc_id)
                .replace("__ARR__", arr).replace("__LEADEXTRA__", lead_extra))
    Path(out_path).write_text(html)
    print(f"wrote {out_path} ({len(html)//1024} KB, {len(uris)} charts, db doc {doc_id})")
    return out_path


PAGE = r"""<title>__TITLE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@500;600&display=swap">
<style>
  :root { --bg:#f6f7f9; --card:#fff; --ink:#1a1d21; --mut:#6b7280; --line:#e5e7eb;
          --yes:#1a7f37; --no:#c0392b; --skip:#8a8f98; --accent:#2563eb;
          --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme=light]) {
    --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --mut:#9aa4b2; --line:#30363d;
    --yes:#3fb950; --no:#f85149; --skip:#8b949e; --accent:#58a6ff; } }
  :root[data-theme=dark] { --bg:#0d1117; --card:#161b22; --ink:#e6edf3; --mut:#9aa4b2;
    --line:#30363d; --yes:#3fb950; --no:#f85149; --skip:#8b949e; --accent:#58a6ff; }
  * { box-sizing:border-box; }
  body { background:var(--bg); color:var(--ink); font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  .wrap { max-width:960px; margin:0 auto; padding:18px 16px 120px; }
  h1 { font-size:20px; margin:.2em 0; }
  .lead { color:var(--mut); font-size:14px; margin:0 0 14px; }
  .lead b { color:var(--ink); }
  .bar { height:8px; background:var(--line); border-radius:6px; overflow:hidden; margin:10px 0 4px; }
  .bar > i { display:block; height:100%; background:var(--accent); width:0; transition:width .2s; }
  .meta { display:flex; justify-content:space-between; color:var(--mut); font-size:13px;
          font-family:var(--mono); font-variant-numeric:tabular-nums; letter-spacing:.01em; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px; margin-top:12px; }
  .imgwrap { position:relative; }
  img.chart { width:100%; border-radius:8px; display:block; background:#0d1117; }
  .tag { position:absolute; top:8px; left:10px; background:rgba(0,0,0,.55); color:#fff;
         font-size:12px; padding:2px 8px; border-radius:20px; letter-spacing:.02em; font-family:var(--mono); }
  .btns { display:grid; grid-template-columns:1fr 1fr 1fr; gap:8px; margin-top:12px; }
  button.mark { border:1px solid var(--line); background:var(--card); color:var(--ink);
    padding:14px 8px; border-radius:10px; font-size:15px; font-weight:600; cursor:pointer; }
  button.mark:hover { border-color:var(--accent); }
  button.mark.sel-yes { background:var(--yes); color:#fff; border-color:var(--yes); }
  button.mark.sel-no { background:var(--no); color:#fff; border-color:var(--no); }
  button.mark.sel-skip { background:var(--skip); color:#fff; border-color:var(--skip); }
  .nav { display:flex; justify-content:space-between; align-items:center; margin-top:12px; gap:8px; }
  .nav button { background:none; border:1px solid var(--line); color:var(--ink); border-radius:8px;
    padding:8px 14px; cursor:pointer; font-size:14px; }
  .nav button:disabled { opacity:.4; cursor:default; }
  .k { color:var(--mut); font-size:12px; font-family:var(--mono); }
  .done { text-align:center; padding:24px; }
  .done h2 { color:var(--yes); }
  .grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(64px,1fr)); gap:6px; margin-top:14px; }
  .cell { aspect-ratio:1; border-radius:6px; border:1px solid var(--line); display:flex;
    align-items:center; justify-content:center; font-size:12px; cursor:pointer; background:var(--card); font-family:var(--mono); }
  .cell.yes { background:var(--yes); color:#fff; border-color:var(--yes); }
  .cell.no { background:var(--no); color:#fff; border-color:var(--no); }
  .cell.skip { background:var(--skip); color:#fff; border-color:var(--skip); }
  .cell.cur { outline:2px solid var(--accent); outline-offset:1px; }
  .save { font-size:12px; color:var(--mut); text-align:center; margin-top:8px; min-height:16px; }
  button.mark:focus-visible, .cell:focus-visible, .nav button:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
  @media (prefers-reduced-motion:reduce) { .bar > i { transition:none; } }
</style>

<div class="wrap">
  <h1>__TITLE__</h1>
  <p class="lead">For each chart: is the move leading into the <b>blue triangle</b> (the trigger bar)
  a <b>slow, grinding drift</b> away from the amber moving-average line &mdash; your "true slow drift"?
  Or is it a fast / spiky / one-bar shove? Tap <b>Slow drift</b>, <b>Not slow</b>, or <b>Can't tell</b>.
  Keys: <span class="k">1</span>/<span class="k">2</span>/<span class="k">3</span> and
  <span class="k">&larr; &rarr;</span>. Your answers save automatically.__LEADEXTRA__</p>

  <div class="bar"><i id="prog"></i></div>
  <div class="meta"><span id="count"></span><span id="idx"></span></div>

  <div id="view"></div>
  <div class="grid" id="grid"></div>
  <div class="save" id="save"></div>
</div>

<script>
const CHARTS = [
__ARR__
];
const N = CHARTS.length;
let marks = {};
let cur = 0;
let db = null, doc = null;

async function connect() {
  try {
    db = await claude.use("db");
    if (db) {
      doc = db.doc("__DOCID__");
      const snap = await doc.get();
      if (snap && snap.marks) marks = snap.marks;
      doc.onSnapshot(s => { if (s && s.marks) { marks = s.marks; render(); } });
      render();
    }
  } catch (e) {}
  if (!db) {
    try { marks = JSON.parse(localStorage.getItem("__DOCID__")||"{}"); } catch(e) {}
    render();
  }
}
function persist() {
  if (doc) { doc.set({ marks: marks, updated: Date.now() }).then(
      ()=>flash("saved"), ()=>flash("save failed — your marks are still on-screen")); }
  else { try { localStorage.setItem("__DOCID__", JSON.stringify(marks)); } catch(e){}
         flash("saved locally — tell me when done and I'll read the on-page results"); }
}
let ft; function flash(t){ const s=document.getElementById("save"); s.textContent=t;
  clearTimeout(ft); ft=setTimeout(()=>s.textContent="",2500); }

function set(val) {
  marks[cur+1] = val; persist();
  let nxt = -1;
  for (let k=1;k<=N;k++) { const j=(cur+k)%N; if(!marks[j+1]) { nxt=j; break; } }
  cur = nxt>=0 ? nxt : Math.min(cur+1, N-1);
  render();
}
function go(i) { cur = Math.max(0, Math.min(N-1, i)); render(); }

function render() {
  const done = Object.keys(marks).length;
  document.getElementById("prog").style.width = (100*done/N)+"%";
  document.getElementById("count").textContent = done+" / "+N+" marked";
  document.getElementById("idx").textContent = "chart "+(cur+1)+" of "+N;
  const m = marks[cur+1];
  const view = document.getElementById("view");
  if (done===N) {
    view.innerHTML = '<div class="card done"><h2>All '+N+' marked ✓</h2>'+
      '<p class="lead">Thanks. Tell me you\'re done and I\'ll read your answers. '+
      'You can still change any square below.</p></div>';
  } else {
    view.innerHTML =
      '<div class="card"><div class="imgwrap"><span class="tag">#'+(cur+1)+'</span>'+
      '<img class="chart" src="'+CHARTS[cur]+'" alt="chart '+(cur+1)+'"></div>'+
      '<div class="btns">'+
        '<button class="mark '+(m==="yes"?"sel-yes":"")+'" onclick="set(\'yes\')">Slow drift</button>'+
        '<button class="mark '+(m==="no"?"sel-no":"")+'" onclick="set(\'no\')">Not slow</button>'+
        '<button class="mark '+(m==="skip"?"sel-skip":"")+'" onclick="set(\'skip\')">Can\'t tell</button>'+
      '</div>'+
      '<div class="nav"><button onclick="go(cur-1)" '+(cur===0?"disabled":"")+'>&larr; Prev</button>'+
        '<span class="k">'+(m?("marked: "+({yes:"slow drift",no:"not slow",skip:"can\'t tell"}[m])):"unmarked")+'</span>'+
        '<button onclick="go(cur+1)" '+(cur===N-1?"disabled":"")+'>Next &rarr;</button></div></div>';
  }
  const g = document.getElementById("grid"); g.innerHTML="";
  for (let i=1;i<=N;i++) {
    const c = document.createElement("div");
    const m2 = marks[i];
    c.className = "cell"+(m2?" "+m2:"")+(i-1===cur?" cur":"");
    c.textContent = i; c.onclick = ()=>go(i-1);
    g.appendChild(c);
  }
}
document.addEventListener("keydown", e => {
  if (e.key==="1"||e.key.toLowerCase()==="y") set("yes");
  else if (e.key==="2"||e.key.toLowerCase()==="n") set("no");
  else if (e.key==="3"||e.key.toLowerCase()==="c") set("skip");
  else if (e.key==="ArrowLeft") go(cur-1);
  else if (e.key==="ArrowRight") go(cur+1);
});
render(); connect();
</script>
"""

if __name__ == "__main__":
    build_page(OUT / "charts", "emarevq/marks", "EMArevQ — the eye test",
               OUT / "calib_page.html")
