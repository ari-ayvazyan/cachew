import { useState } from "react"
import { Clock } from "lucide-react"
import type { Study } from "@/lib/api"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog"
import { Textarea } from "@/components/ui/textarea"

export function ApprovalCard({ s, busy, onApprove, onRevise, onShowPlan }: {
  s: Study; busy: boolean; onApprove: (run: boolean) => void; onRevise: (feedback: string) => void; onShowPlan: () => void
}) {
  const [open, setOpen] = useState(false)
  const [fb, setFb] = useState("")
  const P = s.pending
  const exp = P.candidates?.experiments.find((e) => e.id === P.selection?.chosen)
  const preds = P.predictions?.predictions ?? []
  return (
    <Card className="border-amber-500/40 bg-amber-500/[0.03]">
      <CardHeader className="flex flex-row items-start gap-4">
        <div className="min-w-0 flex-1">
          <CardTitle className="text-base leading-snug">{exp?.description ?? P.selection?.chosen ?? "Plan ready"}</CardTitle>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {exp?.est_cpu_minutes != null && <Badge variant="outline"><Clock />~{exp.est_cpu_minutes} min</Badge>}
            <Badge variant="outline">{preds.length} predictions</Badge>
            {s.problems.length > 0 && <Badge variant="destructive">{s.problems.length} unresolved</Badge>}
          </div>
        </div>
        <div className="flex shrink-0 gap-2">
          <Button variant="outline" onClick={onShowPlan}>Details</Button>
          <Button variant="outline" onClick={() => setOpen(true)} disabled={busy}>Request changes</Button>
          <Button onClick={() => onApprove(true)} disabled={busy}>Approve & run</Button>
        </div>
      </CardHeader>
      {preds.length > 0 && (
        <CardContent>
          <div className="grid gap-1.5">
            {preds.map((p) => (
              <div key={p.hypothesis} className="flex gap-3 text-sm">
                <span className="w-8 shrink-0 font-mono text-xs leading-5 text-muted-foreground">{p.hypothesis}</span>
                <span className="line-clamp-2 text-muted-foreground">{p.prediction}</span>
              </div>
            ))}
          </div>
        </CardContent>
      )}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader><DialogTitle>Request changes</DialogTitle></DialogHeader>
          <Textarea autoFocus rows={5} value={fb} onChange={(e) => setFb(e.target.value)} placeholder="e.g. use 5 seeds, add a no-dropout control" />
          <DialogFooter>
            <Button disabled={!fb.trim()} onClick={() => { onRevise(fb.trim()); setOpen(false); setFb("") }}>Send to agents</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  )
}
