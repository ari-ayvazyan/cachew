import Markdown from "react-markdown"
import { ArrowDownLeft, ArrowUpRight, Ban, CircleAlert, FilePen, Globe, Play, Send, Terminal, X, type LucideIcon } from "lucide-react"
import type { Activity, FeedItem } from "@/lib/api"
import { ktok } from "@/lib/status"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"
import { meta, statesAt, team } from "./TeamGraph"

export const FEED_ICON: Record<string, { icon: LucideIcon; cls: string }> = {
  dispatch: { icon: Send, cls: "text-blue-600 dark:text-blue-400" },
  report: { icon: ArrowDownLeft, cls: "text-emerald-600 dark:text-emerald-400" },
  write: { icon: FilePen, cls: "text-amber-600 dark:text-amber-400" },
  denied: { icon: Ban, cls: "text-red-600" },
  error: { icon: CircleAlert, cls: "text-muted-foreground" },
  web: { icon: Globe, cls: "text-violet-600 dark:text-violet-400" },
  exec: { icon: Terminal, cls: "text-orange-600" },
  run: { icon: Play, cls: "text-orange-600" },
  start: { icon: Play, cls: "text-muted-foreground" },
  approve: { icon: ArrowUpRight, cls: "text-amber-600" },
}

const time = (t: number) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })

export function FeedRow({ f, A, onClick }: { f: FeedItem; A: Activity | null; onClick?: () => void }) {
  const ic = FEED_ICON[f.kind] ?? { icon: CircleAlert, cls: "text-muted-foreground" }
  const Icon = ic.icon
  return (
    <button onClick={onClick} className="flex w-full gap-2.5 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted">
      <Icon className={cn("mt-0.5 size-3.5 shrink-0", ic.cls)} />
      <span className="min-w-0 flex-1">
        <span className="font-medium">{meta(A, f.agent).label}</span>{" "}
        <span className="text-muted-foreground">{f.text}</span>
      </span>
      <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{time(f.t)}</span>
    </button>
  )
}

export function AgentPanel({ A, T, live, selected, onSelect }: {
  A: Activity; T: number; live: boolean; selected: string | null; onSelect: (id: string | null) => void
}) {
  const st = statesAt(A, T, live)
  if (!selected) {
    const active = [...team(A), "sandbox"].filter((id) => st[id]?.state === "working")
    return (
      <div className="flex h-full flex-col gap-2 p-3">
        <div className="text-xs font-medium text-muted-foreground">{active.length ? "Working" : "Idle"}</div>
        {active.map((id) => {
          const m = meta(A, id), Icon = m.icon
          return (
            <button key={id} onClick={() => onSelect(id)} className="rounded-lg border p-2.5 text-left hover:bg-muted">
              <div className="flex items-center gap-2 text-sm font-medium"><Icon className="size-4 text-blue-600" />{m.label}<span className="text-xs font-normal text-muted-foreground">{m.sub}</span></div>
              <div className="mt-1 line-clamp-3 text-xs text-muted-foreground">{st[id].detail || "working"}</div>
            </button>
          )
        })}
        {A.sandbox?.stdout_tail && st.sandbox.state === "working" && (
          <pre className="max-h-40 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px]">{A.sandbox.stdout_tail.join("\n")}</pre>
        )}
        {!active.length && <div className="text-sm text-muted-foreground">Click an agent</div>}
      </div>
    )
  }
  const a = A.agents[selected]
  const s = st[selected] ?? { state: "idle", detail: "" }
  const R = meta(A, selected)
  const mine = A.feed.filter((f) => f.agent === selected && f.t <= T).slice(-15).reverse()
  const tok = a?.tokens
  return (
    <ScrollArea className="h-full">
      <div className="flex flex-col gap-3 p-3">
        <div className="flex items-center gap-2">
          <R.icon className="size-4" />
          <span className="font-medium">{R.label}</span>
          {R.sub && <span className="text-xs text-muted-foreground">{R.sub}</span>}
          <Badge variant="outline" className="capitalize">{s.state}</Badge>
          <Button variant="ghost" size="icon-sm" className="ml-auto" onClick={() => onSelect(null)} aria-label="Close"><X /></Button>
        </div>
        {s.detail && s.state === "working" && <div className="text-sm text-muted-foreground">{s.detail}</div>}
        {a?.task && (
          <section>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Task</div>
            <div className="max-h-48 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-2 text-xs">{a.task}</div>
          </section>
        )}
        {a?.report && (
          <section>
            <div className="mb-1 text-xs font-medium text-muted-foreground">Report</div>
            <div className="md max-h-64 overflow-auto rounded-md border p-2 text-xs"><Markdown>{a.report}</Markdown></div>
          </section>
        )}
        {a && Object.keys(a.tools).length > 0 && (
          <div className="flex flex-wrap gap-1">
            {Object.entries(a.tools).map(([k, v]) => <Badge key={k} variant="secondary" className="font-mono text-[11px]">{k} {v}</Badge>)}
          </div>
        )}
        {tok && (tok.input + tok.output) > 0 && (
          <div className="grid grid-cols-3 gap-2 text-center">
            {[["new", tok.input + tok.output], ["cached", tok.cache_read], ["cache write", tok.cache_write]].map(([l, v]) => (
              <div key={l as string} className="rounded-md bg-muted p-1.5">
                <div className="font-mono text-sm">{ktok(v as number)}</div>
                <div className="text-[10px] text-muted-foreground">{l}</div>
              </div>
            ))}
          </div>
        )}
        {mine.length > 0 && <div className="-mx-2">{mine.map((f, i) => <FeedRow key={i} f={f} A={A} />)}</div>}
      </div>
    </ScrollArea>
  )
}
