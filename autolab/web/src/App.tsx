import { useCallback, useEffect, useState } from "react"
import { Box, FlaskConical, Plus, ShieldCheck, Workflow, Zap } from "lucide-react"
import { api, type Env, type StudySummary } from "@/lib/api"
import { STATUS, ago } from "@/lib/status"
import {
  Sidebar, SidebarContent, SidebarFooter, SidebarGroup, SidebarGroupLabel, SidebarHeader, SidebarInset,
  SidebarMenu, SidebarMenuButton, SidebarMenuItem, SidebarProvider, SidebarTrigger,
} from "@/components/ui/sidebar"
import { Button } from "@/components/ui/button"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { NewStudyDialog } from "@/components/NewStudyDialog"
import { StudyPage } from "@/pages/StudyPage"
import { CachewPage, LabPage } from "@/pages/ToolPages"
import { cn } from "@/lib/utils"

function useHash() {
  const [h, setH] = useState(location.hash)
  useEffect(() => { const f = () => setH(location.hash); addEventListener("hashchange", f); return () => removeEventListener("hashchange", f) }, [])
  return h
}

const dot: Record<string, string> = {
  busy: "bg-blue-500 animate-pulse", you: "bg-amber-500", bad: "bg-red-500", good: "bg-emerald-500", idle: "bg-muted-foreground/30",
}

export default function App() {
  const hash = useHash()
  const [studies, setStudies] = useState<StudySummary[]>([])
  const [env, setEnv] = useState<Env | null>(null)
  const [newOpen, setNewOpen] = useState(false)
  const refresh = useCallback(() => { api.studies().then(setStudies).catch(() => {}) }, [])
  useEffect(() => { refresh(); api.env().then(setEnv).catch(() => {}); const id = setInterval(refresh, 5000); return () => clearInterval(id) }, [refresh])
  useEffect(() => { if (!hash && studies.length) location.hash = `#/s/${encodeURIComponent(studies[0].name)}` }, [hash, studies])

  const current = hash.startsWith("#/s/") ? decodeURIComponent(hash.slice(4)) : null
  return (
    <TooltipProvider>
      <SidebarProvider>
        <Sidebar>
          <SidebarHeader>
            <div className="flex items-center gap-2 px-2 py-1">
              <FlaskConical className="size-5" /><span className="font-semibold">Autolab</span>
              <Button size="icon-sm" variant="ghost" className="ml-auto" onClick={() => setNewOpen(true)} aria-label="New study"><Plus /></Button>
            </div>
          </SidebarHeader>
          <SidebarContent>
            <SidebarGroup>
              <SidebarGroupLabel>Studies</SidebarGroupLabel>
              <SidebarMenu>
                {studies.map((s) => {
                  const tone = s.busy ? "busy" : STATUS[s.status].tone
                  return (
                    <SidebarMenuItem key={s.name}>
                      <SidebarMenuButton isActive={current === s.name} className="h-auto items-start py-2"
                        render={<a href={`#/s/${encodeURIComponent(s.name)}`} />}>
                        <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", dot[tone])} />
                        <span className="flex min-w-0 flex-col">
                          <span className="line-clamp-2 text-sm leading-snug">{s.question}</span>
                          <span className="text-xs text-muted-foreground">{s.busy ? "Working" : STATUS[s.status].label} · {ago(s.updated)}</span>
                        </span>
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  )
                })}
                {!studies.length && (
                  <SidebarMenuItem><SidebarMenuButton onClick={() => setNewOpen(true)}><Plus />New study</SidebarMenuButton></SidebarMenuItem>
                )}
              </SidebarMenu>
            </SidebarGroup>
            <SidebarGroup>
              <SidebarGroupLabel>Tools</SidebarGroupLabel>
              <SidebarMenu>
                <SidebarMenuItem><SidebarMenuButton isActive={hash === "#/lab"} render={<a href="#/lab" />}><Workflow />Fan-out loop</SidebarMenuButton></SidebarMenuItem>
                <SidebarMenuItem><SidebarMenuButton isActive={hash === "#/cachew"} render={<a href="#/cachew" />}><Zap />Cachew</SidebarMenuButton></SidebarMenuItem>
              </SidebarMenu>
            </SidebarGroup>
          </SidebarContent>
          {env && (
            <SidebarFooter>
              <div className="flex flex-col gap-1 px-2 pb-1 text-xs text-muted-foreground">
                <span className="flex items-center gap-1.5"><ShieldCheck className="size-3.5" />{env.api_key ? "API key" : env.claude_login ? "Claude login" : "No credentials"}</span>
                <span className="flex items-center gap-1.5"><Box className="size-3.5" />{env.sandbox ? "Sandboxed" : "No sandbox"} · {env.cpus} CPUs</span>
              </div>
            </SidebarFooter>
          )}
        </Sidebar>
        <SidebarInset>
          <div className="flex h-10 items-center border-b px-2 md:hidden"><SidebarTrigger /></div>
          {current ? <StudyPage key={current} name={current} onChanged={refresh} />
            : hash === "#/lab" ? <LabPage />
              : hash === "#/cachew" ? <CachewPage />
                : (
                  <div className="flex h-full flex-col items-center justify-center gap-3 p-8">
                    <FlaskConical className="size-8 text-muted-foreground" />
                    <Button onClick={() => setNewOpen(true)}><Plus />New study</Button>
                  </div>
                )}
        </SidebarInset>
        <NewStudyDialog open={newOpen} onOpenChange={setNewOpen} existing={studies.map((s) => s.name)}
          onCreated={(n) => { refresh(); location.hash = `#/s/${encodeURIComponent(n)}` }} />
        <Toaster position="bottom-right" />
      </SidebarProvider>
    </TooltipProvider>
  )
}
