// Thin client for the autolab server (autolab/server.py). Types mirror its JSON.

export type Status =
  | "ready" | "planning" | "plan_failed" | "awaiting_approval" | "approved"
  | "running" | "ran" | "analyzing" | "analysis_failed" | "concluded"

export type Job = {
  id: string; kind: string; target: string; action: string; cmd: string
  started: number; ended: number | null; rc: number | null
  state: "running" | "done" | "failed" | "cancelled"
}

export type StudySummary = {
  name: string; question: string; status: Status; round: number; max_rounds: number; busy: boolean; updated: number
}

export type Hypothesis = {
  id: string; statement: string; status: "alive" | "refuted" | "supported"; catch_all: boolean
  credence: Record<string, number>
}

export type Prediction = { hypothesis: string; prediction: string; falsified_if: string }
export type Experiment = { id: string; description: string; discriminates?: string[]; est_cpu_minutes?: number }

export type Verdict = {
  summary: string; decision: "continue" | "conclude"; next_direction: string; surprise?: string
  prediction_scorecard?: { hypothesis: string; predicted: string; observed: string; held: boolean }[]
}

export type Pending = {
  round?: string; plan_md?: string | null; code?: Record<string, string>
  selection?: { chosen: string; why: string; rejected?: { id: string; why: string }[] } | null
  candidates?: { hypotheses: { id: string; statement: string }[]; experiments: Experiment[] } | null
  predictions?: { predictions: Prediction[] } | null
  approval?: { approved_by: string; at: string; note: string; hashes: Record<string, string> } | null
  run?: { exit_code: number; timed_out: boolean; wall_seconds: number; results_json: string; sandbox: string } | null
  review_md?: string | null
  verdict?: Verdict | null
}

export type Study = {
  name: string; question: string; context: string; status: Status; round: number
  config: { model: string; models: Record<string, string>; max_run_minutes: number; max_rounds: number }
  hypotheses: Hypothesis[]; findings: string | null; problems: string[]; tampered: string[]
  rounds: { id: string; verdict: Verdict | null; review: { verdict: string } | null; run: Pending["run"] }[]
  pending: Pending; actions: string[]; job: Job | null; busy: boolean
}

export type AgentState = {
  state: "idle" | "working" | "waiting" | "done" | "stopped"; detail: string; task: string | null; report: string | null
  calls: number; tools: Record<string, number>; cost_usd: number; last_t: number | null
  tokens: { input: number; output: number; cache_read: number; cache_write: number }
}
export type EdgeKind = "dispatch" | "report" | "write" | "read" | "denied" | "web" | "exec" | "run" | "start" | "approve"
export type ActEdge = { from: string; to: string; kind: EdgeKind; label: string; t: number }
export type Span = { agent: string; start: number; end: number | null; kind: string }
export type FeedItem = { t: number; agent: string; kind: string; text: string }
export type RoleInfo = { id: string; group: string; label: string; sub: string; col: number; row: number }
export type Activity = {
  round: string | null; roles: RoleInfo[]; agents: Record<string, AgentState>; edges: ActEdge[]; spans: Span[]; feed: FeedItem[]
  sandbox?: { state: string; stdout_tail: string[] | null }; traced: boolean
}

export type Env = { studies_dir: string; sandbox: boolean; claude_login: boolean; api_key: boolean; cpus: number }

async function call<T>(path: string, body?: unknown): Promise<T> {
  const init: RequestInit = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Autolab": "1" }, body: JSON.stringify(body),
  }
  const r = await fetch(path, init)
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((data as { detail?: string }).detail || `${r.status} ${r.statusText}`)
  return data as T
}

const q = encodeURIComponent
export const api = {
  env: () => call<Env>("/api/env"),
  studies: () => call<StudySummary[]>("/api/studies"),
  study: (n: string) => call<Study>(`/api/studies/${q(n)}`),
  activity: (n: string, round?: number) => call<Activity>(`/api/studies/${q(n)}/activity${round ? `?round=${round}` : ""}`),
  tree: (n: string) => call<{ path: string; size: number }[]>(`/api/studies/${q(n)}/tree`),
  create: (b: Record<string, unknown>) => call<{ name: string }>("/api/studies", b),
  act: (n: string, action: string, b: Record<string, unknown> = {}) => call<{ job?: Job }>(`/api/studies/${q(n)}/${action}`, b),
  job: (id: string, offset = 0) => call<Job & { text: string; offset: number }>(`/api/jobs/${id}?offset=${offset}`),
  cancel: (id: string) => call<Job>(`/api/jobs/${id}/cancel`, {}),
  stop: (n: string) => call<{ stopped: number[] }>(`/api/studies/${q(n)}/stop`, {}),
  lab: () => call<{ studies: { name: string; status: string; round: number; board: boolean; job: Job | null }[]; running: Job[] }>("/api/lab"),
  runLab: (b: Record<string, unknown>) => call<{ job: Job }>("/api/lab", b),
  cachew: () => call<{ api_key: boolean; report: string | null; report_updated: number | null; job: Job | null }>("/api/cachew"),
  runCachew: (b: Record<string, unknown>) => call<{ job: Job }>("/api/cachew", b),
  file: async (n: string, path: string) => {
    const r = await fetch(`/files/${q(n)}/${path.split("/").map(q).join("/")}`)
    if (!r.ok) throw new Error(`${path}: ${r.status}`)
    return r.text()
  },
}
