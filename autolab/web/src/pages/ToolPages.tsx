import { useCallback, useEffect, useState } from "react"
import Markdown from "react-markdown"
import { toast } from "sonner"
import { ExternalLink } from "lucide-react"
import { api, type Job } from "@/lib/api"
import { ago } from "@/lib/status"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { JobLog } from "@/components/StudyTabs"

function Field({ id, label, children }: { id: string; label: string; children: React.ReactNode }) {
  return <div className="grid gap-1.5"><Label htmlFor={id}>{label}</Label>{children}</div>
}

export function LabPage() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.lab>> | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [name, setName] = useState(`fanout-${new Date().toISOString().slice(5, 16).replace(/[-:T]/g, "")}`)
  const [rounds, setRounds] = useState("8")
  const [pace, setPace] = useState("0")
  const [fresh, setFresh] = useState(false)
  const load = useCallback(() => api.lab().then((x) => { setD(x); setJob((j) => j ?? x.running[0] ?? null) }), [])
  useEffect(() => { load() }, [load])
  const run = async () => {
    try { setJob((await api.runLab({ name, max_rounds: rounds, pace, fresh })).job) } catch (e) { toast.error((e as Error).message) }
  }
  return (
    <div className="flex max-w-4xl flex-col gap-4 p-4 md:p-6">
      <h1 className="text-xl font-semibold">Fan-out loop</h1>
      <Card>
        <CardHeader><CardTitle className="text-sm">New run · simulated, free</CardTitle></CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid grid-cols-3 gap-3">
            <Field id="ln" label="Name"><Input id="ln" value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field id="lr" label="Max rounds"><Input id="lr" type="number" value={rounds} onChange={(e) => setRounds(e.target.value)} /></Field>
            <Field id="lp" label="Pace (s)"><Input id="lp" type="number" step="0.5" value={pace} onChange={(e) => setPace(e.target.value)} /></Field>
          </div>
          <div className="flex items-center justify-between">
            <Label className="flex items-center gap-2 font-normal"><Checkbox checked={fresh} onCheckedChange={(v) => setFresh(!!v)} />Overwrite</Label>
            <Button onClick={run} disabled={job?.state === "running"}>Run</Button>
          </div>
        </CardContent>
      </Card>
      {job && <JobLog key={job.id} job={job} onDone={load} />}
      {d && d.studies.length > 0 && (
        <Table>
          <TableHeader><TableRow><TableHead>Run</TableHead><TableHead>Status</TableHead><TableHead>Rounds</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody>{d.studies.map((s) => (
            <TableRow key={s.name}>
              <TableCell className="font-mono text-xs">{s.name}</TableCell>
              <TableCell><Badge variant="outline">{s.status}</Badge></TableCell>
              <TableCell>{s.round}</TableCell>
              <TableCell className="text-right">{s.board && <Button variant="ghost" size="sm" render={<a href={`/files/${encodeURIComponent(s.name)}/board.html`} target="_blank" rel="noopener" />}>Board<ExternalLink /></Button>}</TableCell>
            </TableRow>))}
          </TableBody>
        </Table>
      )}
    </div>
  )
}

export function CachewPage() {
  const [d, setD] = useState<Awaited<ReturnType<typeof api.cachew>> | null>(null)
  const [job, setJob] = useState<Job | null>(null)
  const [model, setModel] = useState("claude-haiku-4-5")
  const [n, setN] = useState("8")
  const [maxTok, setMaxTok] = useState("600")
  const [brief, setBrief] = useState("6000")
  const load = useCallback(() => api.cachew().then((x) => { setD(x); setJob((j) => j ?? (x.job?.state === "running" ? x.job : null)) }), [])
  useEffect(() => { load() }, [load])
  const haiku = model.includes("haiku")
  const run = async () => {
    try { setJob((await api.runCachew({ model, subagents: n, max_tokens: maxTok, brief_target: brief, effort: haiku ? "" : "low" })).job) }
    catch (e) { toast.error((e as Error).message) }
  }
  return (
    <div className="flex max-w-4xl flex-col gap-4 p-4 md:p-6">
      <h1 className="text-xl font-semibold">Cachew cost experiment</h1>
      {d && !d.api_key && <Alert><AlertDescription>Needs <code>ANTHROPIC_API_KEY</code> set before starting the UI.</AlertDescription></Alert>}
      <Card>
        <CardHeader><CardTitle className="text-sm">Naive vs cache vs compact · real API</CardTitle></CardHeader>
        <CardContent className="grid gap-4">
          <div className="grid grid-cols-4 gap-3">
            <Field id="cm" label="Model">
              <Select value={model} onValueChange={(v) => { setModel(String(v)); setBrief(String(v).includes("haiku") ? "6000" : "") }}>
                <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="claude-haiku-4-5">Haiku 4.5 · ~$0.30</SelectItem>
                  <SelectItem value="claude-sonnet-5-5">Sonnet 5.5</SelectItem>
                  <SelectItem value="claude-opus-5-5">Opus 5.5 · $1–3</SelectItem>
                </SelectContent>
              </Select>
            </Field>
            <Field id="cn" label="Sub-agents"><Input id="cn" type="number" value={n} onChange={(e) => setN(e.target.value)} /></Field>
            <Field id="ct" label="Max output"><Input id="ct" type="number" value={maxTok} onChange={(e) => setMaxTok(e.target.value)} /></Field>
            <Field id="cb" label="Brief tokens"><Input id="cb" type="number" value={brief} onChange={(e) => setBrief(e.target.value)} /></Field>
          </div>
          <div className="flex justify-end"><Button onClick={run} disabled={!d?.api_key || job?.state === "running"}>Run</Button></div>
        </CardContent>
      </Card>
      {job && <JobLog key={job.id} job={job} onDone={load} />}
      {d?.report && (
        <Card>
          <CardHeader><CardTitle className="text-sm">Last report · {d.report_updated ? ago(d.report_updated) : ""}</CardTitle></CardHeader>
          <CardContent className="md text-sm"><Markdown>{d.report}</Markdown></CardContent>
        </Card>
      )}
    </div>
  )
}
