// Cachew Studio UI: start research on Omnigent, follow the discovery loop live, and audit every decision.

// ---------------------------------------------------------------- types

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
type Candidate = { id: string; claim?: string; prior?: number };
type Prediction = { id: string; test?: string; predicts?: string };
type TestSelection = { tests: string[]; chosen: string; chosen_index: number; why: string };
type Decision = {
  answer?: string; status?: string | null; posterior?: { id: string; p: number }[];
  next_experiment?: string; validation_needed?: string; source?: string;
};
type Review = { label: string; name: string; focus: string; out?: string; verdict: string | null };
type ApprovalRec = { decision: string; test?: string; note?: string; changed?: boolean; at?: number };
type Science = {
  candidates: Candidate[]; predictions: Prediction[]; selection: TestSelection | null; decision: Decision | null;
  reviews: Review[]; approval: ApprovalRec | null;
};
type Fanout = { step: string; agents: number; serial_s: number; wall_s: number; speedup: number | null };
type Accel = {
  fanouts: Fanout[]; serial_s: number; wall_s: number; speedup: number | null; agent_s: number; run_s: number;
  cost_ratio: number | null;
};

type Run = {
  question: string; context: string; model: string; effort: string; width: number; budget_usd: number;
  status: string; phase: string; created: number; finished?: number; step: number; steps: Step[];
  min_cache_tokens?: number; approval?: boolean; key?: string;
  briefs: Record<string, { id: string; chars: number; prewarm?: string; system_sha?: string; prefix_tokens?: number }>;
  pi_session?: string; verdict?: string; error?: string;
};

type View = {
  id: string; run: Run; agents: Record<string, Agent>;
  prewarms: Record<string, { status?: string; usage?: Usage; usd?: number; started?: number; finished?: number; brief?: string }>;
  stages: Stage[]; savings: Savings; files: string[]; bundle: string[]; authors: Record<string, string>;
  science: Science; acceleration: Accel; spent_usd: number; now: number;
};

type Model = { id: string; name: string; input: number; output: number; cache_read: number; cache_write: number; min_cache_tokens?: number };
type Tier = { models: Record<string, string[]>; max_width: number | null; max_budget_usd: number };
type RoleSpec = { name: string; decision: string; inputs: string; output: string; tools: string };
type Config = {
  models: Model[]; efforts: string[]; omnigent_url: string; shared_key: boolean;
  tiers: { shared: Tier; own: Tier }; roles: Record<string, RoleSpec>; approval_timeout_s: number;
};
type RunRow = { id: string; question: string; status: string; model: string; created: number; saved_usd: number; usd: number; agents?: number };

// ---------------------------------------------------------------- helpers

const $ = <T extends HTMLElement = HTMLElement>(sel: string) => document.querySelector(sel) as T;
const esc = (s: unknown) => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
const usd = (n?: number | null, digits = 3) => n == null ? "—" : `$${n.toFixed(n >= 10 ? 2 : n >= 1 ? 2 : digits)}`;
const ktok = (n: number) => n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1000 ? `${(n / 1000).toFixed(n >= 1e5 ? 0 : 1)}k` : `${Math.round(n)}`;
const num = (n: number) => n.toLocaleString("en-US");
const pct = (x: number) => `${Math.round(100 * x)}%`;
const times = (x?: number | null) => x == null || !isFinite(x) ? "—" : `${x.toFixed(x >= 10 ? 0 : 1)}×`;
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
const shortModel = (m: string) => m.replace(/^claude-/, "").replace(/-(\d+)-(\d+)$/, " $1.$2").replace(/^./, c => c.toUpperCase());

function dur(s: number) {
  s = Math.max(0, Math.round(s));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function ago(t: number) {
  const s = Date.now() / 1000 - t;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const r = await fetch(path, init);
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`;
    try {
      const body = await r.json();
      const d = body.detail;
      msg = typeof d === "string" ? d : Array.isArray(d) ? d.map((e: { msg?: string; loc?: string[] }) =>
        `${(e.loc ?? []).filter(x => x !== "body").join(".")}: ${e.msg}`).join("; ") : JSON.stringify(d ?? body);
    } catch { /* keep status text */ }
    throw new Error(msg);
  }
  const ct = r.headers.get("content-type") ?? "";
  return (ct.includes("json") ? r.json() : r.text()) as Promise<T>;
}

const store = {
  get(area: "local" | "session", k: string) {
    try { return (area === "local" ? localStorage : sessionStorage).getItem(k); } catch { return null; }
  },
  set(area: "local" | "session", k: string, v: string | null) {
    try {
      const s = area === "local" ? localStorage : sessionStorage;
      if (v == null) s.removeItem(k); else s.setItem(k, v);
    } catch { /* storage may be blocked */ }
  },
};

let toastTimer: number | undefined;
function toast(msg: string, bad = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = `toast${bad ? " bad" : ""}`;
  el.hidden = false;
  window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => { el.hidden = true; }, 4200);
}

function confirmDialog(title: string, text: string, ok = "Confirm"): Promise<boolean> {
  const dlg = $<HTMLDialogElement>("#confirm");
  $("#confirm-title").textContent = title;
  $("#confirm-text").textContent = text;
  $("#confirm-ok").textContent = ok;
  if (typeof dlg.showModal !== "function") return Promise.resolve(window.confirm(`${title}\n\n${text}`));
  dlg.returnValue = "";
  dlg.showModal();
  return new Promise(resolve => dlg.addEventListener("close", () => resolve(dlg.returnValue === "ok"), { once: true }));
}

// ---------------------------------------------------------------- icons

const ICONS: Record<string, string> = {
  question: `<circle cx="12" cy="12" r="9"/><path d="M9.5 9.2a2.6 2.6 0 015 .6c0 1.7-2.5 2.2-2.5 3.7"/><path d="M12 17h.01"/>`,
  evidence: `<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>`,
  hypotheses: `<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 00-3.5 10.9V16h7v-2.1A6 6 0 0012 3z"/>`,
  experiment: `<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 001.3 2h12.4a1.5 1.5 0 001.3-2L14 9V3"/><path d="M7 15h10"/>`,
  approval: `<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6.5 6.5-6.5 1.4 0 2.6.4 3.7 1.1"/><path d="M15 17.5l2.2 2.2 4.3-4.4"/>`,
  review: `<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/><path d="M9 12l2 2 4-4"/>`,
  decision: `<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1.2"/>`,
  pi: `<path d="M12 4l9 4-9 4-9-4 9-4z"/><path d="M6 10v4.5c0 1.5 3 3 6 3s6-1.5 6-3V10"/><path d="M21 8v5"/>`,
  scout: `<circle cx="11" cy="11" r="6"/><path d="M20 20l-4.5-4.5"/>`,
  theorist: `<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 00-3.5 10.9V16h7v-2.1A6 6 0 0012 3z"/>`,
  lead: `<path d="M6 4v6a6 6 0 0012 0V4"/><path d="M12 16v4M8 20h8"/>`,
  experimenter: `<path d="M9 3h6M10 3v6L4.5 19a1.5 1.5 0 001.3 2h12.4a1.5 1.5 0 001.3-2L14 9V3"/>`,
  skeptic: `<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/>`,
  judge: `<path d="M12 3v18M5 7h14M5 7l-3 7a3 3 0 006 0L5 7zM19 7l-3 7a3 3 0 006 0l-3-7z"/>`,
  human: `<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-8 8-8s8 3.6 8 8"/>`,
  brief: `<path d="M4 6h16M4 12h16M4 18h10"/>`,
  file: `<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>`,
  code: `<path d="M8 8l-4 4 4 4M16 8l4 4-4 4"/>`,
  json: `<path d="M8 4c-2 0-2 1.5-2 3s0 3-2 5c2 2 2 3.5 2 5s0 3 2 3M16 4c2 0 2 1.5 2 3s0 3 2 5c-2 2-2 3.5-2 5s0 3-2 3"/>`,
  yaml: `<rect x="4" y="4" width="16" height="16" rx="3"/><path d="M8 9h8M8 13h5"/>`,
  check: `<path d="M5 12.5l4.5 4.5L19 7.5"/>`,
  clock: `<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>`,
  coins: `<ellipse cx="12" cy="6" rx="7" ry="3"/><path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6"/><path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6"/>`,
  zap: `<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>`,
  users: `<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6.5 6.5-6.5s6.5 2.9 6.5 6.5"/><path d="M16 4.5a3.5 3.5 0 010 7M18 13.8c2 .9 3.5 3 3.5 5.7"/>`,
  key: `<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M17 6l3 3M15 8l2 2"/>`,
  alert: `<path d="M12 3l9.5 17h-19L12 3z"/><path d="M12 10v4M12 17.5h.01"/>`,
  loop: `<path d="M17 2l3 3-3 3"/><path d="M4 11V9a4 4 0 014-4h12"/><path d="M7 22l-3-3 3-3"/><path d="M20 13v2a4 4 0 01-4 4H4"/>`,
  record: `<path d="M4 4.5A2.5 2.5 0 016.5 2H20v17H6.5A2.5 2.5 0 004 21.5v-17z"/><path d="M4 19.5A2.5 2.5 0 016.5 17H20"/>`,
  stop: `<rect x="6" y="6" width="12" height="12" rx="2"/>`,
  back: `<path d="M15 6l-6 6 6 6"/>`,
  arrow: `<path d="M5 12h14M13 6l6 6-6 6"/>`,
  upload: `<path d="M12 16V4M7 9l5-5 5 5"/><path d="M4 16v3a2 2 0 002 2h12a2 2 0 002-2v-3"/>`,
  lock: `<rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 018 0v4"/>`,
  info: `<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 7.5h.01"/>`,
  cpu: `<rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9z"/><path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/>`,
  gauge: `<path d="M12 14l4-4"/><path d="M3.5 18a9 9 0 1117 0"/>`,
  eye: `<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>`,
  eyeOff: `<path d="M3 3l18 18"/><path d="M10.6 5.1A10 10 0 0112 5c6.5 0 10 7 10 7a17 17 0 01-3.1 4M6.6 6.6A17 17 0 002 12s3.5 7 10 7a9.7 9.7 0 005.4-1.6"/><path d="M9.9 9.9a3 3 0 004.2 4.2"/>`,
  external: `<path d="M14 4h6v6M20 4l-9 9"/><path d="M18 14v5a1 1 0 01-1 1H5a1 1 0 01-1-1V7a1 1 0 011-1h5"/>`,
  shield: `<path d="M12 3l8 3v6c0 4.5-3.5 8-8 9-4.5-1-8-4.5-8-9V6l8-3z"/>`,
  sparkle: `<path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8L19 15z"/>`,
  x: `<path d="M6 6l12 12M18 6L6 18"/>`,
};
const icon = (k: string, size = 18, sw = 1.8) =>
  `<svg viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[k] ?? ICONS.file}</svg>`;

// ---------------------------------------------------------------- the discovery loop

type LoopItem = { key: string; label: string; icon: string; steps: string[]; blurb: string; brief?: string };
const LOOP: LoopItem[] = [
  { key: "question", label: "Question", icon: "question", steps: ["plan"], blurb: "The PI turns the question into a plan and defines what counts as an answer." },
  { key: "evidence", label: "Evidence", icon: "evidence", steps: ["scouts"], brief: "A", blurb: "Scouts gather prior results, methods and counter-evidence in parallel." },
  { key: "hypotheses", label: "Hypotheses", icon: "hypotheses", steps: ["theorists", "lead"], brief: "B", blurb: "Theorists propose competing hypotheses; the lead theorist merges them and assigns priors." },
  { key: "experiment", label: "Experiment", icon: "experiment", steps: ["experimenter"], brief: "B", blurb: "The experimenter compares 2-3 tests, picks the most informative one and writes the analysis code." },
  { key: "approval", label: "Approval", icon: "approval", steps: ["approve"], blurb: "The scientist approves, changes or rejects the experiment before it is reviewed." },
  { key: "review", label: "Review", icon: "review", steps: ["skeptics"], brief: "C", blurb: "Independent skeptics audit the numbers, the code, the controls and the claims." },
  { key: "decision", label: "Decision", icon: "decision", steps: ["judge", "verdict"], brief: "C", blurb: "The judge updates every hypothesis and names the next experiment." },
];

const LIVE = ["starting", "running", "awaiting_approval"];
const isLive = (s?: string) => s === "thinking" || s === "dispatched" || s === "waiting";
const isRunLive = (v: View) => LIVE.includes(v.run.status);

function loopItems(v: View) {
  const ids = new Set(v.run.steps.map(s => s.id));
  return LOOP.filter(i => i.steps.some(s => ids.has(s)));
}

function loopState(v: View, item: LoopItem): string {
  const steps = v.run.steps.filter(s => item.steps.includes(s.id));
  if (!steps.length) return "pending";
  if (steps.some(s => s.status === "rejected")) return "error";
  if (steps.every(s => s.status === "skipped")) return "skipped";
  if (steps.every(s => s.status === "done" || s.status === "skipped")) return "done";
  if (steps.some(s => s.status === "running")) {
    if (v.run.status === "failed") return "error";
    return item.key === "approval" ? "human" : "active";
  }
  if (steps.some(s => s.status === "done") && isRunLive(v)) return "active";
  return "pending";
}

function stepAgents(v: View, steps: string[]) {
  return Object.keys(v.agents).filter(l => steps.includes(v.agents[l].step) && !(l === "pi" && !steps.includes("plan")))
    .sort((a, b) => order(a) - order(b) || a.localeCompare(b, undefined, { numeric: true }));
}
const ROLE_ORDER = ["pi", "scout", "theorist", "lead", "experimenter", "human", "skeptic", "judge"];
const order = (label: string) => ROLE_ORDER.indexOf(label.split("-")[0]);

function loopSub(v: View, item: LoopItem): string {
  const sci = v.science;
  const agents = stepAgents(v, item.steps);
  const done = agents.filter(l => v.agents[l].status === "done").length;
  const state = loopState(v, item);
  switch (item.key) {
    case "question": return state === "done" ? "plan written" : state === "active" ? "PI is planning" : "PI";
    case "evidence": return state === "active" ? `${done}/${agents.length} scouts done` : plural(agents.length, "scout");
    case "hypotheses": return sci.candidates.length ? plural(sci.candidates.length, "hypothesis", "hypotheses") : `${agents.length - 1} theorists + lead`;
    case "experiment": return sci.selection?.tests.length ? `${sci.selection.tests.length} tests compared` : "design + code";
    case "approval": {
      if (state === "skipped") return "not required";
      const a = sci.approval;
      if (!a) return state === "human" ? "waiting for you" : "scientist";
      return a.decision === "approve" ? (a.changed ? "test changed" : "approved") : "rejected";
    }
    case "review": {
      const n = sci.reviews.filter(r => r.verdict).length;
      return state === "active" ? `${done}/${agents.length} audits done` : n ? `${n} audits` : plural(agents.length, "skeptic");
    }
    case "decision": return sci.decision?.status ?? (state === "done" ? "verdict written" : "judge");
  }
  return "";
}

function loopHtml(v: View | null) {
  const items = v ? loopItems(v) : LOOP;
  const li = items.map((it, i) => {
    const st = v ? loopState(v, it) : (it.key === "approval" ? "human" : "");
    const node = st === "done" ? icon("check", 18, 2.4) : icon(it.icon, 18);
    return `<li class="loop-item ${st}">
      <span class="loop-node" title="${esc(it.blurb)}">${node}</span>
      <div><div class="loop-label">${i + 1}. ${esc(it.label)}</div>
        <div class="loop-sub">${esc(v ? loopSub(v, it) : it.blurb)}</div></div>
    </li>`;
  }).join("");
  return `<ol class="loop ${v ? "" : "demo vertical"}" style="--n:${items.length}">${li}</ol>`;
}

// ---------------------------------------------------------------- state

let config: Config;
let current: View | null = null;
let currentId: string | null = null;
let currentTab = "overview";
let drawerSel: string | null = null;
let runTimer: number | undefined;
let homeTimer: number | undefined;
const memo = new Map<string, string>();
const fileCache = new Map<string, string>();

/** Re-render a region only when its inputs changed, so polling never resets scroll, focus or form input. */
function paint(sel: string, key: unknown, html: () => string) {
  const k = JSON.stringify(key);
  if (memo.get(sel) === k) return false;
  memo.set(sel, k);
  const el = $(sel);
  el.innerHTML = html();
  hydrate(el);
  return true;
}

// ---------------------------------------------------------------- home

const STANDARDS = [
  { icon: "coins", title: "Agent swarms, cheaper loops", text: "Each stage launches an agent swarm that reads one cached brief, so every extra agent costs a fraction of a full call.",
    points: ["Measured savings vs. the same calls without cache", "Parallel speedup per swarm"] },
  { icon: "users", title: "Specialist agents on Omnigent", text: "Every role is its own Omnigent agent with a defined decision, inputs, output and tool permissions.",
    points: ["PI, scouts, theorists, experimenter, skeptics, judge", "Structured handoffs through shared briefs"] },
  { icon: "hypotheses", title: "Competing hypotheses, a decisive test", text: "The team states hypotheses with priors, compares tests and picks the one that separates them best.",
    points: ["Always includes \"none of these is right\"", "Predictions written before the test"] },
  { icon: "review", title: "Rigor built in", text: "Independent skeptics audit numbers, code, controls and overclaiming before any verdict.",
    points: ["Posterior for every hypothesis", "Next experiment and validation needed"] },
  { icon: "approval", title: "Scientist in control", text: "You set the question, the budget and the team, and approve the experiment before it is reviewed.",
    points: ["Agents have no web, code or file tools", "Stop any run at any time"] },
];

function renderHomeStatic() {
  $("#page-home").innerHTML = `
    <div class="container">
      <div class="hero">
        <div>
          <p class="eyebrow">Agentic scientific discovery</p>
          <h1>Ask a research question. <em>A team of AI scientists</em> runs the discovery loop.</h1>
          <p class="hero-lead">Cachew Studio turns one question into evidence, competing hypotheses, a chosen experiment,
            independent review and a decision about what to investigate next. Every agent is an Omnigent session, every
            step is recorded, and you approve the experiment.</p>
          <div id="home-savings"></div>
          <div class="hero-actions">
            <a class="btn primary lg" href="#/new">${icon("sparkle", 18)} Start new research</a>
            <a class="btn outline lg" href="#runs">Browse runs</a>
          </div>
          <div class="hero-points">
            <span>${icon("check", 14, 2.4)} Question → evidence → hypothesis → experiment → result → decision</span>
            <span>${icon("check", 14, 2.4)} Agent swarms on a shared prompt cache</span>
          </div>
        </div>
        <div class="card hero-visual" aria-label="The discovery loop">
          <p class="eyebrow" style="margin-bottom:18px">One discovery loop</p>
          ${loopHtml(null)}
          <div class="loop-foot">${icon("loop", 15)} The decision names the next experiment, which starts the next loop.</div>
        </div>
      </div>
      <h2 class="section-title">What every run delivers</h2>
      <div class="grid grid-auto">
        ${STANDARDS.map(s => `<article class="card standard">
          <span class="ico">${icon(s.icon, 20)}</span>
          <h3>${esc(s.title)}</h3><p>${esc(s.text)}</p>
          <ul>${s.points.map(p => `<li>${icon("check", 13, 2.4)}<span>${esc(p)}</span></li>`).join("")}</ul>
        </article>`).join("")}
      </div>
      <h2 class="section-title" id="runs">Research runs</h2>
      <div id="home-runs" class="card run-list-card"></div>
      <div style="height:48px"></div>
    </div>`;
}

const STATUS: Record<string, [string, string]> = {
  starting: ["accent", "Starting"], running: ["accent", "Running"], awaiting_approval: ["human", "Needs approval"],
  done: ["good", "Done"], stopped: ["warn", "Stopped"], budget: ["warn", "Budget reached"], failed: ["bad", "Failed"],
};
function statusBadge(s: string) {
  const [cls, label] = STATUS[s] ?? ["", s];
  const dot = s === "running" || s === "starting" ? "live" : s === "awaiting_approval" ? "human" : "";
  return `<span class="badge ${cls}">${dot ? `<i class="dot ${dot}"></i>` : ""}${esc(label)}</span>`;
}

async function loadRuns() {
  let rows: RunRow[];
  try { rows = await api<RunRow[]>("/api/runs"); } catch (e) {
    paint("#home-runs", ["err", (e as Error).message], () =>
      `<div class="empty"><span class="ico">${icon("alert", 20)}</span><h3>Could not load runs</h3><p>${esc((e as Error).message)}</p></div>`);
    return;
  }
  const spent = rows.reduce((t, r) => t + (r.usd ?? 0), 0), saved = rows.reduce((t, r) => t + Math.max(0, r.saved_usd ?? 0), 0);
  paint("#home-savings", ["hs", spent, saved, rows.length], () => saved > 0 ? `<div class="home-savings">${icon("coins", 18)}
      <span>Agent swarms made ${plural(rows.length, "run")} <b>${Math.round(100 * saved / (spent + saved))}% cheaper</b>: ${usd(spent, 2)} spent instead of ${usd(spent + saved, 2)}.</span></div>` : "");
  paint("#home-runs", rows, () => !rows.length
    ? `<div class="empty"><span class="ico">${icon("record", 20)}</span><h3>No research yet</h3>
        <p>Start a run to see the team plan, gather evidence, test hypotheses and reach a decision.</p>
        <p style="margin-top:16px"><a class="btn primary" href="#/new">Start new research</a></p></div>`
    : `<ul class="run-list">${rows.map(r => `<li><a class="run-row" href="#/run/${esc(r.id)}">
        <span class="q clamp-2" title="${esc(r.question)}">${esc(headline(r.question, 140))}</span>${statusBadge(r.status)}
        <span class="run-meta"><span>${esc(shortModel(r.model ?? ""))}</span>${r.agents ? `<span>${r.agents} agents</span>` : ""}
          <span>${usd(r.usd)} spent</span>${r.saved_usd > 0 ? `<span class="saved">saved ${usd(r.saved_usd)}</span>` : ""}
          ${r.created ? `<span>${esc(ago(r.created))}</span>` : ""}</span>
      </a></li>`).join("")}</ul>`);
}

// ---------------------------------------------------------------- new research

type KeyMode = "shared" | "own";
const form = { mode: "shared" as KeyMode, built: false };
const KEY_STORE = "studio-api-key";

function tier(): Tier { return form.mode === "own" ? config.tiers.own : config.tiers.shared; }
function modelById(id: string) { return config.models.find(m => m.id === id)!; }

function renderNewStatic() {
  const sharedOk = config.shared_key;
  const t = config.tiers.shared;
  $("#page-new").innerHTML = `
    <div class="container page-pad">
      <nav class="crumbs"><a href="#/">${icon("back", 14)} Runs</a></nav>
      <div style="margin-bottom:22px">
        <h1 style="font-size:clamp(22px,3vw,30px);letter-spacing:-.02em">New research</h1>
        <p class="muted" style="margin-top:6px;max-width:680px">State one question the team can meaningfully investigate.
          The PI plans it, the team runs the full discovery loop, and you approve the experiment before it is reviewed.</p>
      </div>
      <form id="new-run" class="new-layout" novalidate>
        <div>
          <section class="card form-section">
            <div class="form-section-head"><span class="n">1</span><div><h2>Research question</h2>
              <p>What should the team find out, and what would a good answer look like?</p></div></div>
            <div class="field">
              <label class="field-label" for="f-question">Question</label>
              <textarea class="textarea" id="f-question" name="question" rows="4" required minlength="10" maxlength="4000"
                placeholder="e.g. Does a 10-minute daily walk measurably improve sleep quality in office workers, and by how much?"></textarea>
            </div>
            <div class="field">
              <label class="field-label" for="f-context">Context <span class="opt">optional · shared with every agent</span></label>
              <textarea class="textarea" id="f-context" name="context" rows="3"
                placeholder="Background notes, data, constraints or papers the team should build on."></textarea>
            </div>
            <div class="field">
              <label class="dropzone" id="dropzone">
                <input type="file" id="f-files" multiple accept=".txt,.md,.csv,.json,.tsv,.py,.tex">
                ${icon("upload", 18)}
                <span class="dropzone-text"><b>Attach text files</b> <span class="muted">or drop them here · .txt .md .csv .json .tsv .py .tex, up to 2 MB each</span>
                  <span id="files-note" class="hint" style="display:block"></span></span>
              </label>
            </div>
          </section>

          <section class="card form-section">
            <div class="form-section-head"><span class="n">2</span><div><h2>API key and model</h2>
              <p>Your own Anthropic key unlocks every model, higher effort and teams of any size.</p></div></div>
            <div class="segmented" role="radiogroup" aria-label="Which API key to use">
              <label><input type="radio" name="keymode" value="shared" ${sharedOk ? "" : "disabled"}>
                <b>Studio key</b><span>${sharedOk ? `Sonnet (low effort) or Haiku · agent swarms of up to ${t.max_width}` : "Not configured on this server"}</span></label>
              <label><input type="radio" name="keymode" value="own">
                <b>My Anthropic key</b><span>All models and efforts · unlimited agents</span></label>
            </div>
            <div class="key-box" id="key-box" hidden>
              <label class="field-label" for="f-key" style="margin-bottom:6px">Anthropic API key</label>
              <div class="key-row">
                <input class="input" id="f-key" type="password" autocomplete="off" spellcheck="false" placeholder="sk-ant-…" maxlength="400">
                <button type="button" class="btn outline" id="f-key-show" aria-label="Show key">${icon("eye", 16)}</button>
              </div>
              <div class="key-foot">
                <label class="check"><input type="checkbox" id="f-key-remember"> Remember on this device</label>
                <span class="hint">${icon("lock", 12)} Checked with Anthropic before the run starts</span>
              </div>
              <p class="hint" style="margin-top:8px">The key is sent only to this Studio server and to Anthropic. The run keeps it in an
                owner-only file inside its study folder and deletes it when the run ends; it never appears in the research record.
                Calls are billed to your Anthropic account.</p>
            </div>
            <div class="field-row" style="margin-top:18px">
              <div class="field"><label class="field-label" for="f-model">Model</label><select class="select" id="f-model" name="model"></select></div>
              <div class="field"><label class="field-label" for="f-effort">Reasoning effort</label><select class="select" id="f-effort" name="effort"></select></div>
            </div>
            <div id="tier-note"></div>
          </section>

          <section class="card form-section">
            <div class="form-section-head"><span class="n">3</span><div><h2>Team and oversight</h2>
              <p>How large each agent swarm is, how much the run may spend, and where it waits for you.</p></div></div>
            <div class="field-row">
              <div class="field">
                <label class="field-label" for="f-width">Agents per swarm <span class="opt" id="width-cap"></span></label>
                <div class="stepper">
                  <button type="button" data-step="-1" aria-label="Fewer agents">−</button>
                  <input id="f-width" name="width" type="number" inputmode="numeric" min="1" value="3">
                  <button type="button" data-step="1" aria-label="More agents">+</button>
                </div>
                <span class="hint">Scouts, theorists and skeptics each run as a swarm of this many agents. Swarm members share one cached brief, so extra agents cost little.</span>
              </div>
              <div class="field">
                <label class="field-label" for="f-budget">Budget cap (USD) <span class="opt" id="budget-cap"></span></label>
                <input class="input" id="f-budget" name="budget_usd" type="number" inputmode="decimal" min="0.1" step="0.1" value="2">
                <span class="hint">No new stage starts once the run has spent this much.</span>
              </div>
            </div>
            <div class="field" style="margin-top:20px">
              <label class="switch">
                <input type="checkbox" id="f-approval" checked>
                <span class="track"></span>
                <span class="switch-text"><b>Wait for my approval before review</b>
                  <span class="hint">The run pauses after the experimenter picks a test. You approve it, choose another test, or reject it.
                  Without a decision within ${Math.round(config.approval_timeout_s / 60)} minutes the run stops.</span></span>
              </label>
            </div>
          </section>
        </div>

        <aside class="card summary" aria-label="Run summary">
          <h2>Run summary</h2>
          <div class="summary-big" id="sum-agents">—</div>
          <p class="muted small" id="sum-agents-sub">Omnigent agents</p>
          <div class="summary-rows">
            <div><span>Model</span><span id="sum-model">—</span></div>
            <div><span>Key</span><span id="sum-key">—</span></div>
            <div><span>Agent swarms</span><span id="sum-fan">—</span></div>
            <div><span>Estimated cost</span><span id="sum-cost">—</span></div>
            <div><span>Budget cap</span><span id="sum-budget">—</span></div>
            <div><span>Your approval</span><span id="sum-approval">—</span></div>
          </div>
          <p class="hint" id="sum-price"></p>
          <div id="sum-warn"></div>
          <button type="submit" class="btn primary block lg" id="f-start" style="margin-top:16px">Start research</button>
          <div id="f-error" class="form-error" role="alert" hidden></div>
        </aside>
      </form>
    </div>`;
  wireForm();
}

function wireForm() {
  const f = $<HTMLFormElement>("#new-run");
  const key = $<HTMLInputElement>("#f-key");
  const remember = $<HTMLInputElement>("#f-key-remember");
  const savedKey = store.get("local", KEY_STORE) ?? store.get("session", KEY_STORE);
  if (savedKey) key.value = savedKey;
  remember.checked = !!store.get("local", KEY_STORE);
  form.mode = !config.shared_key || savedKey ? "own" : "shared";
  f.querySelectorAll<HTMLInputElement>('input[name="keymode"]').forEach(r => {
    r.checked = r.value === form.mode;
    r.addEventListener("change", () => { form.mode = r.value as KeyMode; syncForm(); });
  });
  key.addEventListener("input", () => { saveKey(); syncForm(); });
  remember.addEventListener("change", saveKey);
  $("#f-key-show").addEventListener("click", () => {
    const show = key.type === "password";
    key.type = show ? "text" : "password";
    $("#f-key-show").innerHTML = icon(show ? "eyeOff" : "eye", 16);
    $("#f-key-show").setAttribute("aria-label", show ? "Hide key" : "Show key");
  });
  $("#f-model").addEventListener("change", syncForm);
  $("#f-effort").addEventListener("change", syncForm);
  $("#f-width").addEventListener("input", syncForm);
  $("#f-width").addEventListener("blur", () => { clampWidth(); syncForm(); });
  $("#f-budget").addEventListener("input", syncForm);
  $("#f-approval").addEventListener("change", syncForm);
  f.querySelectorAll<HTMLButtonElement>("[data-step]").forEach(b => b.addEventListener("click", () => {
    const w = $<HTMLInputElement>("#f-width");
    w.value = String(Math.max(1, (parseInt(w.value, 10) || 1) + Number(b.dataset.step)));
    clampWidth();
    syncForm();
  }));

  const files = $<HTMLInputElement>("#f-files");
  const zone = $("#dropzone");
  const noteFiles = () => {
    const list = Array.from(files.files ?? []);
    $("#files-note").textContent = list.length
      ? `${plural(list.length, "file")} attached (${list.map(x => x.name).join(", ")}) · about ${ktok(list.reduce((a, x) => a + x.size, 0) / 4)} tokens`
      : "";
  };
  files.addEventListener("change", noteFiles);
  zone.addEventListener("dragover", e => { e.preventDefault(); zone.classList.add("drag"); });
  zone.addEventListener("dragleave", () => zone.classList.remove("drag"));
  zone.addEventListener("drop", e => {
    e.preventDefault();
    zone.classList.remove("drag");
    if (e.dataTransfer?.files.length) { files.files = e.dataTransfer.files; noteFiles(); }
  });
  f.addEventListener("submit", submitForm);
  syncForm();
}

function saveKey() {
  const k = $<HTMLInputElement>("#f-key").value.trim();
  const remember = $<HTMLInputElement>("#f-key-remember").checked;
  store.set("local", KEY_STORE, remember && k ? k : null);
  store.set("session", KEY_STORE, k || null);
}

function clampWidth() {
  const w = $<HTMLInputElement>("#f-width");
  const max = tier().max_width;
  let n = parseInt(w.value, 10);
  if (!isFinite(n) || n < 1) n = 1;
  if (max && n > max) n = max;
  w.value = String(n);
}

function estimate(m: Model, width: number) {
  // Rough per-call shape from measured runs: ~3k-token cached brief, ~400-token task, ~1.4k-token answer.
  const members = 3 * width + 3, brief = 3000, task = 400, out = 1400;
  const usdMid = (members * (brief * m.cache_read + task * m.input + out * m.output) + 3 * brief * m.cache_write
    + 600 * m.input + 900 * m.output) / 1e6;
  return { agents: members + 1, low: usdMid * 0.6, high: usdMid * 1.8 };
}

function syncForm() {
  const t = tier();
  const own = form.mode === "own";
  $("#key-box").hidden = !own;
  const modelSel = $<HTMLSelectElement>("#f-model");
  const prev = modelSel.value || "claude-sonnet-5-5";
  modelSel.innerHTML = config.models.map(m => {
    const ok = m.id in t.models;
    return `<option value="${esc(m.id)}" ${ok ? "" : "disabled"}>${esc(m.name)}${ok ? "" : " · needs your key"}</option>`;
  }).join("");
  modelSel.value = prev in t.models ? prev : (Object.keys(t.models).find(id => id.includes("sonnet")) ?? Object.keys(t.models)[0]);
  const m = modelById(modelSel.value);
  const effSel = $<HTMLSelectElement>("#f-effort");
  const allowed = t.models[m.id] ?? [];
  const prevEff = effSel.value || "low";
  if (allowed.length === 1 && allowed[0] === "") {
    effSel.innerHTML = `<option value="">Not used by this model</option>`;
    effSel.disabled = true;
  } else {
    effSel.disabled = false;
    effSel.innerHTML = config.efforts.map(e => `<option value="${e}" ${allowed.includes(e) ? "" : "disabled"}>${e[0].toUpperCase() + e.slice(1)}${allowed.includes(e) ? "" : " · needs your key"}</option>`).join("");
    effSel.value = allowed.includes(prevEff) ? prevEff : allowed[0];
  }

  const w = $<HTMLInputElement>("#f-width");
  if (t.max_width) w.max = String(t.max_width); else w.removeAttribute("max");
  if (t.max_width && parseInt(w.value, 10) > t.max_width) w.value = String(t.max_width);
  const width = Math.max(1, parseInt(w.value, 10) || 1);
  $("#width-cap").textContent = t.max_width ? `max ${t.max_width}` : "no limit";
  ($<HTMLButtonElement>('[data-step="1"]')).disabled = !!t.max_width && width >= t.max_width;
  ($<HTMLButtonElement>('[data-step="-1"]')).disabled = width <= 1;
  const budget = $<HTMLInputElement>("#f-budget");
  budget.max = String(t.max_budget_usd);
  $("#budget-cap").textContent = `max $${t.max_budget_usd}`;

  $("#tier-note").innerHTML = own ? "" : `<div class="tier-note">${icon("info", 16)}<span>On the Studio key you can use
    Claude Sonnet at low effort or Claude Haiku, agent swarms of up to ${config.tiers.shared.max_width} and $${config.tiers.shared.max_budget_usd}
    per run. Add your own Anthropic key for Opus, Fable, higher effort and teams of any size.</span></div>`;

  const est = estimate(m, width);
  const budgetUsd = Number(budget.value) || 0;
  $("#sum-agents").textContent = num(est.agents);
  $("#sum-agents-sub").textContent = `Omnigent agents · 1 PI, ${3 * width} in 3 swarms, 3 single specialists`;
  $("#sum-model").textContent = `${m.name}${effSel.disabled ? "" : ` · ${effSel.value}`}`;
  $("#sum-key").textContent = own ? "Your Anthropic key" : "Studio key";
  $("#sum-fan").textContent = `3 × ${width} in parallel`;
  $("#sum-cost").textContent = `${usd(est.low, 2)} – ${usd(est.high, 2)}`;
  $("#sum-budget").textContent = budgetUsd ? usd(budgetUsd, 2) : "—";
  $("#sum-approval").textContent = $<HTMLInputElement>("#f-approval").checked ? "Before review" : "Not required";
  $("#sum-price").textContent = `${m.name}: $${m.input}/MTok input, $${m.output}/MTok output, cache read $${m.cache_read}, cache write $${m.cache_write}. The estimate is rough; the budget cap is enforced.`;
  const warns: string[] = [];
  if (budgetUsd && est.low > budgetUsd) warns.push("The estimate is above your budget cap: the run may stop before the verdict.");
  if (width > 12) warns.push(`${num(3 * width)} swarm agents start in waves of ${width}. Your Anthropic rate limits apply.`);
  if (own && !$<HTMLInputElement>("#f-key").value.trim()) warns.push("Paste your Anthropic API key above.");
  $("#sum-warn").innerHTML = warns.map(w => `<p class="form-warn">${icon("alert", 14)}<span>${esc(w)}</span></p>`).join("");
}

async function readFiles(input: HTMLInputElement): Promise<string> {
  const parts: string[] = [];
  for (const f of Array.from(input.files ?? [])) {
    if (f.size > 2_000_000) throw new Error(`${f.name} is larger than 2 MB`);
    parts.push(`### ${f.name}\n\n${await f.text()}`);
  }
  return parts.join("\n\n");
}

async function submitForm(ev: Event) {
  ev.preventDefault();
  const err = $("#f-error");
  err.hidden = true;
  const question = $<HTMLTextAreaElement>("#f-question").value.trim();
  const own = form.mode === "own";
  const key = $<HTMLInputElement>("#f-key").value.trim();
  const problem = question.length < 10 ? "Write a research question of at least 10 characters."
    : own && !key ? "Paste your Anthropic API key, or switch to the Studio key." : "";
  if (problem) {
    err.textContent = problem;
    err.hidden = false;
    (question.length < 10 ? $("#f-question") : $("#f-key")).focus();
    return;
  }
  clampWidth();
  const btn = $<HTMLButtonElement>("#f-start");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> ${own ? "Checking key and starting…" : "Starting Omnigent…"}`;
  try {
    const attached = await readFiles($<HTMLInputElement>("#f-files"));
    const context = [$<HTMLTextAreaElement>("#f-context").value.trim(), attached].filter(Boolean).join("\n\n");
    const model = $<HTMLSelectElement>("#f-model").value;
    const effSel = $<HTMLSelectElement>("#f-effort");
    const body: Record<string, unknown> = {
      question, context, model, effort: effSel.disabled ? "" : effSel.value,
      width: parseInt($<HTMLInputElement>("#f-width").value, 10), budget_usd: Number($<HTMLInputElement>("#f-budget").value),
      approval: $<HTMLInputElement>("#f-approval").checked,
    };
    if (own) body.api_key = key;
    const { id } = await api<{ id: string }>("/api/runs", {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
    });
    location.hash = `#/run/${id}`;
    toast("Research started");
  } catch (e) {
    err.textContent = (e as Error).message;
    err.hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Start research";
  }
}

// ---------------------------------------------------------------- run page

function tabsFor(v: View) {
  const s = v.science;
  return [
    { id: "overview", label: "Overview" },
    { id: "team", label: "Team", count: Object.keys(v.agents).length },
    { id: "hypotheses", label: "Hypotheses", count: s.candidates.length || undefined },
    { id: "experiment", label: "Experiment", dot: v.run.status === "awaiting_approval" ? "human" : "" },
    { id: "review", label: "Review", count: s.reviews.filter(r => r.verdict).length || undefined },
    { id: "acceleration", label: "Acceleration" },
    { id: "record", label: "Research record", count: v.files.length + v.bundle.length },
  ];
}

function renderRun(v: View) {
  renderHead(v);
  renderAlert(v);
  paint("#run-savings", ["sav", v.id, v.savings, v.acceleration.speedup, v.acceleration.cost_ratio, Object.keys(v.agents).length], () => savingsBanner(v));
  paint("#run-loop", ["loop", v.run.steps, v.science.candidates.length, v.science.selection?.tests.length, v.science.approval,
    v.science.reviews, v.science.decision?.status, Object.values(v.agents).map(a => a.status), v.run.status], () =>
    `${loopHtml(v)}<div class="loop-foot">${icon("loop", 15)} ${esc(loopFoot(v))}</div>`);
  paint("#run-tabs", ["tabs", currentTab, tabsFor(v)], () => tabsFor(v).map(t => `<a role="tab" class="tab" href="#/run/${esc(v.id)}/${t.id}"
      aria-selected="${t.id === currentTab}" aria-controls="tab-${t.id}">${esc(t.label)}${t.count != null ? `<span class="count">${t.count}</span>` : ""}${t.dot ? `<i class="dot ${t.dot}"></i>` : ""}</a>`).join(""));
  for (const t of tabsFor(v)) $(`#tab-${t.id}`).hidden = t.id !== currentTab;
  const renderers: Record<string, (v: View) => void> = {
    overview: renderOverview, team: renderTeam, hypotheses: renderHypotheses, experiment: renderExperiment,
    review: renderReview, acceleration: renderAcceleration, record: renderRecord,
  };
  (renderers[currentTab] ?? renderOverview)(v);
  if (drawerSel) renderDrawer(v);
  tickElapsed();
}

/** The headline result of every run: what the agent swarms saved by sharing one cached brief. */
function savingsBanner(v: View) {
  const s = v.savings, a = v.acceleration;
  const swarm = Object.values(v.agents).filter(x => ["scout", "theorist", "skeptic"].includes(x.role)).length;
  const saved = s.saved_usd > 0;
  const max = Math.max(s.usd_uncached, s.usd, 1e-9);
  const bar = (label: string, n: number, cls: string) =>
    `<div class="bar-line"><span>${label}</span><span class="meter ${cls}"><i style="width:${(100 * n / max).toFixed(1)}%"></i></span><b>${usd(n)}</b></div>`;
  return `<section class="savings-banner ${saved ? "" : "pending"}" aria-label="Agent swarm savings">
    <div class="sb-main">
      <span class="sb-ico">${icon("coins", 22)}</span>
      <div style="min-width:0">
        <p class="eyebrow">Agent swarm savings</p>
        <div class="sb-value">${saved ? `${s.saved_pct.toFixed(0)}% cheaper` : "Measuring…"}</div>
        <p class="sb-sub">${saved
          ? `Saved <b>${usd(s.saved_usd)}</b>: this loop cost ${usd(s.usd)} instead of ${usd(s.usd_uncached)}, because every swarm member read one shared brief from the prompt cache.`
          : "Savings appear as soon as the first agent swarm reads its cached brief."}</p>
      </div>
    </div>
    <div class="sb-side">
      <div class="bars">${bar("Without cache", s.usd_uncached, "muted")}${bar("Agent swarms", s.usd, "good")}</div>
      <div class="sb-stats">
        <span><b>${swarm}</b> agents in 3 swarms</span>
        <span><b>${ktok(s.tokens.cache_read_input_tokens ?? 0)}</b> tok from cache</span>
        <span><b>${times(a.speedup)}</b> parallel speedup</span>
        <a href="#/run/${esc(v.id)}/acceleration">Breakdown →</a>
      </div>
    </div>
  </section>`;
}

function loopFoot(v: View) {
  const next = v.science.decision?.next_experiment;
  if (next) {
    const line = next.split("\n")[0].replace(/[*_`#]/g, "").replace(/[:;,\s]+$/, "").trim();
    return `Next loop: ${line.length > 160 ? `${line.slice(0, line.lastIndexOf(" ", 160))}…` : line}`;
  }
  return "The decision names the next experiment, which starts the next loop.";
}

/** A readable headline from a long research prompt: its first sentence, cut at a word boundary. */
function headline(q: string, max = 110) {
  const flat = q.replace(/\s+/g, " ").trim();
  const first = flat.match(/^.{20,}?[.?!](?=\s|$)/)?.[0] ?? flat;
  if (first.length <= max) return first;
  const cut = first.slice(0, max);
  return `${cut.slice(0, Math.max(cut.lastIndexOf(" "), max - 20)).replace(/[,;:\s]+$/, "")}…`;
}

function renderHead(v: View) {
  const r = v.run;
  const live = isRunLive(v);
  const end = r.finished ?? (v.acceleration.run_s ? r.created + v.acceleration.run_s : v.now);
  const budgetFrac = r.budget_usd ? Math.min(1, v.spent_usd / r.budget_usd) : 0;
  paint("#run-head", ["head", v.id, r.question, r.status, r.phase, r.model, r.effort, r.key, Math.round(v.spent_usd * 1000), r.pi_session, live ? 0 : end],
    () => `
    <nav class="crumbs"><a href="#/">${icon("back", 14)} Runs</a><span aria-hidden="true">/</span><span class="ellipsis mono">${esc(v.id)}</span></nav>
    <div class="run-head">
      <div class="run-head-main">
        <h1 title="${esc(r.question)}">${esc(headline(r.question))}</h1>
        ${headline(r.question) !== r.question.replace(/\s+/g, " ").trim() ? `<details class="full-q"><summary>Full question</summary>
          <p class="wrap-any">${esc(r.question)}</p></details>` : ""}
        <div class="chips">
          ${statusBadge(r.status)}
          <span class="chip">${icon("cpu", 14)}<b>${esc(shortModel(r.model))}</b>${r.effort ? ` · ${esc(r.effort)}` : ""}</span>
          <span class="chip">${icon("users", 14)}<b>${Object.keys(v.agents).length}</b> agents</span>
          <span class="chip">${icon("key", 14)}${r.key === "own" ? "Own key" : "Studio key"}</span>
          <span class="chip">${icon("clock", 14)}${live ? `<b data-since="${r.created}"></b>` : `<b>${dur(end - r.created)}</b>`}</span>
          <span class="chip budget-chip" title="Spent of the budget cap">${icon("coins", 14)}<b>${usd(v.spent_usd)}</b> of ${usd(r.budget_usd, 2)}
            <span class="meter ${budgetFrac > .9 ? "bad" : budgetFrac > .7 ? "warn" : ""}"><i style="width:${(100 * budgetFrac).toFixed(1)}%"></i></span></span>
        </div>
        ${live ? `<p class="phase-line"><i class="dot ${r.status === "awaiting_approval" ? "human" : "live"}"></i><span class="ellipsis">${esc(r.phase)}</span></p>` : ""}
      </div>
      <div class="run-head-actions">
        ${r.pi_session ? `<a class="btn outline sm" href="${esc(config.omnigent_url)}/c/${esc(r.pi_session)}" target="_blank" rel="noopener">${icon("external", 14)} Omnigent session</a>` : ""}
        ${live ? `<button class="btn danger-ghost sm" type="button" id="stop-run">${icon("stop", 14)} Stop</button>` : ""}
      </div>
    </div>`);
}

function renderAlert(v: View) {
  const r = v.run;
  if (r.status === "awaiting_approval") {
    paint("#run-alert", ["approval", v.id, r.status, v.science.selection], () => approvalForm(v));
    return;
  }
  paint("#run-alert", ["alert", r.status, r.phase], () => {
    if (r.status === "failed") return callout("bad", "alert", "The run failed", r.phase || r.error || "Unknown error");
    if (r.status === "budget") return callout("warn", "coins", "Budget reached", `${r.phase}. Everything the team produced so far is in the research record.`);
    if (r.status === "stopped") return callout("warn", "stop", "Run stopped", `${r.phase}. Everything the team produced so far is in the research record.`);
    return "";
  });
}

function callout(kind: string, ico: string, title: string, text: string) {
  return `<div class="callout ${kind}"><span class="ico">${icon(ico, 18)}</span><div class="callout-body"><h3>${esc(title)}</h3><p class="wrap-any">${esc(text)}</p></div></div>`;
}

function approvalForm(v: View) {
  const sel = v.science.selection;
  const tests = sel?.tests ?? [];
  const chosen = sel && sel.chosen_index >= 0 ? sel.chosen_index : 0;
  return `<form class="card approval" id="approval-form">
    <div class="card-head"><div class="card-title"><span class="ico">${icon("approval", 18)}</span><div>
      <h2>Your approval is needed</h2>
      <p>The team paused before review. Choose the experiment the skeptics should audit, or reject it to stop the run.
        The run waits up to ${Math.round(config.approval_timeout_s / 60)} minutes.</p></div></div>
      <button type="button" class="btn outline sm" data-open="experiment/design.md">${icon("file", 14)} Read the design</button>
    </div>
    ${tests.length ? `<div class="choice-list" role="radiogroup" aria-label="Experiment to approve">${tests.map((t, i) => `
      <label class="choice"><input type="radio" name="test" value="${esc(t)}" ${i === chosen ? "checked" : ""}>
        <span class="choice-text">${esc(t)}${i === chosen ? ` <span class="badge sm accent">Experimenter's pick</span>` : ""}</span></label>`).join("")}</div>
      ${sel?.why ? `<div class="why"><b>Why the experimenter picked it:</b> ${esc(sel.why)}</div>` : ""}`
    : `<p class="muted">The experimenter did not list its tests in a structured form. Read the design, then approve or reject it as written.</p>`}
    <div class="field" style="margin-top:16px">
      <label class="field-label" for="approval-note">Note to the team <span class="opt">optional</span></label>
      <textarea class="textarea" id="approval-note" rows="2" maxlength="2000" placeholder="Constraints, changes or context the skeptics should take into account."></textarea>
    </div>
    <div class="approval-actions">
      <button type="button" class="btn danger-ghost" data-approval="reject">Reject and stop</button>
      <button type="submit" class="btn good" data-approval="approve">${icon("check", 16, 2.4)} Approve experiment</button>
    </div>
  </form>`;
}

async function sendApproval(decision: "approve" | "reject") {
  if (!current) return;
  const formEl = document.getElementById("approval-form") as HTMLFormElement | null;
  if (!formEl) return;
  if (decision === "reject" && !(await confirmDialog("Reject the experiment?", "The run stops and nothing more is dispatched. Everything produced so far stays in the research record.", "Reject and stop"))) return;
  const picked = formEl.querySelector<HTMLInputElement>('input[name="test"]:checked')?.value ?? "";
  const note = formEl.querySelector<HTMLTextAreaElement>("#approval-note")?.value ?? "";
  formEl.querySelectorAll<HTMLButtonElement>("button").forEach(b => { b.disabled = true; });
  try {
    await api(`/api/runs/${encodeURIComponent(current.id)}/approval`, {
      method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ decision, test: picked, note }),
    });
    toast(decision === "approve" ? "Approved. The skeptics start now." : "Experiment rejected. The run stops.");
    pollRun();
  } catch (e) {
    toast((e as Error).message, true);
    formEl.querySelectorAll<HTMLButtonElement>("button").forEach(b => { b.disabled = false; });
  }
}

// ---------------------------------------------------------------- overview

function decisionCard(v: View, full = true) {
  const d = v.science.decision;
  const r = v.run;
  if (d && d.answer) {
    const status = d.status ?? "";
    const cls = status === "supported" ? "good" : status === "refuted" ? "bad" : status ? "warn" : "";
    return `<section class="card decision-card">
      <div class="card-head" style="margin-bottom:0"><div class="card-title"><span class="ico">${icon("decision", 18)}</span><h2>Decision</h2></div>
        ${status ? `<span class="badge ${cls}">${esc(status[0].toUpperCase() + status.slice(1))}</span>` : ""}</div>
      <p class="decision-answer">${esc(d.answer)}</p>
      ${d.next_experiment ? `<div class="decision-block"><h4>${icon("loop", 13)} Next experiment</h4><div class="md">${markdown(d.next_experiment)}</div></div>` : ""}
      ${full && d.validation_needed ? `<div class="decision-block"><h4>${icon("shield", 13)} Validation needed before real-world use</h4><div class="md">${markdown(d.validation_needed)}</div></div>` : ""}
      <p class="footnote">Written by the judge, who proposed nothing and ran nothing. <button class="std-link" data-open="verdict.md">Read the full verdict →</button></p>
    </section>`;
  }
  const all = Object.values(v.agents);
  const done = all.filter(a => a.status === "done").length;
  const item = loopItems(v).find(i => ["active", "human"].includes(loopState(v, i)));
  if (isRunLive(v)) {
    return `<section class="card decision-card">
      <div class="card-head" style="margin-bottom:0"><div class="card-title"><span class="ico">${icon("clock", 18)}</span><h2>In progress</h2></div>
        ${item ? `<span class="badge ${item.key === "approval" ? "human" : "accent"}">${esc(item.label)}</span>` : ""}</div>
      <div class="progress-big"><b>${done}</b><span class="muted">of ${all.length} agents finished</span></div>
      <div class="meter"><i style="width:${all.length ? (100 * done / all.length).toFixed(1) : 0}%"></i></div>
      <p class="muted" style="margin-top:14px">${esc(r.phase)}</p>
      ${item ? `<p class="footnote">${esc(item.blurb)}</p>` : ""}
    </section>`;
  }
  return `<section class="card decision-card">
    <div class="card-head" style="margin-bottom:0"><div class="card-title"><span class="ico">${icon("decision", 18)}</span><h2>No decision</h2></div>${statusBadge(r.status)}</div>
    <p class="muted" style="margin-top:12px">${esc(r.phase || "The run ended before the judge wrote a verdict.")}</p>
    <p class="footnote">Everything the team produced is in the research record.</p>
  </section>`;
}

function standards(v: View) {
  const s = v.science, a = v.acceleration, r = v.run;
  const agents = Object.values(v.agents);
  const sessions = agents.filter(x => x.session).length + (r.pi_session ? 1 : 0);
  const handoffs = agents.filter(x => x.role !== "pi" && x.status !== "idle").length;
  const briefs = Object.keys(r.briefs ?? {}).length;
  const verdicts = { PASS: 0, CONCERNS: 0, FAIL: 0 } as Record<string, number>;
  s.reviews.forEach(x => { if (x.verdict) verdicts[x.verdict]++; });
  const audited = s.reviews.filter(x => x.verdict).length;
  const moved = movedHypotheses(v);
  const state = (key: string) => {
    const it = LOOP.find(i => i.key === key)!;
    const st = loopState(v, it);
    return st === "done" ? "done" : st === "active" || st === "human" ? "active" : "";
  };
  const approvalText = !r.approval ? "approval gate off for this run"
    : s.approval ? (s.approval.decision === "approve" ? (s.approval.changed ? "changed the experiment" : "approved the experiment") : "rejected the experiment")
    : r.status === "awaiting_approval" ? "approval pending" : "approves the experiment before review";
  const rows = [
    { title: "Orchestration on Omnigent", tab: "team", st: sessions > 1 ? (r.status === "done" ? "done" : "active") : "",
      text: `${agents.length} specialist agents · ${plural(sessions, "Omnigent session")} · ${plural(handoffs, "handoff")} through ${plural(briefs, "shared brief")}` },
    { title: "Scientific progress", tab: "hypotheses", st: s.selection ? "done" : state("hypotheses"),
      text: s.candidates.length ? `${plural(s.candidates.length, "competing hypothesis", "competing hypotheses")} with priors · ${s.selection ? `${s.selection.tests.length} tests compared, one chosen` : "test selection pending"}` : "Hypotheses and a decisive test are pending" },
    { title: "Acceleration and learning", tab: "acceleration", st: a.fanouts.length ? (s.decision ? "done" : "active") : "",
      text: `${a.speedup ? `${times(a.speedup)} parallel speedup` : "speedup measured per swarm"} · ${a.cost_ratio ? `${times(a.cost_ratio)} cheaper with agent swarms on a shared cache` : "savings measured per call"}${s.decision?.posterior?.length ? ` · beliefs updated on ${plural(moved, "hypothesis", "hypotheses")}` : ""}` },
    { title: "Scientific rigor", tab: "review", st: audited ? (s.decision ? "done" : "active") : state("review"),
      text: audited ? `${plural(audited, "independent audit")}: ${verdicts.PASS} pass · ${verdicts.CONCERNS} concerns · ${verdicts.FAIL} fail · analysis code and every artifact kept` : "Independent skeptic audits are pending" },
    { title: "Responsibility", tab: r.approval ? "experiment" : "team", st: s.approval?.decision === "approve" || (!r.approval && s.decision) ? "done" : r.status === "awaiting_approval" ? "active" : "",
      text: `Scientist ${approvalText} · budget cap ${usd(r.budget_usd, 2)} · agents have no web, code or file tools` },
  ];
  return `<section class="card">
    <div class="card-head"><div><h2>Discovery standards</h2><p>What this run shows, with the evidence behind each point.</p></div></div>
    <ul class="standards-list">${rows.map(x => `<li><div class="std-row">
      <span class="std-state ${x.st}">${x.st === "done" ? icon("check", 14, 2.6) : x.st === "active" ? `<i class="dot live"></i>` : `<i class="dot"></i>`}</span>
      <div><h4>${esc(x.title)}</h4><p>${esc(x.text)}</p></div>
      <a class="std-link" href="#/run/${esc(v.id)}/${x.tab}">View →</a></div></li>`).join("")}</ul>
  </section>`;
}

function movedHypotheses(v: View) {
  const post = new Map((v.science.decision?.posterior ?? []).map(p => [p.id, p.p]));
  return v.science.candidates.filter(c => post.has(c.id) && c.prior != null && Math.abs(post.get(c.id)! - c.prior) >= 0.005).length;
}

function kpis(v: View) {
  const a = v.acceleration, s = v.savings, sci = v.science;
  const sessions = Object.values(v.agents).filter(x => x.session).length + (v.run.pi_session ? 1 : 0);
  return `<div class="kpis">
    <div class="card kpi"><div class="kpi-label">${icon("users", 14)} Agents</div><div class="kpi-value">${Object.keys(v.agents).length}</div>
      <div class="kpi-sub">${plural(sessions, "Omnigent session")} so far</div></div>
    <div class="card kpi"><div class="kpi-label">${icon("hypotheses", 14)} Hypotheses</div><div class="kpi-value">${sci.candidates.length || "—"}</div>
      <div class="kpi-sub">${sci.selection ? `${sci.selection.tests.length} tests compared` : "agent-generated, with priors"}</div></div>
    <div class="card kpi"><div class="kpi-label">${icon("zap", 14)} Parallel speedup</div><div class="kpi-value ${a.speedup && a.speedup > 1 ? "good" : ""}">${times(a.speedup)}</div>
      <div class="kpi-sub">${a.wall_s ? `${dur(a.serial_s)} of agent work in ${dur(a.wall_s)}` : "measured per swarm"}</div></div>
    <div class="card kpi"><div class="kpi-label">${icon("coins", 14)} Spend</div><div class="kpi-value">${usd(s.usd)}</div>
      <div class="kpi-sub">${s.saved_usd > 0 ? `<span style="color:var(--good);font-weight:600">−${s.saved_pct.toFixed(0)}%</span> vs. ${usd(s.usd_uncached)} without cache` : "net of cache writes"}</div></div>
  </div>`;
}

function renderOverview(v: View) {
  paint("#tab-overview", ["ov", v.science, v.acceleration, v.savings, v.run.status, v.run.phase, v.run.briefs,
    Object.values(v.agents).map(a => [a.status, a.session])], () => `
    <div class="overview">${decisionCard(v)}${standards(v)}</div>
    ${kpis(v)}`);
}

// ---------------------------------------------------------------- team

function cacheShare(u?: Usage | null) {
  if (!u) return 0;
  const inp = (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0);
  return inp ? (u.cache_read_input_tokens ?? 0) / inp : 0;
}
function tokens(u?: Usage | null) {
  if (!u) return 0;
  return (u.input_tokens ?? 0) + (u.cache_creation_input_tokens ?? 0) + (u.cache_read_input_tokens ?? 0) + (u.output_tokens ?? 0);
}

// The team as a graph: PI on top, one column per fan-out with its shared brief, hand-offs as edges, artifacts below.
const NW = 230, NH = 112, GX = 64, GY = 22, HH = 46;
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

/** The file a graph chip opens: the file itself, or the first file inside a folder. */
function chipTarget(v: View, f: string) {
  return f.endsWith("/") ? v.files.filter(p => p.startsWith(f)).sort()[0] : v.files.includes(f) ? f : undefined;
}

const dotClass = (s?: string) => isLive(s) ? "live" : s === "done" ? "done" : s === "error" ? "error" : s === "skipped" ? "warn" : "";

function nodeHtml(v: View, label: string, b: Box) {
  const a = v.agents[label];
  const live = isLive(a.status);
  let sub = esc(a.focus);
  if (label === "pi") {
    sub = v.run.status === "awaiting_approval" ? "waiting for your approval" : a.status === "waiting" ? "waiting for agents"
      : v.run.status === "done" ? "verdict written" : esc(a.focus);
  } else if (a.status === "thinking" || a.status === "dispatched") {
    sub = `${a.status === "dispatched" ? "starting" : "thinking"} · <span data-since="${a.started ?? v.now}"></span>`;
  } else if (a.status === "error") {
    sub = `error: ${esc(a.error ?? "")}`;
  }
  const u = a.usage;
  const share = cacheShare(u);
  return `<button type="button" class="node ${label === "pi" ? "pi" : ""} ${live ? "thinking" : ""} ${drawerSel === label ? "selected" : ""}"
      data-sel="${esc(label)}" style="left:${b.x}px;top:${b.y}px" aria-label="${esc(a.name)}, ${esc(a.status)}">
    <span class="nbody">
      <span class="nhead">${icon(a.role)}<span class="name">${esc(a.name)}</span><i class="dot ${dotClass(a.status)}" title="${esc(a.status)}"></i></span>
      <span class="sub">${sub}</span>
    </span>
    <span class="nfoot">
      <span>${ktok(tokens(u))} tok</span>
      ${label === "pi" ? "" : `<span class="cachebar" title="${(100 * share).toFixed(0)}% of input read from cache"><i style="width:${(100 * share).toFixed(1)}%"></i></span>`}
      <span>${u ? usd(a.usd, 3) : ""}</span>
    </span>
  </button>`;
}

function stageHtml(v: View, stage: string, b: Box) {
  const brief = v.run.briefs?.[stage];
  const warm = brief?.prewarm ? v.prewarms[brief.prewarm] : undefined;
  const w = warm?.usage?.cache_creation_input_tokens ?? 0;
  const line = !brief ? "not published yet"
    : warm?.status === "done" ? `pre-warmed ${ktok(w || brief.prefix_tokens || 0)} tok`
    : warm?.status ? `pre-warm ${warm.status}…` : `${ktok(brief.prefix_tokens ?? 0)} tok · below cache minimum`;
  return `<button type="button" class="stage-head ${brief ? "" : "pending"} ${drawerSel === `stage:${stage}` ? "selected" : ""}" data-sel="stage:${stage}"
      style="left:${b.x}px;top:${b.y}px">
    <b>${icon("brief", 14)} Brief ${esc(stage)} · shared cache</b><span>${line}</span>
  </button>`;
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

function graphHtml(v: View) {
  const { boxes, width, height } = layout(v);
  const a = v.agents;
  const edges: { d: string; cls: string }[] = [];
  const st = (l: string) => a[l]?.status ?? "idle";
  const edgeCls = (from: string, to: string) =>
    isLive(st(to)) ? "active" : st(to) === "done" && (from.startsWith("stage:") || st(from) === "done") ? "done" : "";
  const link = (from: string, to: string, fs: "bottom" | "right", ts: "top" | "left", extra = "") => {
    if (boxes[from] && boxes[to]) edges.push({ d: orth(boxes[from], boxes[to], fs, ts), cls: `${edgeCls(from, to)} ${extra}` });
  };
  const byRole = (r: string) => Object.keys(a).filter(l => a[l].role === r).sort();

  for (const s of ["A", "B", "C"]) {
    edges.push({ d: orth(boxes.pi, boxes[`stage:${s}`], "bottom", "top"), cls: v.run.briefs?.[s] ? "brief" : "" });
  }
  // a stage's brief feeds the members in its column; members inside a column chain top-down
  const column = (stage: string, roles: string[]) => {
    const nodes = roles.flatMap(byRole);
    nodes.forEach((n, i) => link(i === 0 ? `stage:${stage}` : nodes[i - 1], n, "bottom", "top", "brief"));
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
  const sel = drawerSel && a[drawerSel] ? drawerSel : null;
  const outs = sel ? [sel === "pi" ? "plan.md" : a[sel].out].filter((o): o is string => !!o) : [];
  const fileFor = (out: string) => ARTIFACTS.find(f => f.endsWith("/") ? out.startsWith(f) : out === f || out.replace(/\.md$/, ".json") === f);

  let html = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">
    <defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
      <path d="M0,1 L9,5 L0,9 z" class="arrow"/></marker></defs>
    ${edges.map(e => `<path class="edge ${e.cls}" d="${e.d}" marker-end="url(#arr)"/>`).join("")}
    ${outs.map(o => {
      const f = fileFor(o);
      return f && sel && boxes[`file:${f}`] && boxes[sel]
        ? `<path class="edge file show" d="${orth(boxes[sel], boxes[`file:${f}`], "bottom", "top")}"/>` : "";
    }).join("")}
  </svg>`;
  for (const s of ["A", "B", "C"]) html += stageHtml(v, s, boxes[`stage:${s}`]);
  for (const l of Object.keys(a)) if (boxes[l]) html += nodeHtml(v, l, boxes[l]);
  for (const f of ARTIFACTS) {
    const b = boxes[`file:${f}`];
    const hl = outs.some(o => fileFor(o) === f);
    const target = chipTarget(v, f);
    const style = `left:${b.x}px;top:${b.y}px;width:${b.w}px`;
    const inner = `${icon(f.endsWith("/") ? "brief" : "file", 14)}${esc(f)}`;
    html += target
      ? `<button type="button" class="file ready ${hl ? "hl" : ""}" data-open="${esc(target)}" style="${style}">${inner}</button>`
      : `<span class="file ${hl ? "hl" : ""}" style="${style}" title="not written yet">${inner}</span>`;
  }
  return `<div class="canvas-wrap" id="canvas-wrap"><div id="canvas" style="width:${width}px;height:${height}px">${html}</div></div>`;
}

/** Scale the graph to the card width, but never below a readable size: narrow screens scroll the graph sideways instead. */
function fitCanvas() {
  const canvas = document.querySelector<HTMLElement>("#canvas"), wrap = document.querySelector<HTMLElement>("#canvas-wrap");
  if (!canvas || !wrap || !wrap.clientWidth) return;
  const w = parseFloat(canvas.style.width), h = parseFloat(canvas.style.height);
  const scale = Math.max(0.5, Math.min(1, (wrap.clientWidth - 8) / w));
  canvas.style.transform = `scale(${scale})`;
  canvas.style.left = `${Math.max(0, (wrap.clientWidth - w * scale) / 2)}px`;
  canvas.style.marginRight = `${w * scale - w}px`;
  canvas.style.marginBottom = `${h * scale - h}px`;
}
const canvasObserver = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => fitCanvas());

function renderTeam(v: View) {
  const changed = paint("#tab-team", ["team", v.agents, v.run.briefs, v.prewarms, v.files, v.run.status, drawerSel], () => {
    const counts: Record<string, number> = {};
    Object.values(v.agents).forEach(a => { counts[a.role] = (counts[a.role] ?? 0) + 1; });
    const specs = ROLE_ORDER.filter(r => config.roles[r] && (counts[r] || (r === "human" && v.run.approval))).map(r => {
      const s = config.roles[r];
      return `<article class="card spec-card">
        <h3>${icon(r, 16)} ${esc(s.name)}${counts[r] > 1 ? ` <span class="badge sm">×${counts[r]}</span>` : ""}</h3>
        <dl class="kv"><dt>Decides</dt><dd>${esc(s.decision)}</dd><dt>Reads</dt><dd>${esc(s.inputs)}</dd>
          <dt>Writes</dt><dd class="mono">${esc(s.output)}</dd><dt>Tools</dt><dd>${esc(s.tools)}</dd></dl>
      </article>`;
    }).join("");
    return `<section class="card graph-card">
        <div class="graph-head"><div><h2>Agent swarms</h2>
          <p>Each agent swarm reads one shared brief from the prompt cache. Click an agent, a brief or an artifact to see exactly what it read and wrote.</p></div>
          <div class="legend">
            <span><i class="dot"></i>idle</span><span><i class="dot live"></i>thinking</span>
            <span><i class="dot done"></i>done</span><span><i class="dot error"></i>error</span>
            <span><i class="bar"></i>input read from cache</span>
          </div></div>
        ${graphHtml(v)}
      </section>
      <h2 class="section-title">Agent specifications <small>every role is an Omnigent agent on the cachew harness, model ${esc(shortModel(v.run.model))}</small></h2>
      <div class="grid grid-auto">${specs}</div>`;
  });
  if (changed) {
    const wrap = document.querySelector<HTMLElement>("#canvas-wrap");
    canvasObserver?.disconnect();
    if (wrap) canvasObserver?.observe(wrap);
    fitCanvas();
  }
}

// ---------------------------------------------------------------- hypotheses

function renderHypotheses(v: View) {
  paint("#tab-hypotheses", ["hyp", v.science.candidates, v.science.predictions, v.science.decision?.posterior, v.science.selection?.chosen], () => {
    const s = v.science;
    if (!s.candidates.length) {
      return emptyCard("hypotheses", "No hypotheses yet",
        "Theorists propose competing hypotheses from the evidence; the lead theorist merges them into candidates with priors. They appear here as soon as they are written.");
    }
    const post = new Map((s.decision?.posterior ?? []).map(p => [p.id, p.p]));
    const preds = new Map(s.predictions.map(p => [p.id, p]));
    const best = post.size ? [...post.entries()].sort((a, b) => b[1] - a[1])[0][0] : null;
    const rows = s.candidates.map(c => {
      const prior = c.prior ?? null;
      const p = post.get(c.id);
      const delta = p != null && prior != null ? p - prior : null;
      const pr = preds.get(c.id);
      const none = /^none\b|none of (these|the above)/i.test(c.claim ?? "");
      return `<article class="hyp ${none ? "none" : ""}">
        <span class="hyp-id">${esc(c.id)}</span>
        <div style="min-width:0">
          <p class="hyp-claim">${esc(c.claim ?? "")}</p>
          ${pr?.predicts ? `<p class="hyp-pred"><b>Predicts:</b> ${esc(pr.predicts)}${pr.test ? `<br><span class="muted small">Test: ${esc(pr.test)}</span>` : ""}</p>` : ""}
          <div class="hyp-tags"><span class="badge sm">Agent-generated</span>${none ? `<span class="badge sm">Catch-all</span>` : ""}${best === c.id ? `<span class="badge sm good">Most likely now</span>` : ""}</div>
        </div>
        <div class="hyp-probs">
          <div class="prob"><span>Prior</span><span class="meter muted"><i style="width:${prior != null ? (100 * prior).toFixed(1) : 0}%"></i></span><b>${prior != null ? pct(prior) : "—"}</b></div>
          <div class="prob"><span>Posterior</span><span class="meter"><i style="width:${p != null ? (100 * p).toFixed(1) : 0}%"></i></span><b>${p != null ? pct(p) : "—"}</b></div>
          ${delta != null ? `<p class="delta ${Math.abs(delta) < 0.005 ? "flat" : delta > 0 ? "up" : "down"}">${Math.abs(delta) < 0.005 ? "unchanged" : `${delta > 0 ? "▲" : "▼"} ${Math.abs(100 * delta).toFixed(0)} pts`}</p>` : ""}
        </div>
      </article>`;
    }).join("");
    return `<section class="card list-card">
      <div class="card-head"><div><h2>Candidate hypotheses</h2>
        <p>Merged by the lead theorist from ${plural(stepAgents(v, ["theorists"]).length, "theorist")}' proposals. Priors are set before the test;
          posteriors come from the judge after review. Both are the team's stated beliefs, not measurements.</p></div>
        <button class="btn outline sm" type="button" data-open="candidates.md">${icon("file", 14)} candidates.md</button></div>
      ${rows}
    </section>`;
  });
}

function emptyCard(ico: string, title: string, text: string) {
  return `<section class="card"><div class="empty"><span class="ico">${icon(ico, 20)}</span><h3>${esc(title)}</h3><p>${esc(text)}</p></div></section>`;
}

// ---------------------------------------------------------------- experiment

function renderExperiment(v: View) {
  const has = (f: string) => v.files.includes(f);
  paint("#tab-experiment", ["exp", v.science.selection, v.science.approval, v.run.approval, v.run.status, has("experiment/design.md"), has("experiment/analysis.py")], () => {
    const s = v.science.selection;
    const a = v.science.approval;
    if (!s && !has("experiment/design.md")) {
      return emptyCard("experiment", "No experiment yet",
        "Once the hypotheses are set, the experimenter compares 2-3 candidate tests, picks the one with the most information per unit of effort, and writes the design and analysis code.");
    }
    const approvedIdx = a?.test && s ? s.tests.findIndex(t => t === a.test) : -1;
    const tests = s?.tests.length ? `<div class="tests">${s.tests.map((t, i) => {
      const m = t.match(/^\s*\(?([A-Z]\d?|\d+)[:.)]\s*/);
      const label = m ? `Test ${m[1]}` : `Test ${String.fromCharCode(65 + i)}`;
      const text = m ? t.slice(m[0].length) : t;
      const pick = i === s.chosen_index;
      const approved = a?.decision === "approve" && (approvedIdx === i || (approvedIdx < 0 && pick));
      return `<article class="test-card ${approved || (pick && !a) ? "chosen" : ""}">
        <div class="test-top"><span class="letter">${esc(label)}</span><span class="tags">
          ${pick ? `<span class="badge sm accent">Experimenter's pick</span>` : ""}
          ${approved ? `<span class="badge sm human">${icon("check", 11, 2.6)} Approved by scientist</span>` : ""}</span></div>
        <p>${esc(text)}</p></article>`;
    }).join("")}</div>${s.why ? `<div class="why"><b>Why this test:</b> ${esc(s.why)}</div>` : ""}` : "";
    const gate = !v.run.approval
      ? `<p class="muted">This run was started without the approval gate, so the experiment went straight to review.</p>`
      : a ? `<dl class="kv"><dt>Decision</dt><dd>${a.decision === "approve" ? `<span class="badge good">Approved</span>` : `<span class="badge bad">Rejected</span>`}${a.changed ? ` <span class="badge human">Scientist changed the test</span>` : ""}</dd>
          <dt>Test</dt><dd>${esc(a.test || "the experimenter's pick")}</dd>${a.note ? `<dt>Note</dt><dd>${esc(a.note)}</dd>` : ""}
          ${a.at ? `<dt>When</dt><dd>${esc(new Date(a.at * 1000).toLocaleString())}</dd>` : ""}</dl>
          <p class="footnote">The skeptics read this decision in brief C.</p>`
      : v.run.status === "awaiting_approval" ? `<p>Waiting for your decision. Use the approval panel at the top of this page.</p>`
      : `<p class="muted">The run pauses here for your approval once the experimenter has picked a test.</p>`;
    return `<div class="grid" style="grid-template-columns:minmax(0,1fr)">
      ${s?.tests.length ? `<section class="card"><div class="card-head"><div><h2>Test selection</h2>
        <p>The experimenter compared ${plural(s.tests.length, "candidate test")} by how strongly each separates the hypotheses, and by feasibility and cost.</p></div></div>${tests}</section>` : ""}
      <section class="card"><div class="card-head"><div class="card-title"><span class="ico" style="background:var(--human-soft);color:var(--human)">${icon("approval", 18)}</span>
        <div><h2>Scientist approval</h2><p>The scientist approves consequential steps; the agents cannot skip this gate.</p></div></div></div>${gate}</section>
      <section class="card"><div class="card-head"><div><h2>Design and analysis</h2><p>Data, procedure, controls, sample size and the result each hypothesis predicts.</p></div>
        <button class="btn outline sm" type="button" data-open="experiment/design.md">${icon("external", 14)} Open</button></div>
        ${has("experiment/design.md") ? `<div class="md" data-file="experiment/design.md" data-fmt="md"><p class="muted">Loading…</p></div>` : `<p class="muted">Not written yet.</p>`}
        ${has("experiment/analysis.py") ? `<details class="code"><summary>${icon("code", 14)} experiment/analysis.py <span class="muted small">· reproducible analysis script</span></summary>
          <div class="md" data-file="experiment/analysis.py" data-fmt="code"></div></details>` : ""}
      </section>
    </div>`;
  });
}

// ---------------------------------------------------------------- review

function renderReview(v: View) {
  const verdictReady = v.files.includes("verdict.md");
  paint("#tab-review", ["rev", v.science.reviews, v.science.decision, verdictReady, Object.values(v.agents).filter(a => a.role === "skeptic").map(a => a.status)], () => {
    const rs = v.science.reviews;
    const n = (k: string) => rs.filter(r => r.verdict === k).length;
    if (!rs.some(r => r.verdict) && !verdictReady) {
      return emptyCard("review", "No reviews yet",
        "After the experiment is set, independent skeptics each audit one angle: numbers, code against plan, alternative explanations, overclaiming, reproducibility and missing controls.");
    }
    return `<div class="stack">
      <section class="card"><div class="card-head"><div><h2>Independent audits</h2>
        <p>Each skeptic reviews the hypotheses, the design and the code from one angle only, and states PASS, CONCERNS or FAIL.</p></div></div>
        <div class="verdict-tiles">
          <div class="verdict-tile good"><b>${n("PASS")}</b><span>pass</span></div>
          <div class="verdict-tile warn"><b>${n("CONCERNS")}</b><span>concerns</span></div>
          <div class="verdict-tile bad"><b>${n("FAIL")}</b><span>fail</span></div>
        </div>
        <div class="grid grid-auto" style="margin-top:14px">${rs.map(r => {
          const cls = r.verdict === "PASS" ? "good" : r.verdict === "FAIL" ? "bad" : r.verdict ? "warn" : "";
          const st = v.agents[r.label]?.status;
          return `<button type="button" class="review-card" data-sel="${esc(r.label)}">
            <span class="rc-top"><h4 class="ellipsis">${esc(r.name)}</h4>${r.verdict ? `<span class="badge sm ${cls}">${esc(r.verdict)}</span>` : `<span class="badge sm">${isLive(st) ? "reviewing…" : "pending"}</span>`}</span>
            <p>Angle: ${esc(r.focus)}</p></button>`;
        }).join("")}</div>
      </section>
      ${v.science.decision?.answer ? decisionCard(v) : ""}
      ${verdictReady ? `<section class="card"><div class="card-head"><div><h2>The judge's verdict</h2><p>Weighs the hypotheses, the experiment and every review, citing the team's artifacts.</p></div></div>
        <div class="md" data-file="verdict.md" data-fmt="md"><p class="muted">Loading…</p></div></section>` : ""}
    </div>`;
  });
}

// ---------------------------------------------------------------- acceleration

function renderAcceleration(v: View) {
  paint("#tab-acceleration", ["acc", v.acceleration, v.savings, v.stages], () => {
    const a = v.acceleration, s = v.savings, t = s.tokens;
    const inputAll = t.input_tokens + t.cache_creation_input_tokens + t.cache_read_input_tokens;
    const maxUsd = Math.max(s.usd_uncached, s.usd, 1e-9);
    const w = (x: number, max: number) => `${Math.max(1, (100 * x) / Math.max(max, 1e-9)).toFixed(1)}%`;
    const STEP_LABEL: Record<string, string> = { scouts: "Evidence · scouts", theorists: "Hypotheses · theorists", skeptics: "Review · skeptics" };
    const fan = a.fanouts.length ? a.fanouts.map(f => {
      const max = Math.max(f.serial_s, f.wall_s);
      return `<div class="bar-group"><h4><span class="bg-label">${esc(STEP_LABEL[f.step] ?? f.step)} <small>${plural(f.agents, "agent")}</small></span><span>${times(f.speedup)} faster</span></h4>
        <div class="bar-line"><span>One at a time</span><span class="meter muted"><i style="width:${w(f.serial_s, max)}"></i></span><b>${dur(f.serial_s)}</b></div>
        <div class="bar-line"><span>In parallel</span><span class="meter good"><i style="width:${w(f.wall_s, max)}"></i></span><b>${dur(f.wall_s)}</b></div></div>`;
    }).join("") : `<p class="muted">Measured as soon as the first agent swarm finishes.</p>`;
    const rows = v.stages.map(st => `<tr data-sel="stage:${esc(st.stage)}">
      <td><b>Brief ${esc(st.stage)}</b><div class="muted small mono nowrap">${esc(st.brief)}</div></td>
      <td class="r">${num(st.prefix_tokens)}</td>
      <td><div class="tags">${st.agents.map(l => `<span class="badge sm">${esc(v.agents[l]?.name ?? l)}</span>`).join("") || `<span class="muted">—</span>`}</div></td>
      <td>${esc(st.prewarm ?? "skipped")}</td><td class="r">${st.writes}</td><td class="r">${st.reads}</td>
      <td class="r">${ktok(st.cache_read_tokens)}</td><td class="r">${usd(st.usd_uncached, 4)}</td><td class="r">${usd(st.usd, 4)}</td>
      <td class="r"><b style="color:${st.saved_usd > 0 ? "var(--good)" : "inherit"}">${usd(st.saved_usd, 4)}</b></td>
      <td>${st.prefix_match ? `<span class="badge sm good">identical</span>` : `<span class="badge sm bad">differs</span>`}</td></tr>`).join("");
    return `<div class="stack">
      <section class="card"><div class="card-head"><div class="card-title"><span class="ico">${icon("gauge", 18)}</span><h2>The bottleneck this lab attacks</h2></div></div>
        <div class="bottleneck">
          <div><h4>Bottleneck</h4><p>A discovery loop needs many specialist perspectives, and every specialist must first read everything the team knows.
            Done one after another, or by re-sending the full history to each agent, that makes each loop slow and expensive, so fewer hypotheses get tested.</p></div>
          <div><h4>What the lab does</h4><p>Each stage launches an agent swarm: specialists that run in parallel and read one shared brief from the prompt cache.
            The PI writes the brief once and pre-warms it; every member then reads it at the cache price. Faster, cheaper loops mean more candidates screened for the same budget.</p></div>
        </div></section>
      <div class="kpis" style="margin-top:0">
        <div class="card kpi"><div class="kpi-label">${icon("zap", 14)} Parallel speedup</div><div class="kpi-value good">${times(a.speedup)}</div>
          <div class="kpi-sub">${a.wall_s ? `${dur(a.serial_s)} of agent work in ${dur(a.wall_s)}` : "after the first swarm"}</div></div>
        <div class="card kpi"><div class="kpi-label">${icon("coins", 14)} Cost per loop</div><div class="kpi-value good">${a.cost_ratio ? times(a.cost_ratio) : "—"}<small>${a.cost_ratio ? "cheaper" : ""}</small></div>
          <div class="kpi-sub">${usd(s.usd)} vs. ${usd(s.usd_uncached)} without cache</div></div>
        <div class="card kpi"><div class="kpi-label">${icon("brief", 14)} Read from cache</div><div class="kpi-value">${ktok(t.cache_read_input_tokens)}</div>
          <div class="kpi-sub">${inputAll ? `${pct(t.cache_read_input_tokens / inputAll)} of all input tokens` : "tokens"}</div></div>
        <div class="card kpi"><div class="kpi-label">${icon("clock", 14)} Wall time</div><div class="kpi-value">${a.run_s ? dur(a.run_s) : "—"}</div>
          <div class="kpi-sub">${a.agent_s ? `${dur(a.agent_s)} of total agent time` : "whole loop"}</div></div>
      </div>
      <div class="grid grid-2">
        <section class="card"><div class="card-head"><div><h2>Time: parallel agent swarms</h2><p>Agent time if each specialist ran after the previous one, vs. the measured wall time.</p></div></div>
          <div class="bars">${fan}</div></section>
        <section class="card"><div class="card-head"><div><h2>Cost: one shared cache</h2><p>The real bill vs. the same calls with every input token at full price.</p></div></div>
          <div class="bars"><div class="bar-group"><h4><span class="bg-label">All model calls <small>${plural(s.calls, "call")}</small></span><span>${s.saved_usd > 0 ? `−${s.saved_pct.toFixed(1)}%` : ""}</span></h4>
            <div class="bar-line"><span>Without cache</span><span class="meter muted"><i style="width:${w(s.usd_uncached, maxUsd)}"></i></span><b>${usd(s.usd_uncached)}</b></div>
            <div class="bar-line"><span>With cache</span><span class="meter good"><i style="width:${w(s.usd, maxUsd)}"></i></span><b>${usd(s.usd)}</b></div></div>
            <dl class="kv" style="margin-top:6px"><dt>Read from cache</dt><dd>${num(t.cache_read_input_tokens)} tok</dd><dt>Written to cache</dt><dd>${num(t.cache_creation_input_tokens)} tok</dd>
              <dt>Uncached input</dt><dd>${num(t.input_tokens)} tok</dd><dt>Output</dt><dd>${num(t.output_tokens)} tok</dd><dt>Model calls</dt><dd>${s.calls}</dd></dl></div></section>
      </div>
      <section class="card"><div class="card-head"><div><h2>Shared briefs and the cache</h2>
        <p>One write (the pre-warm) and one read per member is the goal. "Identical" confirms every member got byte-identical instructions, which the cache needs.</p></div></div>
        ${v.stages.length ? `<div class="table-wrap"><table class="data"><thead><tr><th>Brief</th><th class="r">Prefix tok</th><th>Shared by</th><th>Pre-warm</th>
          <th class="r">Writes</th><th class="r">Reads</th><th class="r">From cache</th><th class="r">No cache</th><th class="r">With cache</th><th class="r">Saved</th><th>System prefix</th></tr></thead>
          <tbody>${rows}</tbody></table></div>` : `<p class="muted">No brief has been published yet.</p>`}
      </section>
      <p class="footnote">All numbers are measured from this run's API responses, not projected. "With cache" includes the pre-warm calls and the 1.25× cache-write premium,
        so savings are net. Parallel speedup compares summed agent time with wall time inside each swarm.</p>
    </div>`;
  });
}

// ---------------------------------------------------------------- research record

const GROUPS: [string, (p: string) => boolean][] = [
  ["Question and plan", p => p === "plan.md" || p.startsWith("briefs/")],
  ["Evidence", p => p.startsWith("literature/")],
  ["Hypotheses", p => p.startsWith("proposals/") || p.startsWith("candidates.") || p === "predictions.json"],
  ["Experiment and approval", p => p.startsWith("experiment/") || p === "selection.json" || p === "approval.json"],
  ["Review and decision", p => p.startsWith("reviews/") || p === "verdict.md" || p === "decision.json"],
];

function fileIcon(p: string) {
  return p.endsWith(".py") ? "code" : p.endsWith(".json") ? "json" : p.endsWith(".yaml") ? "yaml" : p.startsWith("briefs/") ? "brief" : "file";
}

function authorName(v: View, p: string) {
  if (p.startsWith("briefs/")) return "PI";
  const who = v.authors[p];
  if (!who) return "";
  if (who === "scientist") return "Scientist";
  return v.agents[who]?.name ?? who;
}

function renderRecord(v: View) {
  paint("#tab-record", ["rec", v.files, v.bundle], () => {
    const used = new Set<string>();
    const sections = GROUPS.map(([title, test]) => {
      const files = v.files.filter(f => test(f) && !used.has(f));
      files.forEach(f => used.add(f));
      return { title, files };
    });
    const rest = v.files.filter(f => !used.has(f));
    if (rest.length) sections.push({ title: "Other", files: rest });
    sections.push({ title: "Agent specifications (Omnigent bundle)", files: v.bundle });
    const row = (p: string) => {
      const who = p.startsWith("agent/") ? "Studio" : authorName(v, p);
      return `<li><button type="button" class="record-row" data-open="${esc(p)}"><span class="ico">${icon(fileIcon(p), 16)}</span>
        <span style="min-width:0"><span class="path" style="display:block">${esc(p)}</span>${who ? `<span class="desc">by ${esc(who)}</span>` : ""}</span>
        <span class="badge sm">${esc(p.split(".").pop()!.toUpperCase())}</span></button></li>`;
    };
    const body = sections.filter(s => s.files.length).map(s => `<li class="record-group">${esc(s.title)}</li>${s.files.map(row).join("")}`).join("");
    return `<section class="card list-card">
      <div class="card-head"><div><h2>Research record</h2>
        <p>Every artifact the team wrote, who wrote it, and the agent specifications, so each decision can be reconstructed.
          Model calls with their exact usage are logged alongside in the run's <code>calls/</code> folder.</p></div></div>
      ${body ? `<ul class="record">${body}</ul>` : `<div class="empty"><p>Nothing written yet.</p></div>`}
    </section>`;
  });
}

// ---------------------------------------------------------------- drawer (inspector)

function kv(rows: [string, string][]) {
  return `<dl class="kv">${rows.map(([k, val]) => `<dt>${esc(k)}</dt><dd>${val}</dd>`).join("")}</dl>`;
}

function usageRows(u?: Usage | null, cost?: number | null, uncached?: number | null): [string, string][] {
  if (!u) return [["Usage", `<span class="muted">no call yet</span>`]];
  return [
    ["Read from cache", `${num(u.cache_read_input_tokens ?? 0)} tok <span class="muted">(${pct(cacheShare(u))} of input)</span>`],
    ["Written to cache", `${num(u.cache_creation_input_tokens ?? 0)} tok`],
    ["Uncached input", `${num(u.input_tokens ?? 0)} tok`],
    ["Output", `${num(u.output_tokens ?? 0)} tok`],
    ["Cost", `${usd(cost, 4)} <span class="muted">(without cache ${usd(uncached, 4)})</span>`],
  ];
}

let lastFocus: HTMLElement | null = null;

function openDrawer(sel: string) {
  if (!current) return;
  if (!drawerSel) lastFocus = document.activeElement as HTMLElement | null;
  drawerSel = sel;
  memo.delete("#drawer-body");
  $("#drawer").hidden = false;
  document.body.classList.add("drawer-open");
  renderDrawer(current);
  memo.delete("#tab-team");
  if (currentTab === "team") renderTeam(current);
  ($("#drawer .drawer-head [data-close]") as HTMLElement).focus();
}

function closeDrawer() {
  if (!drawerSel) return;
  drawerSel = null;
  $("#drawer").hidden = true;
  document.body.classList.remove("drawer-open");
  if (current && currentTab === "team") { memo.delete("#tab-team"); renderTeam(current); }
  lastFocus?.focus?.();
}

function renderDrawer(v: View) {
  const sel = drawerSel!;
  let kicker = "", title = "", head = "", file: string | null = null;
  if (sel.startsWith("stage:")) {
    const stage = sel.slice(6);
    const b = v.run.briefs?.[stage];
    const row = v.stages.find(s => s.stage === stage);
    const warm = b?.prewarm ? v.prewarms[b.prewarm] : undefined;
    kicker = "Shared brief";
    title = `Brief ${stage}`;
    head = !b ? `<p class="muted">Not written yet. The PI writes it right before this stage's agents start.</p>` : kv([
      ["Brief id", `<code>${esc(b.id)}</code>`],
      ["Cached prefix", `${num(b.prefix_tokens ?? 0)} tok <span class="muted">(model minimum ${num(v.run.min_cache_tokens ?? 0)})</span>`],
      ["Pre-warm", `${esc(warm?.status ?? "skipped")} · wrote ${num(warm?.usage?.cache_creation_input_tokens ?? 0)} tok for ${usd(warm?.usd, 4)}`],
      ["Members read it", row ? `${row.reads}× · ${num(row.cache_read_tokens)} tok` : "—"],
      ["Saved", row ? `<b style="color:var(--good)">${usd(row.saved_usd, 4)}</b>` : "—"],
      ["System prompt", `<code>${esc(b.system_sha ?? "")}</code> ${row ? (row.prefix_match ? `<span class="badge sm good">identical for all</span>` : `<span class="badge sm bad">differs</span>`) : ""}`],
    ]);
    if (b) file = `briefs/${b.id}.md`;
  } else if (sel.startsWith("path:")) {
    file = sel.slice(5);
    kicker = "Research record";
    title = file;
    const who = file.startsWith("agent/") ? "" : authorName(v, file);
    head = who ? kv([["Written by", esc(who)]]) : "";
  } else {
    const a = v.agents[sel];
    if (!a) { closeDrawer(); return; }
    const spec = config.roles[a.role];
    kicker = spec?.name ?? a.role;
    title = a.name;
    const elapsed = a.started ? (a.finished ?? v.now) - a.started : 0;
    const omni = (s?: string | null) => s ? `<a href="${esc(config.omnigent_url)}/c/${esc(s)}" target="_blank" rel="noopener">Open in Omnigent ↗</a>` : `<span class="muted">—</span>`;
    head = kv([
      ["Decides", esc(spec?.decision ?? a.focus)],
      ["Angle", esc(a.focus)],
      ["Status", `<i class="dot ${isLive(a.status) ? "live" : a.status === "done" ? "done" : a.status === "error" ? "error" : ""}"></i> ${esc(a.status)}${elapsed ? ` · ${dur(elapsed)}` : ""}`],
      ["Reads", esc(spec?.inputs ?? "—") + (a.stage ? ` <span class="muted">(brief ${esc(a.stage)})</span>` : "")],
      ["Writes", `<code>${esc(sel === "pi" ? "plan.md" : a.out ?? "—")}</code>`],
      ["Tools", esc(spec?.tools ?? "—")],
      ["Model", esc(v.run.model)],
      ...usageRows(sel === "pi" ? a.usage : a.usage, a.usd, a.usd_uncached),
      ["Omnigent", omni(sel === "pi" ? v.run.pi_session : a.session)],
      ...(a.error ? [["Error", `<span style="color:var(--bad)">${esc(a.error)}</span>`] as [string, string]] : []),
    ]);
    file = sel === "pi" ? "plan.md" : a.out ?? null;
  }
  $("#drawer-kicker").textContent = kicker;
  $("#drawer-title").textContent = title;
  const ready = file ? v.files.includes(file) || v.bundle.includes(file) || file.startsWith("briefs/") : false;
  paint("#drawer-body", [sel, head, file, ready], () => `${head}
    ${file ? `<div class="drawer-file"><div class="drawer-file-head"><span class="ellipsis">${esc(file)}</span></div>
      ${ready ? `<div class="md" data-file="${esc(file)}" data-fmt="${file.endsWith(".md") ? "md" : "code"}"><p class="muted">Loading…</p></div>` : `<p class="muted small">Not written yet.</p>`}</div>` : ""}`);
}

// ---------------------------------------------------------------- file loading

function hydrate(root: HTMLElement) {
  if (!current) return;
  const id = current.id;
  root.querySelectorAll<HTMLElement>("[data-file]").forEach(async el => {
    const path = el.dataset.file!;
    const key = `${id}:${path}`;
    try {
      let text = fileCache.get(key);
      if (text == null) {
        text = await api<string>(`/api/runs/${encodeURIComponent(id)}/file?path=${encodeURIComponent(path)}`);
        fileCache.set(key, text);
      }
      if (!el.isConnected) return;
      el.innerHTML = el.dataset.fmt === "md" ? markdown(text) : `<pre><code>${esc(text)}</code></pre>`;
    } catch (e) {
      if (el.isConnected) el.innerHTML = `<p class="muted small">Could not load ${esc(path)}: ${esc((e as Error).message)}</p>`;
    }
  });
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
    if (/^&gt;\s?/.test(l)) {
      const q: string[] = [];
      for (; i < lines.length && /^&gt;\s?/.test(lines[i]); i++) q.push(lines[i].replace(/^&gt;\s?/, ""));
      out.push(`<blockquote>${inline(q.join(" "))}</blockquote>`);
      continue;
    }
    if (/^\s*\|.*\|\s*$/.test(l)) {
      const rows: string[][] = [];
      for (; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) {
        if (/^\s*\|[\s:|-]+\|\s*$/.test(lines[i])) continue;
        rows.push(lines[i].trim().slice(1, -1).split("|").map(c => inline(c.trim())));
      }
      const [hd, ...rest] = rows;
      if (hd) out.push(`<table><thead><tr>${hd.map(c => `<th>${c}</th>`).join("")}</tr></thead><tbody>${rest.map(r => `<tr>${r.map(c => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`);
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
    for (; i < lines.length && lines[i].trim() && !/^(#{1,4}\s|```|\s*([-*+]|\d+\.)\s+|\s*\||&gt;)/.test(lines[i]); i++) para.push(lines[i]);
    if (!para.length) { out.push(`<p>${inline(l)}</p>`); i++; continue; }
    out.push(`<p>${inline(para.join(" "))}</p>`);
  }
  return out.join("\n");
}

// ---------------------------------------------------------------- routing & polling

function showPage(page: "home" | "new" | "run") {
  $("#boot").hidden = true;
  for (const p of ["home", "new", "run"]) $(`#page-${p}`).hidden = p !== page;
  $("#nav-new").hidden = page === "new";
  window.clearTimeout(homeTimer);
  if (page !== "run") { window.clearTimeout(runTimer); closeDrawer(); }
}

function route() {
  const h = location.hash;
  const m = h.match(/^#\/run\/([\w.-]+)(?:\/(\w+))?/);
  if (m) {
    const id = m[1];
    const tab = m[2] && ["overview", "team", "hypotheses", "experiment", "review", "acceleration", "record"].includes(m[2]) ? m[2] : "overview";
    const changedRun = id !== currentId;
    const changedTab = tab !== currentTab;
    if (changedRun) {
      currentId = id;
      current = null;
      memo.clear();
      closeDrawer();
      $("#run-head").innerHTML = `<div class="skeleton-block" style="height:120px"></div>`;
      ["#run-alert", "#run-loop"].forEach(s => { $(s).innerHTML = ""; });
      window.scrollTo(0, 0);
    }
    currentTab = tab;
    showPage("run");
    if (changedTab && !changedRun) {
      memo.delete("#run-tabs");
      memo.delete(`#tab-${tab}`);
    }
    window.clearTimeout(runTimer);
    if (current && !changedRun) renderRun(current);
    pollRun();
    return;
  }
  currentId = null;
  current = null;
  if (h.startsWith("#/new")) {
    showPage("new");
    if (!form.built) { renderNewStatic(); form.built = true; }
    window.scrollTo(0, 0);
    setTimeout(() => $("#f-question")?.focus(), 50);
    return;
  }
  showPage("home");
  if (h === "#runs") $("#runs")?.scrollIntoView();
  pollHome();
}

async function pollHome() {
  await loadRuns();
  window.clearTimeout(homeTimer);
  if (!$("#page-home").hidden) homeTimer = window.setTimeout(pollHome, 6000);
}

async function pollRun() {
  const id = currentId;
  if (!id) return;
  window.clearTimeout(runTimer);
  try {
    const v = await api<View>(`/api/runs/${encodeURIComponent(id)}`);
    if (id !== currentId) return;
    current = v;
    try { renderRun(v); } catch (e) { console.error("render failed", e); }
  } catch (e) {
    if (id !== currentId) return;
    if (!current) {
      $("#run-head").innerHTML = `<nav class="crumbs"><a href="#/">${icon("back", 14)} Runs</a></nav>` +
        callout("bad", "alert", "Could not load this run", (e as Error).message);
      return;
    }
  }
  if (id !== currentId) return;
  const live = current && isRunLive(current);
  runTimer = window.setTimeout(pollRun, live ? 1500 : 10000);
}

function tickElapsed() {
  const skew = current ? current.now - Date.now() / 1000 : 0;
  const now = Date.now() / 1000 + skew;
  document.querySelectorAll<HTMLElement>("[data-since]").forEach(el => {
    el.textContent = dur(now - Number(el.dataset.since));
  });
}

// ---------------------------------------------------------------- wiring

async function main() {
  try {
    config = await api<Config>("/api/config");
  } catch (e) {
    $("#boot").innerHTML = callout("bad", "alert", "Cachew Studio could not start", (e as Error).message);
    return;
  }
  const omni = $<HTMLAnchorElement>("#omnigent-link");
  omni.href = config.omnigent_url;
  renderHomeStatic();

  document.addEventListener("click", ev => {
    const t = ev.target as HTMLElement;
    if (t.closest("[data-close]")) { closeDrawer(); return; }
    const open = t.closest<HTMLElement>("[data-open]");
    if (open) { ev.preventDefault(); openDrawer(`path:${open.dataset.open}`); return; }
    const sel = t.closest<HTMLElement>("[data-sel]");
    if (sel && !t.closest(".drawer")) { openDrawer(sel.dataset.sel!); return; }
    const appr = t.closest<HTMLElement>("[data-approval]");
    if (appr) { ev.preventDefault(); sendApproval(appr.dataset.approval as "approve" | "reject"); return; }
    if (t.closest("#stop-run")) {
      const id = currentId;
      if (!id) return;
      confirmDialog("Stop this run?", "Agents that are already thinking finish their call; nothing new is dispatched. Everything produced so far stays in the research record.", "Stop run")
        .then(async ok => {
          if (!ok) return;
          try {
            await api(`/api/runs/${encodeURIComponent(id)}/stop`, { method: "POST" });
            toast("Run stopped");
          } catch (e) { toast((e as Error).message, true); }
          pollRun();
        });
    }
  });
  document.addEventListener("submit", ev => {
    if ((ev.target as HTMLElement).id === "approval-form") { ev.preventDefault(); sendApproval("approve"); }
  });
  document.addEventListener("keydown", ev => {
    if (ev.key === "Escape" && drawerSel && !($<HTMLDialogElement>("#confirm")).open) closeDrawer();
  });
  window.addEventListener("hashchange", route);
  setInterval(tickElapsed, 1000);
  route();
}

main();
