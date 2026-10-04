import { useEffect, useMemo } from "react"
import {
  Background, Controls, Handle, MarkerType, Position, ReactFlow, ReactFlowProvider, useNodesState, useReactFlow,
  type Edge, type Node, type NodeProps,
} from "@xyflow/react"
import "@xyflow/react/dist/style.css"
import {
  BookOpen, Box, Code2, FileText, Gavel, Globe, Lightbulb, Network, ShieldCheck, Sparkles, Terminal, User,
  type LucideIcon,
} from "lucide-react"
import type { Activity, EdgeKind } from "@/lib/api"
import { ktok } from "@/lib/status"
import { cn } from "@/lib/utils"

type Meta = { label: string; sub: string; icon: LucideIcon }
const AUX: Record<string, Meta> = {
  you: { label: "You", sub: "approve", icon: User },
  driver: { label: "Driver", sub: "autolab", icon: Terminal },
  pi: { label: "PI", sub: "orchestrates", icon: Network },
  web: { label: "Web", sub: "search", icon: Globe },
  sandbox: { label: "Sandbox", sub: "runs code", icon: Box },
}
const GROUP_ICON: Record<string, LucideIcon> = {
  scout: BookOpen, theorist: Lightbulb, experimenter: Code2, skeptic: ShieldCheck, judge: Gavel,
}

/** Label, sub-label and icon for any node id (team roles come from the server's layout). */
export function meta(A: Activity | null, id: string): Meta {
  if (AUX[id]) return AUX[id]
  const r = A?.roles.find((x) => x.id === id)
  const group = r?.group ?? id.split("_")[0]
  return { label: r?.label ?? id, sub: r?.sub ?? "", icon: id === "theorist" ? Sparkles : GROUP_ICON[group] ?? Network }
}
export const team = (A: Activity) => ["pi", ...A.roles.map((r) => r.id)]

export const EDGE_COLOR: Record<EdgeKind, string> = {
  dispatch: "#3b82f6", report: "#10b981", write: "#f59e0b", read: "#a1a1aa", denied: "#ef4444",
  web: "#8b5cf6", exec: "#f97316", run: "#f97316", start: "#71717a", approve: "#71717a",
}
const HOT_S = 12
// Fixed sizes so React Flow never has to measure (see the controlled-nodes note in Graph).
const W = 160, H = 84, FILE_W = 152, FILE_H = 28
const COL = 220, ROW = 104, TOP = 170

export type NodeState = { state: string; detail: string }

/** Every node's state at time T: live state from the server, or reconstructed from spans when replaying. */
export function statesAt(A: Activity, T: number, live: boolean): Record<string, NodeState> {
  const ids = [...team(A), "sandbox"]
  const st: Record<string, NodeState> = {}
  for (const id of ids) st[id] = { state: "idle", detail: "" }
  for (const sp of A.spans) {
    if (!st[sp.agent]) continue
    const end = sp.end ?? Infinity
    if (sp.start <= T && T <= end) st[sp.agent] = { state: "working", detail: "" }
    else if (sp.end != null && sp.end < T && st[sp.agent].state !== "working") st[sp.agent] = { state: "done", detail: "" }
  }
  for (const f of A.feed) if (f.t <= T && st[f.agent]?.state === "working") st[f.agent].detail = f.text
  if (live) {
    for (const id of team(A)) { const a = A.agents[id]; if (a) st[id] = { state: a.state, detail: a.detail } }
    if (A.sandbox?.state === "working") st.sandbox = { state: "working", detail: A.sandbox.stdout_tail?.at(-1) ?? "running" }
  }
  if (team(A).slice(1).some((id) => st[id].state === "working") && st.pi.state !== "working") st.pi = { state: "waiting", detail: "waiting for agents" }
  return st
}

function shortFile(p: string, round: string | null) {
  const m = p.match(/^rounds\/(R\d+)\/(.*)$/)
  let rest = m ? m[2] : p
  for (const dir of ["experiment/", "analysis/", "proposals/", "reviews/"]) if (rest.startsWith(dir)) rest = dir
  if (rest.startsWith("output")) rest = "output/"
  if (rest.startsWith("literature/")) rest = "literature/"
  return (m && m[1] !== round ? `${m[1]}/` : "") + rest
}

const hc = "!size-1.5 !min-w-0 !border-0 !bg-transparent"
type AgentData = { id: string; label: string; sub: string; icon: LucideIcon; state: string; detail: string; tokens: number; selected: boolean; team: boolean }
function AgentNode({ data }: NodeProps<Node<AgentData>>) {
  const Icon = data.icon
  const working = data.state === "working"
  return (
    <div className={cn(
      "flex h-[84px] w-40 flex-col rounded-xl border bg-card px-3 py-2 shadow-xs transition-colors",
      working && "border-blue-500 ring-4 ring-blue-500/15",
      data.state === "waiting" && "border-dashed border-blue-400",
      data.state === "stopped" && "border-red-400",
      data.selected && "ring-2 ring-foreground/70",
      !data.team && "bg-muted/40",
      data.team && "cursor-pointer",
    )}>
      <div className="flex items-center gap-2">
        <Icon className={cn("size-4 shrink-0", working ? "text-blue-600 dark:text-blue-400" : "text-muted-foreground")} />
        <span className="truncate text-sm font-medium">{data.label}</span>
        <span className={cn("ml-auto size-2 shrink-0 rounded-full",
          working ? "animate-pulse bg-blue-500" : data.state === "done" ? "bg-emerald-500"
            : data.state === "waiting" ? "bg-blue-300" : data.state === "stopped" ? "bg-red-500" : "bg-muted-foreground/25")} />
      </div>
      <div className="mt-0.5 line-clamp-2 h-8 text-xs leading-4 text-muted-foreground">
        {working || data.state === "waiting" ? data.detail || "working" : data.sub}
      </div>
      <div className="mt-auto h-3 text-right font-mono text-[10px] leading-3 text-muted-foreground">{data.tokens > 0 ? `${ktok(data.tokens)} tok` : ""}</div>
      {/* side handles keep edges in the gaps between columns, so stacked instances don't get crossed */}
      <Handle id="in-top" type="target" position={Position.Top} className={hc} />
      <Handle id="out-top" type="source" position={Position.Top} className={hc} />
      <Handle id="out-bottom" type="source" position={Position.Bottom} style={{ left: "40%" }} className={hc} />
      <Handle id="in-bottom" type="target" position={Position.Bottom} style={{ left: "60%" }} className={hc} />
      <Handle id="in-left" type="target" position={Position.Left} style={{ top: "30%" }} className={hc} />
      <Handle id="in-left2" type="target" position={Position.Left} style={{ top: "70%" }} className={hc} />
      <Handle id="out-left" type="source" position={Position.Left} style={{ top: "50%" }} className={hc} />
      <Handle id="out-right" type="source" position={Position.Right} style={{ top: "30%" }} className={hc} />
      <Handle id="out-right2" type="source" position={Position.Right} style={{ top: "70%" }} className={hc} />
      <Handle id="in-right" type="target" position={Position.Right} style={{ top: "50%" }} className={hc} />
    </div>
  )
}

type FileData = { name: string; hot: boolean }
function FileNode({ data }: NodeProps<Node<FileData>>) {
  return (
    <div className={cn("flex h-7 w-38 items-center gap-1.5 rounded-md border bg-card px-2 font-mono text-xs",
      data.hot ? "border-amber-500 text-foreground" : "text-muted-foreground")}>
      <FileText className="size-3.5 shrink-0" />
      <span className="truncate">{data.name}</span>
      <Handle id="in-top" type="target" position={Position.Top} style={{ left: "35%" }} className={hc} />
      <Handle id="out-top" type="source" position={Position.Top} style={{ left: "65%" }} className={hc} />
    </div>
  )
}
const nodeTypes = { agent: AgentNode, file: FileNode }

function FitOnGrow({ count }: { count: number }) {
  const { fitView } = useReactFlow()
  useEffect(() => { const id = requestAnimationFrame(() => fitView({ padding: 0.1, duration: 250 })); return () => cancelAnimationFrame(id) }, [count, fitView])
  return null
}

export function TeamGraph(props: Parameters<typeof Graph>[0]) {
  return <ReactFlowProvider><Graph {...props} /></ReactFlowProvider>
}

const HANDLES: Partial<Record<EdgeKind, [string, string]>> = {
  dispatch: ["out-bottom", "in-left"], report: ["out-right", "in-bottom"],
  write: ["out-right2", "in-top"], denied: ["out-right2", "in-top"], read: ["out-top", "in-left2"],
  web: ["out-left", "in-right"], exec: ["out-right2", "in-top"],
  start: ["out-right", "in-left"], approve: ["out-bottom", "in-top"], run: ["out-bottom", "in-top"],
}

function Graph({ A, T, live, selected, onSelect }: {
  A: Activity; T: number; live: boolean; selected: string | null; onSelect: (id: string | null) => void
}) {
  const { nodes, edges } = useMemo(() => {
    const st = statesAt(A, T, live)
    const ids = team(A)
    const cols = Math.max(1, ...A.roles.map((r) => r.col + 1))
    const rows = Math.max(1, ...A.roles.map((r) => r.row + 1))
    const pos: Record<string, { x: number; y: number }> = {
      pi: { x: ((cols - 1) * COL) / 2, y: 0 },
      web: { x: -COL, y: TOP },
      you: { x: -2 * COL - 20, y: 0 }, driver: { x: -2 * COL - 20, y: TOP }, sandbox: { x: -2 * COL - 20, y: TOP + 2 * ROW },
    }
    for (const r of A.roles) pos[r.id] = { x: r.col * COL, y: TOP + r.row * ROW }

    const past = A.edges.filter((e) => e.t <= T)
    const fileOf = (id: string) => (id.startsWith("file:") ? shortFile(id.slice(5), A.round) : null)
    const files = [...new Set(past.flatMap((e) => [fileOf(e.from), fileOf(e.to)]).filter(Boolean) as string[])].sort()
    const hotFiles = new Set(past.filter((e) => T - e.t < HOT_S).flatMap((e) => [fileOf(e.from), fileOf(e.to)]))
    const nodes: Node[] = [...ids, "you", "driver", "web", "sandbox"].map((id) => {
      const m = meta(A, id)
      return {
        id, type: "agent", position: pos[id] ?? { x: 0, y: 0 }, draggable: false, selectable: false, width: W, height: H,
        data: {
          id, label: m.label, sub: m.sub, icon: m.icon, state: st[id]?.state ?? "idle", detail: st[id]?.detail ?? "",
          selected: selected === id, team: ids.includes(id) || id === "sandbox",
          tokens: A.agents[id] ? Object.values(A.agents[id].tokens).reduce((a, b) => a + b, 0) : 0,
        },
      }
    })
    const fileTop = TOP + rows * ROW + 40
    files.forEach((f, i) => nodes.push({
      id: `file:${f}`, type: "file", draggable: false, selectable: false, width: FILE_W, height: FILE_H,
      position: { x: (i % 6) * 170, y: fileTop + Math.floor(i / 6) * 44 }, data: { name: f, hot: hotFiles.has(f) },
    }))
    // one edge per (kind, from, to), styled by its most recent use
    const agg = new Map<string, { kind: EdgeKind; from: string; to: string; n: number; last: number }>()
    for (const e of past) {
      const from = e.from.startsWith("file:") ? `file:${fileOf(e.from)}` : e.from
      const to = e.to.startsWith("file:") ? `file:${fileOf(e.to)}` : e.to
      if (to === "files" || !(from in pos || from.startsWith("file:")) || !(to in pos || to.startsWith("file:"))) continue
      const k = `${e.kind}|${from}|${to}`
      const g = agg.get(k) ?? { kind: e.kind, from, to, n: 0, last: 0 }
      g.n++; g.last = Math.max(g.last, e.t); agg.set(k, g)
    }
    const edges: Edge[] = [...agg.values()].map((g) => {
      const hot = T - g.last < HOT_S
      const color = EDGE_COLOR[g.kind]
      const [sh, th] = HANDLES[g.kind] ?? ["out-bottom", "in-top"]
      return {
        id: `${g.kind}|${g.from}|${g.to}`, source: g.from, target: g.to, sourceHandle: sh, targetHandle: th,
        type: "smoothstep", pathOptions: { borderRadius: 10 }, animated: hot, selectable: false, zIndex: hot ? 2 : 0,
        style: { stroke: color, strokeWidth: hot ? 2.5 : 1.25, opacity: hot ? 1 : 0.3, strokeDasharray: g.kind === "read" && !hot ? "4 4" : undefined },
        markerEnd: { type: MarkerType.ArrowClosed, color, width: 14, height: 14 },
        label: g.n > 1 && hot ? `×${g.n}` : undefined,
        labelStyle: { fontSize: 10, fill: color }, labelBgStyle: { fillOpacity: 0.85 },
      }
    })
    return { nodes, edges }
  }, [A, T, live, selected])

  // Controlled nodes: React Flow writes each node's measured size and handle positions back through
  // onNodesChange, and every data update is merged into the existing node so those survive. Passing fresh
  // node objects instead makes React Flow drop the handle positions, and the edges disappear.
  const [rfNodes, setRfNodes, onNodesChange] = useNodesState<Node>([])
  useEffect(() => {
    setRfNodes((prev) => {
      const old = new Map(prev.map((n) => [n.id, n]))
      return nodes.map((n) => { const p = old.get(n.id); return p ? { ...p, ...n, measured: p.measured } : n })
    })
  }, [nodes, setRfNodes])

  const teamIds = useMemo(() => new Set([...team(A), "sandbox"]), [A])
  return (
    <ReactFlow
      nodes={rfNodes} onNodesChange={onNodesChange} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ padding: 0.1 }}
      minZoom={0.2} maxZoom={1.6} proOptions={{ hideAttribution: true }} nodesConnectable={false}
      onNodeClick={(_, n) => { if (teamIds.has(n.id)) onSelect(selected === n.id ? null : n.id) }}
      onPaneClick={() => onSelect(null)}
    >
      <FitOnGrow count={rfNodes.length} />
      <Background gap={20} size={1} />
      <Controls showInteractive={false} position="bottom-right" />
    </ReactFlow>
  )
}
