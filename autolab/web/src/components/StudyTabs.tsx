import { useEffect, useRef, useState } from "react"
import Markdown from "react-markdown"
import { Check, X } from "lucide-react"
import { api, type Job, type Study } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { cn } from "@/lib/utils"

function CodeViewer({ files }: { files: Record<string, string> }) {
  const names = Object.keys(files)
  const [cur, setCur] = useState(names[0])
  if (!names.length) return null
  return (
    <div className="grid gap-3 md:grid-cols-[180px_minmax(0,1fr)]">
      <div className="flex flex-col gap-0.5">
        {names.map((n) => (
          <button key={n} onClick={() => setCur(n)}
            className={cn("truncate rounded-md px-2 py-1 text-left font-mono text-xs hover:bg-muted", n === cur && "bg-muted font-medium")}>{n}</button>
        ))}
      </div>
      <pre className="max-h-[560px] overflow-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs leading-5">{files[cur ?? names[0]]}</pre>
    </div>
  )
}

export function PlanTab({ s }: { s: Study }) {
  const P = s.pending
  if (!P.round) return <Empty text="No plan yet" />
  const sel = P.selection
  const preds = P.predictions?.predictions ?? []
  const hyps = Object.fromEntries((P.candidates?.hypotheses ?? []).map((h) => [h.id, h.statement]))
  return (
    <div className="flex flex-col gap-6">
      {P.approval && (
        <div className="text-xs text-muted-foreground">
          Approved by {P.approval.approved_by} · {P.approval.at.replace("T", " ")} · <span className="font-mono">{P.approval.hashes.experiment.slice(0, 10)}</span>
        </div>
      )}
      {sel?.why && <p className="max-w-3xl text-sm">{sel.why}</p>}
      {preds.length > 0 && (
        <Table>
          <TableHeader><TableRow><TableHead className="w-12" /><TableHead>Hypothesis</TableHead><TableHead>Predicts</TableHead><TableHead>Refuted if</TableHead></TableRow></TableHeader>
          <TableBody>
            {preds.map((p) => (
              <TableRow key={p.hypothesis} className="align-top">
                <TableCell className="font-mono text-xs">{p.hypothesis}</TableCell>
                <TableCell className="max-w-xs whitespace-normal text-xs text-muted-foreground">{hyps[p.hypothesis]}</TableCell>
                <TableCell className="max-w-sm whitespace-normal text-xs">{p.prediction}</TableCell>
                <TableCell className="max-w-xs whitespace-normal text-xs text-muted-foreground">{p.falsified_if}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {P.code && <CodeViewer files={P.code} />}
      {P.plan_md && <details className="text-sm"><summary className="cursor-pointer font-medium">plan.md</summary><div className="md mt-3 max-w-3xl"><Markdown>{P.plan_md}</Markdown></div></details>}
    </div>
  )
}

export function ResultsTab({ s }: { s: Study }) {
  const rounds = s.rounds.map((r) => r.id)
  const last = rounds.at(-1)
  const v = [...s.rounds].reverse().find((r) => r.verdict)?.verdict
  if (!s.hypotheses.length) return <Empty text="No results yet" />
  return (
    <div className="flex flex-col gap-6">
      {v && (
        <div className="max-w-3xl">
          <p className="text-sm">{v.summary}</p>
          {v.next_direction && <p className="mt-2 text-sm text-muted-foreground">Next: {v.next_direction}</p>}
        </div>
      )}
      <div className="grid gap-2">
        {s.hypotheses.map((h) => {
          const c = last ? h.credence[last] : undefined
          const traj = rounds.map((r) => h.credence[r]).filter((x) => x != null)
          return (
            <div key={h.id} className="grid grid-cols-[40px_minmax(0,1fr)_160px_90px] items-center gap-3 text-sm">
              <span className="font-mono text-xs text-muted-foreground">{h.id}</span>
              <span className={cn("line-clamp-2", h.status === "refuted" && "text-muted-foreground line-through")}>{h.statement}</span>
              <div className="flex items-center gap-2">
                <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                  <div className={cn("h-full rounded-full", h.status === "refuted" ? "bg-muted-foreground/40" : h.status === "supported" ? "bg-emerald-500" : "bg-blue-500")}
                    style={{ width: `${Math.round((c ?? 0) * 100)}%` }} />
                </div>
                <span className="w-9 text-right font-mono text-xs">{c != null ? c.toFixed(2) : "–"}</span>
              </div>
              <Badge variant={h.status === "refuted" ? "outline" : "secondary"} className="justify-self-start capitalize"
                title={traj.map((x) => x.toFixed(2)).join(" → ")}>{h.status}</Badge>
            </div>
          )
        })}
      </div>
      {v?.prediction_scorecard && (
        <Table>
          <TableHeader><TableRow><TableHead className="w-12" /><TableHead>Predicted</TableHead><TableHead>Observed</TableHead><TableHead className="w-12" /></TableRow></TableHeader>
          <TableBody>
            {v.prediction_scorecard.map((p) => (
              <TableRow key={p.hypothesis} className="align-top">
                <TableCell className="font-mono text-xs">{p.hypothesis}</TableCell>
                <TableCell className="max-w-sm whitespace-normal text-xs text-muted-foreground">{p.predicted}</TableCell>
                <TableCell className="max-w-sm whitespace-normal text-xs">{p.observed}</TableCell>
                <TableCell>{p.held ? <Check className="size-4 text-emerald-600" /> : <X className="size-4 text-red-600" />}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {s.findings && <details className="text-sm"><summary className="cursor-pointer font-medium">Findings</summary><div className="md mt-3 max-w-3xl"><Markdown>{s.findings}</Markdown></div></details>}
    </div>
  )
}

export function FilesTab({ name }: { name: string }) {
  const [tree, setTree] = useState<{ path: string; size: number }[]>([])
  const [cur, setCur] = useState<string | null>(null)
  const [text, setText] = useState("")
  useEffect(() => { api.tree(name).then(setTree).catch(() => setTree([])) }, [name])
  const open = async (p: string) => {
    setCur(p)
    if (p.endsWith(".html")) { window.open(`/files/${encodeURIComponent(name)}/${p}`, "_blank", "noopener"); return }
    let t = await api.file(name, p).catch((e) => String(e))
    if (p.endsWith(".json")) { try { t = JSON.stringify(JSON.parse(t), null, 2) } catch { /* keep raw */ } }
    setText(t)
  }
  return (
    <div className="grid gap-3 md:grid-cols-[260px_minmax(0,1fr)]">
      <ScrollArea className="h-[560px] rounded-lg border">
        <div className="flex flex-col p-1">
          {tree.map((f) => (
            <button key={f.path} onClick={() => open(f.path)}
              className={cn("truncate rounded px-2 py-1 text-left font-mono text-xs hover:bg-muted", cur === f.path && "bg-muted font-medium")}>{f.path}</button>
          ))}
        </div>
      </ScrollArea>
      <pre className="h-[560px] overflow-auto rounded-lg border bg-muted/30 p-3 font-mono text-xs leading-5">{cur ? text : ""}</pre>
    </div>
  )
}

export function JobLog({ job, onDone, className }: { job: Job; onDone?: () => void; className?: string }) {
  const [text, setText] = useState("")
  const [state, setState] = useState(job.state)
  const pre = useRef<HTMLPreElement>(null)
  useEffect(() => {
    let off = 0, stop = false, timer = 0
    setText("")
    const tick = async () => {
      try {
        const j = await api.job(job.id, off)
        off = j.offset
        if (j.text) setText((t) => t + j.text)
        setState(j.state)
        if (j.state === "running" && !stop) timer = window.setTimeout(tick, 1200)
        else if (job.state === "running") onDone?.()
      } catch { /* server restarted */ }
    }
    tick()
    return () => { stop = true; clearTimeout(timer) }
  }, [job.id]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { pre.current?.scrollTo(0, pre.current.scrollHeight) }, [text])
  return (
    <div className={className}>
      <div className="mb-1 text-xs text-muted-foreground">{job.action} · {state}</div>
      <pre ref={pre} className="max-h-[70vh] overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 font-mono text-xs leading-5">{text}</pre>
    </div>
  )
}

function Empty({ text }: { text: string }) {
  return <div className="py-12 text-center text-sm text-muted-foreground">{text}</div>
}
