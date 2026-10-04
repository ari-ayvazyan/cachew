// Cachew Studio UI: start research runs on Omnigent, watch the team, see what the shared cache saved.

type Usage = {
  input_tokens?: number;
  cache_creation_input_tokens?: number;
  cache_read_input_tokens?: number;
  output_tokens?: number;
};

type Agent = {
  role: string; name: string; focus: string; step: string; stage?: string; out?: string;
  status: string; usage?: Usage | null; usd?: number | null; usd_uncached?: number | null;
  started?: number | null; finished?: number | null; error?: string | null; session?: string | null; brief?: string | null;
};

type Stage = {
  stage: string; brief: string; chars: number; prefix_tokens: number; agents: string[]; prewarm?: string | null;
  writes: number; reads: number; cache_read_tokens: number; usd: number; usd_uncached: number; saved_usd: number;
  prefix_match: boolean;
};

type Savings = {
  usd: number; usd_uncached: number; saved_usd: number; saved_pct: number; calls: number;
  tokens: Required<Usage>;
};

type Step = { id: string; stage: string; role: string; fan: boolean; status: string; started?: number; finished?: number };

type View = {
  id: string;
  run: {
    question: string; context: string; model: string; effort: string; width: number; budget_usd: number;
    status: string; phase: string; created: number; step: number; steps: Step[]; min_cache_tokens?: number;
    briefs: Record<string, { id: string; chars: number; prewarm?: string; system_sha?: string; prefix_tokens?: number }>;
    pi_session?: string; verdict?: string; error?: string;
  };
  agents: Record<string, Agent>;
  prewarms: Record<string, { status?: string; usage?: Usage; usd?: number; started?: number; finished?: number; brief?: string }>;
  stages: Stage[]; savings: Savings; files: string[]; spent_usd: number; now: number;
};

type Model = { id: string; name: string; input: number; output: number; cache_read: number; cache_write: number; min_cache_tokens?: number };
type Config = { models: Model[]; efforts: string[]; omnigent_url: string };
type RunRow = { id: string; question: string; status: string; model: string; created: number; saved_usd: number; usd: number };

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const usd = (n?: number | null, digits = 4) => n == null ? "—" : `$${n.toFixed(n >= 1 ? 2 : digits)}`;
const ktok = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(n >= 1e5 ? 0 : 1)}k` : `${n}`;
const num = (n: number) => n.toLocaleString("en-US");

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch { /* keep status text */ }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

// ---------------------------------------------------------------- state

let config: Config;
let current: View | null = null;
let currentId: string | null = null;
let selected: string | null = null; // agent label, "stage:X" or "file:path"
let graphKey = "";
let runTimer: number | undefined;

// ---------------------------------------------------------------- theme

function initTheme() {
  try {
    const saved = localStorage.getItem("studio-theme");
    if (saved) document.documentElement.dataset.theme = saved;
  } catch { /* storage may be blocked */ }
  $("#toggle-theme").addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("studio-theme", next); } catch { /* ignore */ }
  });
}

// ---------------------------------------------------------------- new run form

function modelById(id: string) { return config.models.find(m => m.id === id)!; }

function updateEstimate() {
  const form = $<HTMLFormElement>("#new-run");
  const m = modelById((form.elements.namedItem("model") as HTMLSelectElement).value);
  const width = Number(($<HTMLInputElement>("#width")).value);
  $("#width-out").textContent = String(width);
  const effort = $<HTMLSelectElement>("#effort");
  effort.disabled = m.id.includes("haiku");
  const agents = 1 + 3 * width + 3;
  $("#estimate").innerHTML =
    `${agents} Omnigent agents · 3 fan-outs on 3 cached briefs.<br>` +
    `${esc(m.name)}: $${m.input}/MTok input, cache read $${m.cache_read}, cache write $${m.cache_write}. ` +
    `Briefs shorter than ${num(m.min_cache_tokens ?? 0)} tokens can't be cached on this model.`;
}

async function readFiles(input: HTMLInputElement): Promise<string> {
  const parts: string[] = [];
  for (const f of Array.from(input.files ?? [])) {
    if (f.size > 2_000_000) throw new Error(`${f.name} is larger than 2 MB`);
    parts.push(`### ${f.name}\n\n${await f.text()}`);
  }
  return parts.join("\n\n");
}

function initForm() {
  const form = $<HTMLFormElement>("#new-run");
  const model = $<HTMLSelectElement>("#model");
  model.innerHTML = config.models.map(m => `<option value="${esc(m.id)}">${esc(m.name)}</option>`).join("");
  const sonnet = config.models.find(m => m.id.includes("sonnet"));
  if (sonnet) model.value = sonnet.id;
  $<HTMLSelectElement>("#effort").innerHTML = config.efforts.map(e => `<option>${esc(e)}</option>`).join("");
  form.addEventListener("input", updateEstimate);
  updateEstimate();

  const files = $<HTMLInputElement>("#files");
  files.addEventListener("change", () => {
    const list = Array.from(files.files ?? []);
    $("#files-note").textContent = list.length
      ? `${list.length} file(s), ${ktok(list.reduce((a, f) => a + f.size, 0) / 4)} tok approx, added to the context`
      : "";
  });

  form.addEventListener("submit", async ev => {
    ev.preventDefault();
    const err = $("#form-error");
    err.hidden = true;
    const btn = $<HTMLButtonElement>("#start");
    btn.disabled = true;
    btn.textContent = "Starting Omnigent…";
    try {
      const fd = new FormData(form);
      const attached = await readFiles(files);
      const context = [String(fd.get("context") ?? "").trim(), attached].filter(Boolean).join("\n\n");
      const body = {
        question: String(fd.get("question") ?? "").trim(),
        context,
        model: String(fd.get("model")),
        effort: model.value.includes("haiku") ? "" : String(fd.get("effort") ?? "low"),
        width: Number(fd.get("width")),
        budget_usd: Number(fd.get("budget_usd")),
      };
      const { id } = await api<{ id: string }>("/api/runs", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      });
      await loadRuns();
      location.hash = `#/run/${id}`;
    } catch (e) {
      err.textContent = (e as Error).message;
      err.hidden = false;
    } finally {
      btn.disabled = false;
      btn.textContent = "Start research";
    }
  });
}

// ---------------------------------------------------------------- runs list

async function loadRuns() {
  let rows: RunRow[];
  try { rows = await api<RunRow[]>("/api/runs"); } catch { return; }
  const ul = $("#runs");
  if (!rows.length) {
    ul.innerHTML = `<li class="muted">No runs yet.</li>`;
    return;
  }
  ul.innerHTML = rows.map(r => `
    <li><a href="#/run/${esc(r.id)}" class="${r.id === currentId ? "active" : ""}">
      <div class="q">${esc(r.question)}</div>
      <div class="meta"><span>${esc(r.status)}</span><span>${esc(r.model?.replace("claude-", ""))}</span>
        <span>${usd(r.usd, 3)}</span>${r.saved_usd > 0 ? `<span class="saved">saved ${usd(r.saved_usd, 3)}</span>` : ""}</div>
    </a></li>`).join("");
}

// ---------------------------------------------------------------- routing

function route() {
  const m = location.hash.match(/^#\/run\/([\w.-]+)/);
  const id = m ? m[1] : null;
  if (id !== currentId) {
    currentId = id;
    current = null;
    graphKey = "";
    selected = null;
    closeInspector();
  }
  $("#empty").hidden = !!id;
  $("#run").hidden = !id;
  window.clearTimeout(runTimer);
  if (id) pollRun();
  loadRuns();
}

async function pollRun() {
  const id = currentId;
  if (!id) return;
  try {
    const v = await api<View>(`/api/runs/${encodeURIComponent(id)}`);
    if (id !== currentId) return;
    current = v;
    renderRun(v);
  } catch (e) {
    $("#phase").textContent = `Could not load the run: ${(e as Error).message}`;
  }
  const live = current && ["starting", "running"].includes(current.run.status);
  runTimer = window.setTimeout(pollRun, live ? 1500 : 8000);
}

// ---------------------------------------------------------------- run view

function renderRun(v: View) {
  $("#question").textContent = v.run.question;
  const step = v.run.steps[v.run.step];
  $("#phase").textContent = `${v.run.phase ?? ""}${step && v.run.status === "running" ? ` · step ${v.run.step + 1}/${v.run.steps.length}` : ""}` +
    ` · ${v.run.model.replace("claude-", "")}${v.run.effort ? ` (${v.run.effort})` : ""} · spent ${usd(v.spent_usd, 3)} of ${usd(v.run.budget_usd, 2)}`;
  const status = $("#status");
  status.textContent = v.run.status;
  status.className = `pill ${v.run.status}`;
  $("#stop").hidden = !["starting", "running"].includes(v.run.status);
  renderSavings(v);
  renderGraph(v);
  renderStages(v);
  renderVerdict(v);
  if (selected) renderInspector(v);
  tickElapsed();
}

function renderSavings(v: View) {
  const s = v.savings;
  const t = s.tokens;
  const maxUsd = Math.max(s.usd_uncached, s.usd, 1e-9);
  const pct = (x: number) => `${Math.max(0.5, (100 * x) / maxUsd).toFixed(1)}%`;
  const inputAll = t.input_tokens + t.cache_creation_input_tokens + t.cache_read_input_tokens;
  $("#savings").innerHTML = `
    <div class="hero">
      <h2>Saved by the shared cache</h2>
      <div class="big num">${usd(s.saved_usd)}<small>${s.saved_pct.toFixed(1)}%</small></div>
      <p>${num(t.cache_read_input_tokens)} input tokens served from cache across ${s.calls} model calls.</p>
    </div>
    <div class="compare">
      <div class="bar-row"><span>Without cache</span><div class="track"><div class="fill" style="width:${pct(s.usd_uncached)}"></div></div><span class="val">${usd(s.usd_uncached)}</span></div>
      <div class="bar-row"><span>With cache</span><div class="track"><div class="fill with" style="width:${pct(s.usd)}"></div></div><span class="val">${usd(s.usd)}</span></div>
      <div class="bar-row muted small"><span>Cache share</span><div class="track"><div class="fill with" style="width:${inputAll ? (100 * t.cache_read_input_tokens / inputAll).toFixed(1) : 0}%"></div></div><span class="val">${inputAll ? (100 * t.cache_read_input_tokens / inputAll).toFixed(0) : 0}% of input</span></div>
    </div>
    <div class="tokens">
      <div class="read"><b>${ktok(t.cache_read_input_tokens)}</b>read from cache</div>
      <div><b>${ktok(t.cache_creation_input_tokens)}</b>written to cache</div>
      <div><b>${ktok(t.input_tokens)}</b>uncached input</div>
      <div><b>${ktok(t.output_tokens)}</b>output</div>
    </div>
    <p class="foot">"Without cache" prices every input token at the base rate. "With cache" is the real bill: it includes the pre-warm call
      before each fan-out and the 1.25× cache-write premium, so the savings shown are net. Usage comes from the API responses that went through
      Omnigent's cachew harness.</p>`;
}

// ---------------------------------------------------------------- graph

const NW = 230, NH = 112, GX = 64, GY = 22, HH = 46;
const ICONS: Record<string, string> = {
  pi: `<path d="M12 3l9 4-9 4-9-4 9-4z"/><path d="M6 9.5V14c0 1.5 3 3 6 3s6-1.5 6-3V9.5"/>`,
  scout: `<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>`,
  theorist: `<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 00-3.5 10.9V16h7v-2.1A6 6 0 0012 3z"/>`,
  lead: `<path d="M6 4v6a6 6 0 0012 0V4"/><path d="M12 16v4M8 20h8"/>`,
  experimenter: `<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 001.3 2h12.4a1.5 1.5 0 001.3-2L14 9V3"/>`,
  skeptic: `<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/>`,
  judge: `<path d="M12 3v18M5 7h14M5 7l-3 7a3 3 0 006 0L5 7zM19 7l-3 7a3 3 0 006 0l-3-7z"/>`,
  brief: `<path d="M4 6h16M4 12h16M4 18h10"/>`,
  file: `<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>`,
};
const icon = (k: string, size = 18) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${ICONS[k] ?? ICONS.file}</svg>`;

type Box = { x: number; y: number; w: number; h: number };
const ARTIFACTS = ["plan.md", "literature/", "proposals/", "candidates.json", "predictions.json", "experiment/",
  "selection.json", "reviews/", "verdict.md", "briefs/"];

function layout(v: View) {
  const a = v.agents;
  const labels = (role: string) => Object.keys(a).filter(l => a[l].role === role).sort();
  const cols: { stage?: string; nodes: string[] }[] = [
    { stage: "A", nodes: labels("scout") },
    { stage: "B", nodes: [...labels("lead"), ...labels("theorist")] },
    { nodes: labels("experimenter") },
    { stage: "C", nodes: labels("skeptic") },
    { nodes: labels("judge") },
  ];
  const boxes: Record<string, Box> = {};
  const top = 20 + NH + 56;           // stage headers
  const first = top + HH + 34;        // first agent row
  let maxY = first;
  cols.forEach((c, ci) => {
    const x = 20 + ci * (NW + GX);
    if (c.stage) boxes[`stage:${c.stage}`] = { x, y: top, w: NW, h: HH };
    c.nodes.forEach((l, ri) => {
      boxes[l] = { x, y: first + ri * (NH + GY), w: NW, h: NH };
      maxY = Math.max(maxY, first + ri * (NH + GY) + NH);
    });
  });
  const width = 20 + cols.length * (NW + GX) - GX + 20;
  boxes.pi = { x: (width - NW) / 2, y: 20, w: NW, h: NH };
  // artifact chips along the bottom
  const fy = maxY + 48;
  let fx = 20, fRow = 0;
  const chipW = (n: string) => 44 + n.length * 7.4;
  for (const f of ARTIFACTS) {
    const w = chipW(f);
    if (fx + w > width - 20) { fx = 20; fRow++; }
    boxes[`file:${f}`] = { x: fx, y: fy + fRow * 46, w, h: 34 };
    fx += w + 12;
  }
  return { boxes, width, height: fy + (fRow + 1) * 46 + 4 };
}

function tokens(u?: Usage | null) {
  if (!u) return 0;
  return (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.output_tokens ?? 0);
}

function cacheShare(u?: Usage | null) {
  if (!u) return 0;
  const inp = (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0);
  return inp ? (u.cache_read_input_tokens ?? 0) / inp : 0;
}

const isLive = (s?: string) => s === "thinking" || s === "dispatched" || s === "waiting";

function fileReady(v: View, f: string) {
  return f.endsWith("/") ? v.files.some(p => p.startsWith(f)) : v.files.includes(f);
}

function piUsage(v: View): Usage | null {
  return v.agents.pi?.usage ?? null;
}

function nodeHtml(v: View, label: string, b: Box) {
  const a = v.agents[label];
  const live = isLive(a.status);
  let sub = esc(a.focus);
  if (label === "pi") {
    sub = a.status === "waiting" ? "waiting for agents" : v.run.status === "done" ? "verdict written" : esc(a.focus);
  } else if (a.status === "thinking" || a.status === "dispatched") {
    sub = `${a.status === "dispatched" ? "starting" : "thinking"} · <span data-since="${a.started ?? v.now}"></span>`;
  } else if (a.status === "error") {
    sub = `error: ${esc(a.error ?? "")}`;
  }
  const u = label === "pi" ? piUsage(v) : a.usage;
  const share = cacheShare(u);
  return `<div class="node ${label === "pi" ? "pi" : ""} ${live ? "thinking" : ""} ${selected === label ? "selected" : ""}"
      data-sel="${esc(label)}" style="left:${b.x}px;top:${b.y}px">
    <div>
      <div class="nhead">${icon(a.role)}<span class="name">${esc(a.name)}</span><i class="dot ${esc(a.status)}" title="${esc(a.status)}"></i></div>
      <div class="sub">${sub}</div>
    </div>
    <div class="nfoot">
      <span>${ktok(tokens(u))} tok</span>
      ${label === "pi" ? "" : `<span class="cachebar" title="${(100 * share).toFixed(0)}% of input read from cache"><i style="width:${(100 * share).toFixed(1)}%"></i></span>`}
      <span>${u ? usd(a.usd, 3) : ""}</span>
    </div>
  </div>`;
}

function stageHtml(v: View, stage: string, b: Box) {
  const brief = v.run.briefs?.[stage];
  const warm = brief?.prewarm ? v.prewarms[brief.prewarm] : undefined;
  const w = warm?.usage?.cache_creation_input_tokens ?? 0;
  const line = !brief ? "not published yet"
    : warm?.status === "done" ? `pre-warmed ${ktok(w || brief.prefix_tokens || 0)} tok`
    : warm?.status ? `pre-warm ${warm.status}…` : `${ktok(brief.prefix_tokens ?? 0)} tok · below cache minimum`;
  return `<div class="stage-head ${brief ? "" : "pending"} ${selected === `stage:${stage}` ? "selected" : ""}" data-sel="stage:${stage}"
      style="left:${b.x}px;top:${b.y}px;cursor:pointer">
    <b>${icon("brief", 14)} Brief ${esc(stage)} · shared cache</b><span>${line}</span>
  </div>`;
}

function orth(a: Box, b: Box, from: "bottom" | "right", to: "top" | "left"): string {
  const x1 = from === "bottom" ? a.x + a.w / 2 : a.x + a.w, y1 = from === "bottom" ? a.y + a.h : a.y + a.h / 2;
  const x2 = to === "top" ? b.x + b.w / 2 : b.x, y2 = to === "top" ? b.y : b.y + b.h / 2;
  const r = 8;
  if (from === "bottom" && to === "top") {
    if (Math.abs(x1 - x2) < 1) return `M${x1},${y1} V${y2}`;
    const my = y1 + Math.max(14, (y2 - y1) / 2);
    const dx = Math.sign(x2 - x1) * r;
    return `M${x1},${y1} V${my - r} Q${x1},${my} ${x1 + dx},${my} H${x2 - dx} Q${x2},${my} ${x2},${my + r} V${y2}`;
  }
  if (from === "right" && to === "left") {
    if (Math.abs(y1 - y2) < 1) return `M${x1},${y1} H${x2}`;
    const mx = x1 + (x2 - x1) / 2;
    const dy = Math.sign(y2 - y1) * r;
    return `M${x1},${y1} H${mx - r} Q${mx},${y1} ${mx},${y1 + dy} V${y2 - dy} Q${mx},${y2} ${mx + r},${y2} H${x2}`;
  }
  // bottom -> left
  return `M${x1},${y1} V${y2 - r} Q${x1},${y2} ${x1 + r},${y2} H${x2}`;
}

function renderGraph(v: View) {
  const key = JSON.stringify([v.agents, v.run.briefs, v.prewarms, v.files, v.run.status, selected]);
  if (key === graphKey) return;
  graphKey = key;
  const { boxes, width, height } = layout(v);
  const a = v.agents;
  const edges: { d: string; cls: string; from?: string; to?: string }[] = [];
  const st = (l: string) => a[l]?.status ?? "idle";
  const edgeCls = (from: string, to: string) =>
    isLive(st(to)) ? "active" : st(to) === "done" && (from.startsWith("stage:") || st(from) === "done") ? "done" : "";
  const link = (from: string, to: string, fs: "bottom" | "right", ts: "top" | "left", extra = "") => {
    if (boxes[from] && boxes[to]) edges.push({ d: orth(boxes[from], boxes[to], fs, ts), cls: `${edgeCls(from, to)} ${extra}`, from, to });
  };
  const byRole = (r: string) => Object.keys(a).filter(l => a[l].role === r).sort();

  for (const s of ["A", "B", "C"]) {
    const b = v.run.briefs?.[s];
    edges.push({ d: orth(boxes.pi, boxes[`stage:${s}`], "bottom", "top"), cls: b ? "brief" : "", from: "pi", to: `stage:${s}` });
  }
  // a stage's brief feeds the members in its column; members inside a column chain top-down
  const column = (stage: string, roles: string[]) => {
    const nodes = roles.flatMap(byRole);
    const s = `stage:${stage}`;
    nodes.forEach((n, i) => link(i === 0 ? s : nodes[i - 1], n, "bottom", "top", i === 0 ? "brief" : "brief"));
  };
  column("A", ["scout"]);
  column("B", ["lead", "theorist"]);
  column("C", ["skeptic"]);
  // hand-offs between steps
  for (const s of byRole("scout")) link(s, "stage:B", "right", "left");
  for (const t of byRole("theorist")) link(t, "lead", "right", "left");
  link("stage:B", "experimenter", "right", "left", "brief");
  link("lead", "experimenter", "right", "left");
  link("experimenter", "stage:C", "right", "left");
  for (const s of byRole("skeptic")) link(s, "judge", "right", "left");

  // files written by the selected agent
  const outs = selected && a[selected]?.out ? [a[selected].out!] : [];
  const fileFor = (out: string) => ARTIFACTS.find(f => f.endsWith("/") ? out.startsWith(f) : out === f || out.replace(/\.md$/, ".json") === f);

  let html = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
    <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,1 L9,5 L0,9 z" class="arrow"/></marker></defs>
    ${edges.map(e => `<path class="edge ${e.cls}" d="${e.d}" marker-end="url(#arr)"/>`).join("")}
    ${outs.map(o => {
      const f = fileFor(o);
      return f && boxes[f === undefined ? "" : `file:${f}`] && boxes[selected!]
        ? `<path class="edge file show" d="${orth(boxes[selected!], boxes[`file:${f}`], "bottom", "top")}"/>` : "";
    }).join("")}
  </svg>`;
  for (const s of ["A", "B", "C"]) html += stageHtml(v, s, boxes[`stage:${s}`]);
  for (const l of Object.keys(a)) if (boxes[l]) html += nodeHtml(v, l, boxes[l]);
  for (const f of ARTIFACTS) {
    const b = boxes[`file:${f}`];
    const hl = outs.some(o => fileFor(o) === f);
    html += `<div class="file ${fileReady(v, f) ? "ready" : ""} ${hl ? "hl" : ""}" data-sel="file:${esc(f)}"
      style="left:${b.x}px;top:${b.y}px;width:${b.w}px">${icon(f.endsWith("/") ? "brief" : "file", 14)}${esc(f)}</div>`;
  }
  const canvas = $("#canvas");
  canvas.innerHTML = html;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  fitCanvas();
}

function fitCanvas() {
  const canvas = $("#canvas"), wrap = $("#canvas-wrap");
  const w = parseFloat(canvas.style.width || "0"), h = parseFloat(canvas.style.height || "0");
  if (!w) return;
  const scale = Math.min(1, (wrap.clientWidth - 8) / w);
  canvas.style.transform = `scale(${scale})`;
  canvas.style.left = `${Math.max(0, (wrap.clientWidth - w * scale) / 2)}px`;
  wrap.style.height = `${h * scale + 8}px`;
}

function tickElapsed() {
  const now = Date.now() / 1000;
  const skew = current ? current.now - (performance.timeOrigin + performance.now()) / 1000 : 0;
  document.querySelectorAll<HTMLElement>("[data-since]").forEach(el => {
    const s = Math.max(0, Math.round(now + skew - Number(el.dataset.since)));
    el.textContent = s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
  });
}

// ---------------------------------------------------------------- stages table

function renderStages(v: View) {
  if (!v.stages.length) {
    $("#stages").innerHTML = `<p class="muted">Before each fan-out the PI writes what the team knows into a brief, publishes it, and pre-warms it in the prompt cache
      with one call. The sub-agents of that fan-out then read the brief from the cache. Nothing has been published yet.</p>`;
    return;
  }
  const rows = v.stages.map(s => `<tr data-sel="stage:${esc(s.stage)}" style="cursor:pointer">
      <td><b>Brief ${esc(s.stage)}</b><div class="muted small mono">${esc(s.brief)}</div></td>
      <td class="r">${num(s.prefix_tokens)}</td>
      <td>${s.agents.map(l => `<span class="tag">${esc(v.agents[l]?.name ?? l)}</span>`).join("") || `<span class="muted">—</span>`}</td>
      <td>${esc(s.prewarm ?? "skipped")}</td>
      <td class="r">${s.writes}</td>
      <td class="r">${s.reads}</td>
      <td class="r">${ktok(s.cache_read_tokens)}</td>
      <td class="r">${usd(s.usd_uncached)}</td>
      <td class="r">${usd(s.usd)}</td>
      <td class="r"><span class="${s.saved_usd > 0 ? "good" : ""}">${usd(s.saved_usd)}</span></td>
      <td>${s.prefix_match ? `<span class="ok">✓ identical</span>` : `<span class="no">✕ differs</span>`}</td>
    </tr>`).join("");
  $("#stages").innerHTML = `<div style="overflow-x:auto"><table>
    <thead><tr><th>Brief</th><th class="r">Prefix tok</th><th>Shared by</th><th>Pre-warm</th><th class="r">Writes</th>
      <th class="r">Reads</th><th class="r">From cache</th><th class="r">No cache</th><th class="r">With cache</th><th class="r">Saved</th><th>System prefix</th></tr></thead>
    <tbody>${rows}</tbody></table></div>
    <p class="muted small">One write (the pre-warm) and one read per sub-agent is the goal. "With cache" includes the pre-warm call.
      "System prefix" checks that every member got byte-identical instructions from Omnigent, which the cache needs.</p>`;
}

// ---------------------------------------------------------------- verdict

async function renderVerdict(v: View) {
  const card = $("#verdict-card");
  if (!v.files.includes("verdict.md")) {
    card.hidden = true;
    return;
  }
  card.hidden = false;
  const el = $("#verdict");
  if (el.dataset.run === v.id) return;
  try {
    el.innerHTML = markdown(await api<string>(`/api/runs/${encodeURIComponent(v.id)}/file?path=verdict.md`));
    el.dataset.run = v.id;
  } catch { /* retry next poll */ }
}

// ---------------------------------------------------------------- inspector

function closeInspector() {
  $("#inspector").hidden = true;
  document.querySelector(".layout")!.classList.remove("with-inspector");
}

function select(sel: string | null) {
  selected = selected === sel ? null : sel;
  graphKey = "";
  if (!current) return;
  renderGraph(current);
  if (selected) renderInspector(current);
  else closeInspector();
}

const insp = { key: "", fileKey: "" };

function kv(rows: [string, string][]) {
  return `<dl class="kv">${rows.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${val}</dd>`).join("")}</dl>`;
}

function usageRows(u?: Usage | null, cost?: number | null, uncached?: number | null): [string, string][] {
  if (!u) return [["Usage", `<span class="muted">no call yet</span>`]];
  return [
    ["Read from cache", `${num(u.cache_read_input_tokens ?? 0)} tok`],
    ["Written to cache", `${num(u.cache_creation_input_tokens ?? 0)} tok`],
    ["Uncached input", `${num(u.input_tokens ?? 0)} tok`],
    ["Output", `${num(u.output_tokens ?? 0)} tok`],
    ["Cost", `${usd(cost)} <span class="muted">(no cache: ${usd(uncached)})</span>`],
  ];
}

async function renderInspector(v: View) {
  const panel = $("#inspector");
  panel.hidden = false;
  document.querySelector(".layout")!.classList.add("with-inspector");
  const body = $("#insp-body");
  const sel = selected!;
  let head = "";
  let file: string | null = null;
  const omni = (s?: string | null) => s ? `<a href="${esc(config.omnigent_url)}/c/${esc(s)}" target="_blank" rel="noopener">open in Omnigent ↗</a>` : `<span class="muted">—</span>`;

  if (sel.startsWith("stage:")) {
    const stage = sel.slice(6);
    const b = v.run.briefs?.[stage];
    const row = v.stages.find(s => s.stage === stage);
    const warm = b?.prewarm ? v.prewarms[b.prewarm] : undefined;
    $("#insp-title").textContent = `Brief ${stage}`;
    head = !b ? `<p class="muted">Not published yet. The PI publishes it right before the fan-out starts.</p>` : kv([
      ["Brief id", `<code>${esc(b.id)}</code>`],
      ["Cached prefix", `${num(b.prefix_tokens ?? 0)} tok <span class="muted">(min ${num(v.run.min_cache_tokens ?? 0)})</span>`],
      ["Pre-warm", esc(warm?.status ?? "skipped")],
      ["Pre-warm wrote", `${num(warm?.usage?.cache_creation_input_tokens ?? 0)} tok for ${usd(warm?.usd)}`],
      ["Members read", row ? `${row.reads} × · ${num(row.cache_read_tokens)} tok` : "—"],
      ["Saved", row ? `<b class="ok">${usd(row.saved_usd)}</b>` : "—"],
      ["System sha", `<code>${esc(b.system_sha ?? "")}</code>`],
    ]);
    if (b) file = `briefs/${b.id}.md`;
  } else if (sel.startsWith("file:")) {
    const f = sel.slice(5);
    $("#insp-title").textContent = f;
    if (f.endsWith("/")) {
      const list = v.files.filter(p => p.startsWith(f));
      head = list.length
        ? `<ul class="md">${list.map(p => `<li><a href="#" data-open="${esc(p)}">${esc(p)}</a></li>`).join("")}</ul>`
        : `<p class="muted">Nothing written yet.</p>`;
    } else {
      file = f;
    }
  } else if (sel.startsWith("path:")) {
    file = sel.slice(5);
    $("#insp-title").textContent = file;
  } else {
    const a = v.agents[sel];
    if (!a) return;
    $("#insp-title").textContent = a.name;
    const elapsed = a.started ? (a.finished ?? v.now) - a.started : 0;
    head = kv([
      ["Role", `${esc(a.role)} · ${esc(a.focus)}`],
      ["Status", `<i class="dot ${esc(a.status)}"></i> ${esc(a.status)}${elapsed ? ` · ${elapsed.toFixed(0)}s` : ""}`],
      ["Model", esc(v.run.model)],
      ["Brief", a.stage ? `Brief ${esc(a.stage)}${a.brief ? ` <code>${esc(a.brief)}</code>` : ""}` : "—"],
      ...usageRows(a.usage, a.usd, a.usd_uncached),
      ["Omnigent", omni(sel === "pi" ? v.run.pi_session : a.session)],
      ...(a.error ? [["Error", `<span class="no">${esc(a.error)}</span>`] as [string, string]] : []),
    ]);
    file = sel === "pi" ? "plan.md" : a.out ?? null;
  }
  const key = JSON.stringify([sel, head]);
  if (key !== insp.key) {
    insp.key = key;
    body.innerHTML = `${head}<div id="insp-file"></div>`;
    insp.fileKey = "";
  }
  if (file && v.files.includes(file)) {
    const fk = `${v.id}:${file}`;
    if (insp.fileKey === fk) return;
    insp.fileKey = fk;
    try {
      const text = await api<string>(`/api/runs/${encodeURIComponent(v.id)}/file?path=${encodeURIComponent(file)}`);
      const box = document.getElementById("insp-file");
      if (box && insp.fileKey === fk) {
        box.innerHTML = `<h2 style="margin:6px 0 8px">${esc(file)}</h2>` +
          (file.endsWith(".md") ? `<div class="md">${markdown(text)}</div>` : `<div class="md"><pre><code>${esc(text)}</code></pre></div>`);
      }
    } catch { insp.fileKey = ""; }
  } else if (file) {
    const box = document.getElementById("insp-file");
    if (box) box.innerHTML = `<p class="muted small">${esc(file)} is not written yet.</p>`;
    insp.fileKey = "";
  }
}

// ---------------------------------------------------------------- markdown (small, escape-first)

function inline(s: string) {
  return s
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^*])\*([^*\s][^*]*)\*/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, `<a href="$2" target="_blank" rel="noopener">$1</a>`);
}

function markdown(src: string): string {
  const lines = esc(src).replace(/\r/g, "").split("\n");
  const out: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^```/.test(l)) {
      const code: string[] = [];
      for (i++; i < lines.length && !/^```/.test(lines[i]); i++) code.push(lines[i]);
      out.push(`<pre><code>${code.join("\n")}</code></pre>`);
      i++;
      continue;
    }
    const h = l.match(/^(#{1,4})\s+(.*)/);
    if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue; }
    if (/^\s*([-*_])\s*\1\s*\1\s*$/.test(l)) { out.push("<hr>"); i++; continue; }
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows: string[][] = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) {
        if (/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) continue;
        rows.push(lines[i].trim().slice(1, -1).split("|").map(c => inline(c.trim())));
      }
      const [hd, ...rest] = rows;
      out.push(`<table><thead><tr>${hd.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rest.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
      continue;
    }
    if (/^\s*([-*+]|\d+\.)\s+/.test(l)) {
      const ordered = /^\s*\d+\./.test(l);
      const items: string[] = [];
      for (; i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ""));
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map(t => `<li>${inline(t)}</li>`).join("")}</${tag}>`);
      continue;
    }
    if (!l.trim()) { i++; continue; }
    const para: string[] = [];
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*+]|\d+\.)\s+|\s*\|)/.test(lines[i]); i++) para.push(lines[i]);
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- wiring

async function main() {
  initTheme();
  config = await api<Config>("/api/config");
  $("#omnigent-link").innerHTML = `<a href="${esc(config.omnigent_url)}" target="_blank" rel="noopener">Omnigent ↗</a>`;
  initForm();

  document.addEventListener("click", ev => {
    const t = ev.target as HTMLElement;
    const open = t.closest<HTMLElement>("[data-open]");
    if (open) {
      ev.preventDefault();
      selected = null;
      select(`path:${open.dataset.open}`);
      return;
    }
    const hit = t.closest<HTMLElement>("[data-sel]");
    if (hit && (hit.closest("#canvas") || hit.closest("#stages"))) select(hit.dataset.sel!);
  });
  $("#insp-close").addEventListener("click", () => { selected = null; graphKey = ""; closeInspector(); if (current) renderGraph(current); });
  $("#stop").addEventListener("click", async () => {
    if (!currentId || !confirm("Stop this run? Agents that are already thinking finish their call; nothing new is dispatched.")) return;
    try { await api(`/api/runs/${encodeURIComponent(currentId)}/stop`, { method: "POST" }); } catch (e) { alert((e as Error).message); }
    pollRun();
  });
  window.addEventListener("hashchange", route);
  new ResizeObserver(fitCanvas).observe($("#canvas-wrap"));
  setInterval(tickElapsed, 1000);
  setInterval(loadRuns, 5000);
  route();
}

main().catch(e => {
  document.body.insertAdjacentHTML("afterbegin", `<p class="error" style="padding:12px 20px">Studio failed to start: ${esc((e as Error).message)}</p>`);
});
