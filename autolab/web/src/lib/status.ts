import type { Status } from "./api"

// Short status labels. "tone" picks the badge style.
export const STATUS: Record<Status, { label: string; tone: "busy" | "you" | "bad" | "good" | "idle" }> = {
  ready: { label: "Ready", tone: "idle" },
  planning: { label: "Planning", tone: "busy" },
  plan_failed: { label: "Planning failed", tone: "bad" },
  awaiting_approval: { label: "Needs approval", tone: "you" },
  approved: { label: "Approved", tone: "idle" },
  running: { label: "Running", tone: "busy" },
  ran: { label: "Ready to analyze", tone: "idle" },
  analyzing: { label: "Analyzing", tone: "busy" },
  analysis_failed: { label: "Analysis failed", tone: "bad" },
  concluded: { label: "Concluded", tone: "good" },
}

// The one main action per status (the header button).
export function primaryAction(status: Status, round: number): { action: string; label: string } | null {
  switch (status) {
    case "ready": return { action: "plan", label: round === 0 ? "Start" : `Start round ${round + 1}` }
    case "plan_failed": return { action: "plan", label: "Retry planning" }
    case "approved": return { action: "run", label: "Run experiment" }
    case "ran": return { action: "analyze", label: "Analyze" }
    case "analysis_failed": return { action: "analyze", label: "Retry analysis" }
    case "concluded": return { action: "reopen", label: "Another round" }
    default: return null
  }
}

export const toneClass: Record<string, string> = {
  busy: "bg-blue-500/10 text-blue-700 dark:text-blue-300 border-blue-500/20",
  you: "bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/30",
  bad: "bg-red-500/10 text-red-700 dark:text-red-300 border-red-500/20",
  good: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20",
  idle: "bg-muted text-muted-foreground border-transparent",
}

export const ago = (t: number) => {
  const s = Date.now() / 1000 - t
  return s < 60 ? "now" : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${Math.round(s / 3600)}h` : `${Math.round(s / 86400)}d`
}
export const dur = (s: number) => (s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${String(Math.round(s % 60)).padStart(2, "0")}s`)
export const ktok = (n: number) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : `${n}`)
