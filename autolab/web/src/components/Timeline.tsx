import { useMemo, useRef } from "react"
import type { Activity } from "@/lib/api"
import { EDGE_COLOR, meta } from "./TeamGraph"

const X0 = 110, X1 = 1000, W = 1010, RH = 22, TOP = 16
const GAP_S = 90, GAP_FRAC = 0.04

/** Piecewise time scale: idle stretches longer than GAP_S (e.g. waiting for approval) collapse to a sliver. */
export function makeScale(A: Activity, now: number) {
  const pts = [...new Set([
    ...A.spans.flatMap((s) => [s.start, s.end ?? now]), ...A.edges.map((e) => e.t), ...A.feed.map((f) => f.t),
  ])].sort((a, b) => a - b)
  if (!pts.length) return null
  const segs: [number, number][] = []
  let s0 = pts[0], prev = pts[0]
  for (const p of pts.slice(1)) { if (p - prev > GAP_S) { segs.push([s0, prev]); s0 = p } prev = p }
  segs.push([s0, prev])
  const active = segs.reduce((a, [s, e]) => a + Math.max(e - s, 1), 0)
  const width = X1 - X0, gapW = width * GAP_FRAC
  const unit = (width - (segs.length - 1) * gapW) / active
  const map: { s: number; e: number; x: number }[] = []
  let x = X0
  segs.forEach(([s, e], i) => { map.push({ s, e, x }); x += Math.max(e - s, 1) * unit; if (i < segs.length - 1) x += gapW })
  const fx = (t: number) => {
    let m = map[0]
    for (const k of map) if (t >= k.s) m = k
    return Math.min(m.x + Math.max(0, Math.min(t, m.e) - m.s) * unit, X1)
  }
  const inv = (px: number) => {
    for (const k of map) {
      const xe = k.x + Math.max(k.e - k.s, 1) * unit
      if (px <= xe + gapW / 2) return px < k.x ? k.s : k.s + (px - k.x) / unit
    }
    return map[map.length - 1].e
  }
  const gaps = segs.slice(1).map((s, i) => ({ x: map[i + 1].x - gapW, w: gapW, dt: s[0] - segs[i][1] }))
  return { fx, inv, map, gaps, t0: pts[0], t1: pts[pts.length - 1] }
}

const hdur = (s: number) => (s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`)
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`

export function Timeline({ A, T, now, onScrub }: { A: Activity; T: number; now: number; onScrub: (t: number) => void }) {
  const sc = useMemo(() => makeScale(A, now), [A, now])
  const ROWS = ["pi", ...A.roles.map((r) => r.id), "sandbox"]
  const svg = useRef<SVGSVGElement>(null)
  const drag = useRef(false)
  if (!sc) return <div className="py-6 text-center text-sm text-muted-foreground">No activity yet</div>
  const H = TOP + ROWS.length * RH + 18
  const toT = (clientX: number) => {
    const r = svg.current!.getBoundingClientRect()
    return sc.inv(((clientX - r.left) * W) / r.width)
  }
  return (
    <svg
      ref={svg} viewBox={`0 0 ${W} ${H}`} className="block w-full min-w-[640px] cursor-col-resize touch-none select-none"
      role="img" aria-label="Agent activity over time"
      onPointerDown={(e) => { drag.current = true; (e.target as Element).setPointerCapture?.(e.pointerId); onScrub(toT(e.clientX)) }}
      onPointerMove={(e) => drag.current && onScrub(toT(e.clientX))}
      onPointerUp={() => { drag.current = false }}
    >
      {ROWS.map((r, i) => (
        <g key={r}>
          <text x={X0 - 10} y={TOP + i * RH + 15} textAnchor="end" className="fill-muted-foreground text-[11px]">{meta(A, r).label}</text>
          <line x1={X0} x2={X1} y1={TOP + (i + 1) * RH} y2={TOP + (i + 1) * RH} className="stroke-border" />
        </g>
      ))}
      {sc.gaps.map((g, i) => (
        <g key={i}>
          <rect x={g.x} y={TOP} width={g.w} height={ROWS.length * RH} className="fill-muted" />
          <text x={g.x + g.w / 2} y={TOP + ROWS.length * RH + 13} textAnchor="middle" className="fill-muted-foreground font-mono text-[10px]">{hdur(g.dt)}</text>
        </g>
      ))}
      {sc.map.map((m, i) => (
        <text key={i} x={m.x + 2} y={TOP - 5} className="fill-muted-foreground font-mono text-[10px]">{mmss(m.s - sc.t0)}</text>
      ))}
      {A.spans.map((sp, i) => {
        const row = ROWS.indexOf(sp.agent)
        if (row < 0) return null
        const a = sc.fx(sp.start), b = sc.fx(sp.end ?? now)
        return <rect key={i} x={a} y={TOP + row * RH + 5} width={Math.max(b - a, 3)} height={RH - 10} rx={3}
          className={sp.end == null ? "fill-blue-500" : sp.agent === "sandbox" ? "fill-orange-400/70" : "fill-blue-500/45"} />
      })}
      {A.edges.map((e, i) => {
        if (!["dispatch", "report", "write", "denied", "web", "exec"].includes(e.kind)) return null
        const row = ROWS.indexOf(e.from)
        if (row < 0) return null
        return <circle key={i} cx={sc.fx(e.t)} cy={TOP + row * RH + RH / 2} r={e.kind === "denied" ? 3.5 : 2.4}
          fill={EDGE_COLOR[e.kind]} className="stroke-background" strokeWidth={1} />
      })}
      <line x1={sc.fx(T)} x2={sc.fx(T)} y1={TOP - 2} y2={TOP + ROWS.length * RH} className="stroke-foreground" strokeWidth={1.5} />
    </svg>
  )
}
