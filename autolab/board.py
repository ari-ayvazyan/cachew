"""board.html: a self-contained progress board, rendered from the study artifacts.

Open it straight from disk (file://). It embeds the artifacts as JSON and
renders: status + next command, credence over rounds, the hypothesis ledger,
and one expandable card per round (selection, plan, predictions, run, skeptic,
judge), plus findings and literature.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autolab.study import STATUSES, Study, read_json, round_name


def _text(p: Path, limit: int = 40_000) -> str | None:
    if not p.exists():
        return None
    t = p.read_text(encoding="utf-8", errors="replace")
    return t if len(t) <= limit else t[:limit] + "\n…[truncated]"


def _data(study: Study) -> dict[str, Any]:
    m = study.meta
    rounds = []
    for n in study.rounds():
        r = study.rdir(n)
        res = read_json(r / "output" / "results.json") if (r / "output" / "results.json").exists() else None
        try:
            summary = res.get("summary") if isinstance(res, dict) else None
        except Exception:
            summary = None
        rounds.append({
            "id": round_name(n),
            "candidates": read_json(r / "candidates.json"), "selection": read_json(r / "selection.json"),
            "plan": _text(r / "plan.md"), "predictions": read_json(r / "predictions.json"),
            "approval": read_json(r / "approval.json"), "run": read_json(r / "run.json"),
            "summary": summary, "review": read_json(r / "review.json"), "review_md": _text(r / "review.md"),
            "verdict": read_json(r / "verdict.json"),
            "code": sorted(p.relative_to(r).as_posix() for p in (r / "experiment").rglob("*.py")) if (r / "experiment").exists() else [],
            "revisions": len(list((r / "revisions").glob("v*"))) if (r / "revisions").exists() else 0,
        })
    return {
        "question": m["question"], "context": m.get("context", ""), "status": m["status"],
        "next": STATUSES.get(m["status"], ""), "round": m["round"], "config": m["config"],
        "hypotheses": study.hypotheses(), "rounds": rounds, "root": str(study.root),
        "findings": _text(study.root / "findings.md"), "literature": _text(study.root / "literature.md"),
        "problems": (m.get("plan_problems") or []) + (m.get("analysis_problems") or []),
    }


def render(study: Study) -> Path:
    payload = json.dumps(_data(study), ensure_ascii=False).replace("</", "<\\/")
    out = study.root / "board.html"
    out.write_text(TEMPLATE.replace("__DATA__", payload), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Autolab Study Board</title>
<style>
:root{--bg:#f7f6f3;--panel:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2dc;--accent:#2f5d8a;
--ok:#2e7d4f;--warn:#a36a00;--bad:#b3392f;--code:#f1efea;
--s1:#2f5d8a;--s2:#c0622b;--s3:#3f8f5a;--s4:#8a4f9e;--s5:#b8902a;--s6:#4b8f99;--s7:#9a4057;--s0:#8d8b85}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--muted:#9c9a93;
--line:#33322f;--accent:#7fa9d6;--ok:#6cc08b;--warn:#e0a83a;--bad:#e5786d;--code:#262624;
--s1:#7fa9d6;--s2:#e08a55;--s3:#6cc08b;--s4:#b98bd0;--s5:#dcb752;--s6:#76bcc6;--s7:#d97a92;--s0:#8d8b85}}
:root[data-theme="dark"]{--bg:#141413;--panel:#1d1d1b;--ink:#ecebe6;--muted:#9c9a93;--line:#33322f;--accent:#7fa9d6;
--ok:#6cc08b;--warn:#e0a83a;--bad:#e5786d;--code:#262624;--s1:#7fa9d6;--s2:#e08a55;--s3:#6cc08b;--s4:#b98bd0;--s5:#dcb752;--s6:#76bcc6;--s7:#d97a92;--s0:#8d8b85}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:22px;line-height:1.3;margin:0 0 6px}h2{font-size:16px;margin:28px 0 10px}h3{font-size:15px;margin:0}
.muted{color:var(--muted)}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
code,pre{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--code);border-radius:6px}
code{padding:1px 5px}pre{padding:10px 12px;overflow:auto;max-height:420px;white-space:pre-wrap;word-break:break-word;margin:6px 0}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:14px}
.kpi .v{font-size:20px;font-weight:650}.kpi .l{font-size:12px;color:var(--muted)}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}
.sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}
details.round{margin:10px 0}details.round>summary{cursor:pointer;list-style:none;display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
details.round>summary::-webkit-details-marker{display:none}details.round[open]>summary{margin-bottom:10px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media (max-width:720px){.grid2{grid-template-columns:1fr}}
.sec{margin-top:12px}.sec>b{font-size:12px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted)}
svg text{fill:var(--muted);font-size:11px}.tabs button{background:none;border:1px solid var(--line);color:var(--ink);border-radius:6px;padding:4px 10px;margin-right:6px;cursor:pointer}
.tabs button[aria-pressed=true]{border-color:var(--accent);color:var(--accent)}
</style></head><body><main id="app"></main>
<script id="data" type="application/json">__DATA__</script>
<script>
const D=JSON.parse(document.getElementById('data').textContent);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const col=(i,h)=>h&&h.catch_all?'var(--s0)':`var(--s${(i%7)+1})`;
const statusCls={concluded:'ok',awaiting_approval:'warn',plan_failed:'bad',analysis_failed:'bad'};
const verdCls={pass:'ok',pass_with_caveats:'warn',fail:'bad'};
const H=D.hypotheses, R=D.rounds.map(r=>r.id);
function chart(){
  if(!R.length||!H.some(h=>Object.keys(h.credence).length))return '<p class="muted">Credences appear after the first round is judged.</p>';
  const w=640,h=220,p={l:36,r:12,t:10,b:26},X=i=>p.l+(R.length<2?(w-p.l-p.r)/2:i*(w-p.l-p.r)/(R.length-1)),Y=v=>p.t+(1-v)*(h-p.t-p.b);
  let s=`<svg viewBox="0 0 ${w} ${h}" width="100%" role="img" aria-label="credence by round">`;
  for(const v of [0,.5,1])s+=`<line x1="${p.l}" x2="${w-p.r}" y1="${Y(v)}" y2="${Y(v)}" stroke="var(--line)"/><text x="4" y="${Y(v)+4}">${v}</text>`;
  R.forEach((r,i)=>s+=`<text x="${X(i)}" y="${h-6}" text-anchor="middle">${r}</text>`);
  H.forEach((hy,k)=>{const pts=R.map((r,i)=>hy.credence[r]!=null?[X(i),Y(hy.credence[r])]:null).filter(Boolean);
    if(!pts.length)return;const c=col(k,hy);
    s+=`<polyline fill="none" stroke="${c}" stroke-width="2" ${hy.status==='refuted'?'stroke-dasharray="4 3"':''} points="${pts.map(q=>q.join(',')).join(' ')}"><title>${esc(hy.id)}</title></polyline>`;
    pts.forEach(q=>s+=`<circle cx="${q[0]}" cy="${q[1]}" r="3" fill="${c}"><title>${esc(hy.id)}</title></circle>`);});
  return s+'</svg>';
}
function kv(o){if(!o)return '<span class="muted">—</span>';return `<pre>${esc(JSON.stringify(o,null,2))}</pre>`}
function roundCard(r,i){
  const v=r.verdict,rv=r.review,run=r.run,sel=r.selection||{};
  const exp=(r.candidates?.experiments||[]).find(e=>e.id===sel.chosen)||{};
  const badge=v?`<span class="pill">${esc(v.decision)}</span>`:run?'<span class="pill warn">analysis pending</span>':r.approval?'<span class="pill warn">approved</span>':r.plan?'<span class="pill warn">awaiting approval</span>':'<span class="pill muted">planning</span>';
  let s=`<details class="round panel" ${i===D.rounds.length-1?'open':''}><summary><h3>${r.id}</h3><span>${esc(exp.description||sel.chosen||'')}</span>${badge}${rv?` <span class="pill ${verdCls[rv.verdict]||''}">skeptic: ${esc(rv.verdict)}</span>`:''}</summary>`;
  if(v)s+=`<div class="sec"><b>Judge</b><p>${esc(v.summary)}</p>${v.surprise?`<p><b>Surprise:</b> ${esc(v.surprise)}</p>`:''}<p class="muted">Next: ${esc(v.next_direction)}</p>`+
    (v.prediction_scorecard?`<table><tr><th>hyp</th><th>predicted</th><th>observed</th><th>held</th></tr>${v.prediction_scorecard.map(x=>`<tr><td>${esc(x.hypothesis)}</td><td>${esc(x.predicted)}</td><td>${esc(x.observed)}</td><td class="${x.held?'ok':'bad'}">${x.held?'yes':'no'}</td></tr>`).join('')}</table>`:'')+`</div>`;
  s+=`<div class="grid2"><div class="sec"><b>Why this experiment</b><p>${esc(sel.why||'—')}</p>${(sel.rejected||[]).map(x=>`<p class="muted">Rejected ${esc(x.id)}: ${esc(x.why)}</p>`).join('')}</div>`;
  s+=`<div class="sec"><b>Run</b>${run?`<p>exit <code>${run.exit_code}</code>${run.timed_out?' <span class="bad">timed out</span>':''} · ${run.wall_seconds}s · sandbox ${esc(run.sandbox)} · results ${esc(run.results_json)}</p>`:'<p class="muted">not run</p>'}
    ${r.approval?`<p class="muted">approved by ${esc(r.approval.approved_by)} at ${esc(r.approval.at)}${r.revisions?` after ${r.revisions} revision(s)`:''}</p>`:''}
    <p class="muted">code: ${r.code.map(c=>`<code>${esc(c)}</code>`).join(' ')||'—'}</p></div></div>`;
  if(r.summary)s+=`<div class="sec"><b>Results summary</b>${kv(r.summary)}</div>`;
  if(r.predictions)s+=`<div class="sec"><b>Pre-registered predictions</b><table><tr><th>hyp</th><th>prediction</th><th>falsified if</th></tr>${(r.predictions.predictions||[]).map(x=>`<tr><td>${esc(x.hypothesis)}</td><td>${esc(x.prediction)}</td><td>${esc(x.falsified_if)}</td></tr>`).join('')}</table></div>`;
  if(rv)s+=`<div class="sec"><b>Skeptic checks</b><table>${(rv.checks||[]).map(c=>`<tr><td class="${c.ok?'ok':'bad'}">${c.ok?'✓':'✗'}</td><td>${esc(c.name)}</td><td>${esc(c.detail)}</td></tr>`).join('')}</table></div>`;
  if(r.plan)s+=`<details class="sec"><summary><b>plan.md</b></summary><pre>${esc(r.plan)}</pre></details>`;
  if(r.review_md)s+=`<details class="sec"><summary><b>review.md</b></summary><pre>${esc(r.review_md)}</pre></details>`;
  return s+'</details>';
}
function render(){
  const alive=H.filter(h=>h.status!=='refuted');
  const lead=[...alive].sort((a,b)=>(b.credence[R[R.length-1]]??-1)-(a.credence[R[R.length-1]]??-1))[0];
  const runs=D.rounds.filter(r=>r.run).length;
  let s=`<h1>${esc(D.question)}</h1>${D.context?`<p class="muted">${esc(D.context)}</p>`:''}
  <div class="kpis"><div class="panel kpi"><div class="v"><span class="pill ${statusCls[D.status]||''}">${esc(D.status.replaceAll('_',' '))}</span></div><div class="l">next: <code>${esc(D.next)}</code></div></div>
  <div class="panel kpi"><div class="v">${D.round} / ${D.config.max_rounds}</div><div class="l">rounds</div></div>
  <div class="panel kpi"><div class="v">${runs}</div><div class="l">experiments run</div></div>
  <div class="panel kpi"><div class="v">${lead&&lead.credence[R[R.length-1]]!=null?esc(lead.id)+' · '+lead.credence[R[R.length-1]].toFixed(2):'—'}</div><div class="l">leading hypothesis</div></div></div>`;
  if(D.problems.length)s+=`<div class="panel sec bad"><b>Unresolved problems</b><ul>${D.problems.map(p=>`<li>${esc(p)}</li>`).join('')}</ul></div>`;
  s+=`<h2>Credence by round</h2><div class="panel">${chart()}</div>`;
  s+=`<h2>Hypotheses</h2><div class="panel"><table><tr><th>id</th><th>status</th><th>credence</th><th>statement</th></tr>${H.map((h,k)=>`<tr><td><span class="sw" style="background:${col(k,h)}"></span>${esc(h.id)}</td><td class="${h.status==='refuted'?'bad':h.status==='supported'?'ok':''}">${esc(h.status)}</td><td>${Object.entries(h.credence).sort().map(([r,v])=>v.toFixed(2)).join(' → ')||'—'}</td><td>${esc(h.statement)}</td></tr>`).join('')||'<tr><td colspan=4 class="muted">none yet</td></tr>'}</table></div>`;
  s+=`<h2>Rounds</h2>${D.rounds.map(roundCard).join('')||'<p class="muted">No rounds yet. Run <code>autolab plan</code>.</p>'}`;
  s+=`<h2>Notes</h2><div class="panel"><div class="tabs"><button data-t="findings" aria-pressed="true">Findings</button><button data-t="literature" aria-pressed="false">Literature</button></div><pre id="note"></pre></div>`;
  s+=`<p class="muted">Study folder: <code>${esc(D.root)}</code> · model ${esc(D.config.model)}</p>`;
  document.getElementById('app').innerHTML=s;
  const note=document.getElementById('note');const show=t=>{note.textContent=D[t]||'(nothing yet)';document.querySelectorAll('.tabs button').forEach(b=>b.setAttribute('aria-pressed',b.dataset.t===t))};
  document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>show(b.dataset.t));show('findings');
}
render();
</script></body></html>
"""
