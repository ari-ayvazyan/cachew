import { useState } from "react"
import { toast } from "sonner"
import { ChevronDown, Loader2 } from "lucide-react"
import { api } from "@/lib/api"
import { Button } from "@/components/ui/button"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"

const STOP = new Set(["does", "the", "and", "for", "with", "what", "how", "are", "when", "which", "that", "this", "small", "from", "into"])
const slug = (q: string) =>
  q.toLowerCase().replace(/[^a-z0-9]+/g, " ").trim().split(" ").filter((w) => w.length > 2 && !STOP.has(w)).slice(0, 4).join("-") || "study"

const MODELS = [{ v: "claude-sonnet-5-5", l: "Sonnet 5.5" }, { v: "claude-opus-5-5", l: "Opus 5.5" }]

export function NewStudyDialog({ open, onOpenChange, onCreated, existing }: {
  open: boolean; onOpenChange: (o: boolean) => void; onCreated: (name: string) => void; existing: string[]
}) {
  const [q, setQ] = useState("")
  const [ctx, setCtx] = useState("")
  const [model, setModel] = useState(MODELS[0].v)
  const [cap, setCap] = useState("30")
  const [rounds, setRounds] = useState("6")
  const [fanout, setFanout] = useState("3")
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    let name = slug(q), i = 2
    while (existing.includes(name)) name = `${slug(q)}-${i++}`
    setBusy(true)
    try {
      await api.create({ name, question: q.trim(), context: ctx, model, max_run_minutes: cap, max_rounds: rounds, fanout })
      await api.act(name, "plan")
      onOpenChange(false); setQ(""); setCtx("")
      onCreated(name)
    } catch (e) { toast.error((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader><DialogTitle>New study</DialogTitle></DialogHeader>
        <Textarea autoFocus rows={4} value={q} onChange={(e) => setQ(e.target.value)}
          placeholder="Does mixup improve calibration of small CNNs on FashionMNIST?" />
        <Collapsible>
          <CollapsibleTrigger render={<Button variant="ghost" size="sm" className="-ml-2 text-muted-foreground" />}>
            Options <ChevronDown />
          </CollapsibleTrigger>
          <CollapsibleContent className="grid gap-4 pt-2">
            <div className="grid gap-1.5"><Label htmlFor="ctx">Context</Label>
              <Textarea id="ctx" rows={2} value={ctx} onChange={(e) => setCtx(e.target.value)} placeholder="Constraints, prior knowledge" /></div>
            <div className="grid grid-cols-4 gap-3">
              <div className="grid gap-1.5"><Label>Model</Label>
                <Select value={model} onValueChange={(v) => setModel(String(v))}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>{MODELS.map((m) => <SelectItem key={m.v} value={m.v}>{m.l}</SelectItem>)}</SelectContent>
                </Select></div>
              <div className="grid gap-1.5"><Label htmlFor="cap">Run cap (min)</Label>
                <Input id="cap" type="number" min={1} value={cap} onChange={(e) => setCap(e.target.value)} /></div>
              <div className="grid gap-1.5"><Label>Parallel</Label>
                <Select value={fanout} onValueChange={(v) => setFanout(String(v))}>
                  <SelectTrigger className="w-full"><SelectValue /></SelectTrigger>
                  <SelectContent>{["1", "2", "3", "4"].map((n) => <SelectItem key={n} value={n}>{n} per role</SelectItem>)}</SelectContent>
                </Select></div>
              <div className="grid gap-1.5"><Label htmlFor="rounds">Max rounds</Label>
                <Input id="rounds" type="number" min={1} value={rounds} onChange={(e) => setRounds(e.target.value)} /></div>
            </div>
          </CollapsibleContent>
        </Collapsible>
        <DialogFooter>
          <Button disabled={q.trim().length < 10 || busy} onClick={submit}>{busy && <Loader2 className="animate-spin" />}Start</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
