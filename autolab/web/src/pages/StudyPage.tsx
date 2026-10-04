import { useCallback, useEffect, useRef, useState } from "react"
import { toast } from "sonner"
import { FileText, Loader2, Pause, Play, RotateCcw, ScrollText, Square } from "lucide-react"
import { api, type Activity, type Study } from "@/lib/api"
import { primaryAction, STATUS, toneClass } from "@/lib/status"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ApprovalCard } from "@/components/Approval"
import { AgentPanel, FeedRow } from "@/components/AgentPanel"
import { FilesTab, JobLog, PlanTab, ResultsTab } from "@/components/StudyTabs"
import { TeamGraph } from "@/components/TeamGraph"
import { makeScale, Timeline } from "@/components/Timeline"
import { cn } from "@/lib/utils"

export function StatusBadge({ status }: { status: Study["status"] }) {
  const s = STATUS[status]
  return <Badge variant="outline" className={cn("gap-1.5", toneClass[s.tone])}>
    {s.tone === "busy" && <Loader2 className="animate-spin" />}{s.label}</Badge>
}

export function StudyPage({ name, onChanged }: { name: string; onChanged: () => void }) {
  const [s, setS] = useState<Study | null>(null)
  const [A, setA] = useState<Activity | null>(null)
  const [round, setRound] = useState<number | null>(null)
  const [cursor, setCursor] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [tab, setTab] = useState("activity")
  const [logOpen, setLogOpen] = useState(false)
  const [pending, setPending] = useState(false)
  const [now, setNow] = useState(Date.now() / 1000)
  const playing = useRef<number | null>(null)
  const [isPlaying, setIsPlaying] = useState(false)

  // "busy" comes from the server, which checks for a live phase process (UI job, terminal, or older server)
  const running = !!s?.busy
  const viewRound = round ?? s?.round ?? 0
  const live = running && viewRound === s?.round

  // Responses can arrive out of order; only apply one if no newer request has been answered.
  const studySeq = useRef(0), actSeq = useRef(0)
  const loadStudy = useCallback(async () => {
    const id = ++studySeq.current
    try { const x = await api.study(name); if (id === studySeq.current) setS(x) }
    catch (e) { if (id === studySeq.current) toast.error(String((e as Error).message), { id: "sync" }) }
  }, [name])
  const loadActivity = useCallback(async () => {
    const id = ++actSeq.current
    if (!viewRound) { setA(null); return }
    try { const x = await api.activity(name, viewRound); if (id === actSeq.current) setA(x) } catch { /* keep last */ }
  }, [name, viewRound])

  useEffect(() => { setS(null); setA(null); setRound(null); setCursor(null); setSelected(null); setTab("activity") }, [name])
  useEffect(() => { loadStudy() }, [loadStudy])
  useEffect(() => { loadActivity() }, [loadActivity])
  // poll: fast while something runs, slow otherwise (picks up CLI changes)
  useEffect(() => {
    const id = window.setInterval(() => { loadStudy(); loadActivity() }, live ? 1500 : 6000)
    return () => clearInterval(id)
  }, [live, loadStudy, loadActivity])
  useEffect(() => {
    if (!live) return
    const id = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => clearInterval(id)
  }, [live])
  const prevRunning = useRef(running)
  useEffect(() => {
    if (prevRunning.current && !running) { onChanged(); loadActivity() }
    prevRunning.current = running
  }, [running, onChanged, loadActivity])

  const act = async (action: string, body: Record<string, unknown> = {}) => {
    setPending(true)
    try { await api.act(name, action, body); await loadStudy(); onChanged(); setCursor(null); setRound(null) }
    catch (e) { toast.error((e as Error).message) }
    finally { setPending(false) }
  }

  const stopPlay = () => { if (playing.current) clearInterval(playing.current); playing.current = null; setIsPlaying(false) }
  const play = () => {
    if (!A) return
    if (playing.current) return stopPlay()
    const sc = makeScale(A, now); if (!sc) return
    const steps = 200
    let i = 0
    setIsPlaying(true)
    playing.current = window.setInterval(() => {
      if (i > steps) { stopPlay(); setCursor(null); return }
      setCursor(sc.inv(110 + (890 * i++) / steps))
    }, 60)
  }
  useEffect(() => stopPlay, [name])

  if (!s) return <div className="flex h-full items-center justify-center"><Loader2 className="animate-spin text-muted-foreground" /></div>

  const lastT = A ? Math.max(0, ...A.edges.map((e) => e.t), ...A.feed.map((f) => f.t), ...A.spans.map((x) => x.end ?? x.start)) : now
  const T = cursor ?? (live ? now : lastT)
  const primary = running ? null : primaryAction(s.status, s.round)
  const stuck = !running && ["planning", "running", "analyzing"].includes(s.status)

  return (
    <div className="flex flex-col gap-4 p-4 md:p-6">
      {/* header */}
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold leading-tight tracking-tight text-balance">{s.question}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <StatusBadge status={s.status} />
            <span>Round {s.round}/{s.config.max_rounds}</span>
            <span>·</span><span>{s.config.model.replace("claude-", "")}</span>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {s.job && <Button variant="ghost" size="icon" onClick={() => setLogOpen(true)} aria-label="Logs"><ScrollText /></Button>}
          {running && <Button variant="outline" onClick={() => api.stop(name).then(() => toast("Stopping")).catch((e) => toast.error(e.message))}><Square />Stop</Button>}
          {stuck && <Button variant="outline" onClick={() => act("recover")}><RotateCcw />Reset</Button>}
          {s.status === "concluded" && <Button variant="outline" onClick={() => setTab("results")}><FileText />Results</Button>}
          {s.status === "approved" && <Button variant="outline" disabled={pending} onClick={() => setTab("plan")}>Plan</Button>}
          {primary && <Button disabled={pending} onClick={() => act(primary.action)}>{pending && <Loader2 className="animate-spin" />}{primary.label}</Button>}
        </div>
      </div>

      {s.status === "awaiting_approval" && !running && (
        <ApprovalCard s={s} busy={pending}
          onApprove={(run) => act(run ? "approve_run" : "approve")}
          onRevise={(feedback) => act("revise", { feedback })}
          onShowPlan={() => setTab("plan")} />
      )}
      {["plan_failed", "analysis_failed"].includes(s.status) && s.problems.length > 0 && (
        <Card className="border-red-500/30 p-3 text-sm">{s.problems.map((p, i) => <div key={i} className="text-red-700 dark:text-red-300">{p}</div>)}</Card>
      )}

      {/* live team */}
      {s.round > 0 && A && (
        <Card className="gap-0 overflow-hidden p-0">
          <div className="flex items-center gap-2 border-b px-3 py-2">
            <Select value={String(viewRound)} onValueChange={(v) => { stopPlay(); setCursor(null); setSelected(null); setRound(Number(v)) }}>
              <SelectTrigger size="sm" className="w-24"><SelectValue /></SelectTrigger>
              <SelectContent>{Array.from({ length: s.round }, (_, i) => s.round - i).map((r) =>
                <SelectItem key={r} value={String(r)}>R{String(r).padStart(2, "0")}</SelectItem>)}</SelectContent>
            </Select>
            {live && cursor == null && <Badge variant="outline" className={toneClass.busy}><span className="size-1.5 animate-pulse rounded-full bg-blue-500" />Live</Badge>}
            {cursor != null && <Button variant="ghost" size="sm" onClick={() => { stopPlay(); setCursor(null) }}>{live ? "Back to live" : "Reset"}</Button>}
            <div className="flex-1" />
            {!A.traced && <span className="text-xs text-muted-foreground">Not traced</span>}
            <Button variant="ghost" size="sm" onClick={play} disabled={!A.edges.length}>{isPlaying ? <Pause /> : <Play />}Replay</Button>
          </div>
          <div className="grid lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="h-[560px]">
              <TeamGraph A={A} T={T} live={live && cursor == null} selected={selected} onSelect={setSelected} />
            </div>
            <div className="h-[560px] border-t lg:border-t-0 lg:border-l">
              <AgentPanel A={A} T={T} live={live && cursor == null} selected={selected} onSelect={setSelected} />
            </div>
          </div>
          <div className="overflow-x-auto border-t px-3 py-2">
            <Timeline A={A} T={T} now={live ? now : lastT} onScrub={(t) => { stopPlay(); setCursor(t) }} />
          </div>
        </Card>
      )}

      <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
        <TabsList>
          <TabsTrigger value="activity">Activity</TabsTrigger>
          <TabsTrigger value="plan">Plan</TabsTrigger>
          <TabsTrigger value="results">Results</TabsTrigger>
          <TabsTrigger value="files">Files</TabsTrigger>
        </TabsList>
        <TabsContent value="activity" className="pt-2">
          {A && A.feed.length ? (
            <ScrollArea className="h-[420px]">
              {[...A.feed].filter((f) => f.t <= T + 0.5).reverse().map((f, i) =>
                <FeedRow key={i} f={f} A={A} onClick={() => { stopPlay(); setCursor(f.t) }} />)}
            </ScrollArea>
          ) : <div className="py-12 text-center text-sm text-muted-foreground">{s.round ? "No activity" : "Not started"}</div>}
        </TabsContent>
        <TabsContent value="plan" className="pt-4"><PlanTab s={s} /></TabsContent>
        <TabsContent value="results" className="pt-4"><ResultsTab s={s} /></TabsContent>
        <TabsContent value="files" className="pt-4"><FilesTab name={name} /></TabsContent>
      </Tabs>

      <Sheet open={logOpen} onOpenChange={setLogOpen}>
        <SheetContent className="w-full sm:max-w-2xl">
          <SheetHeader><SheetTitle>Log</SheetTitle></SheetHeader>
          {s.job && <JobLog job={s.job} className="px-4" />}
        </SheetContent>
      </Sheet>
    </div>
  )
}
