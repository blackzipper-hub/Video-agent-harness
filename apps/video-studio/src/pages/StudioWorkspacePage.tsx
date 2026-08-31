import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Loader2, Plus, RefreshCw, Send, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import { studioClient, type StudioPlanItem, type StudioSnapshot } from "@/features/studio/client";

const starterPrompts = [
  "先生成一张女主概念图",
  "根据这张图写一个十秒样片故事",
  "先分析一段音乐的节拍和情绪",
];

const statusClass = (status = "proposed") => ({
  succeeded: "bg-emerald-500/15 text-emerald-300",
  running: "bg-sky-500/15 text-sky-300",
  failed: "bg-red-500/15 text-red-300",
  cancelled: "bg-zinc-500/15 text-zinc-300",
}[status] || "bg-amber-500/15 text-amber-300");

export default function StudioWorkspacePage() {
  const { language } = useLanguage();
  const { threadId: routeThreadId } = useParams<{ threadId?: string }>();
  const navigate = useNavigate();
  const [threadId, setThreadId] = useState(routeThreadId || "");
  const [snapshot, setSnapshot] = useState<StudioSnapshot | null>(null);
  const [skills, setSkills] = useState<Array<Record<string, unknown>>>([]);
  const [objective, setObjective] = useState("");
  const [command, setCommand] = useState("");
  const [plan, setPlan] = useState<StudioPlanItem[]>([]);
  const [loading, setLoading] = useState(Boolean(routeThreadId));
  const [submitting, setSubmitting] = useState(false);

  const workspacePath = useCallback((id?: string) => `/${language}/studio${id ? `/${encodeURIComponent(id)}` : ""}`, [language]);
  const refresh = useCallback(async (id = threadId) => {
    if (!id) return;
    setLoading(true);
    try {
      setSnapshot(await studioClient.getProject(id));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "无法读取 Studio 项目");
    } finally {
      setLoading(false);
    }
  }, [threadId]);

  useEffect(() => { void studioClient.listSkills().then(setSkills).catch(() => undefined); }, []);
  useEffect(() => { if (routeThreadId) { setThreadId(routeThreadId); void refresh(routeThreadId); } }, [routeThreadId, refresh]);

  const createProject = async (event: FormEvent) => {
    event.preventDefault();
    if (!objective.trim()) return;
    setSubmitting(true);
    try {
      const result = await studioClient.createProject(objective.trim());
      const nextId = result.project.thread_id;
      setThreadId(nextId);
      setPlan(result.suggested_plan);
      setObjective("");
      navigate(workspacePath(nextId));
      await refresh(nextId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "项目创建失败");
    } finally { setSubmitting(false); }
  };

  const submitCommand = async (event: FormEvent) => {
    event.preventDefault();
    if (!threadId || !command.trim()) return;
    setSubmitting(true);
    try {
      const result = await studioClient.sendCommand(threadId, command.trim());
      setPlan(result.suggested_plan);
      setCommand("");
      await refresh(threadId);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "命令提交失败");
    } finally { setSubmitting(false); }
  };

  const tasks = snapshot?.tasks || [];
  const artifacts = snapshot?.artifacts || [];
  const selected = useMemo(() => new Set((snapshot?.selections || []).map((item) => item.artifact_version_id)), [snapshot]);

  return <main className="min-h-screen bg-zinc-950 text-zinc-100">
    <header className="flex items-center justify-between border-b border-white/10 px-5 py-4">
      <div><p className="text-xs uppercase tracking-[0.25em] text-violet-300">Studio Beta</p><h1 className="text-xl font-semibold">动态视频项目工作台</h1></div>
      <div className="flex gap-2"><Button variant="outline" size="sm" asChild><Link to={`/${language}/create/legacy`}>旧创建页</Link></Button><Button variant="outline" size="sm" onClick={() => void refresh()} disabled={!threadId || loading}><RefreshCw className="h-4 w-4" />刷新</Button></div>
    </header>
    <div className="grid gap-4 p-5 lg:grid-cols-[1.1fr_1fr_1fr]">
      <section className="rounded-xl border border-white/10 bg-white/[0.03] p-4 lg:col-span-3">
        {!threadId ? <form onSubmit={createProject} className="space-y-3"><h2 className="font-medium">从任意入口开始</h2><p className="text-sm text-zinc-400">故事、图片、音乐或已有素材都可以成为第一个资产。</p><textarea value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="例如：先为悬疑短剧生成女主概念图" className="min-h-24 w-full rounded-lg border border-white/10 bg-black/30 p-3 outline-none focus:border-violet-400" /><Button disabled={submitting}>{submitting ? <Loader2 className="animate-spin" /> : <Plus />}创建 Studio 项目</Button></form> : <form onSubmit={submitCommand} className="flex gap-2"><input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="改变方向：不要这首音乐；先做一个十秒样片…" className="h-10 min-w-0 flex-1 rounded-lg border border-white/10 bg-black/30 px-3 outline-none focus:border-violet-400" /><Button disabled={submitting}>{submitting ? <Loader2 className="animate-spin" /> : <Send />}发送</Button></form>}
        {!threadId && <div className="mt-3 flex flex-wrap gap-2">{starterPrompts.map((prompt) => <button type="button" key={prompt} onClick={() => setObjective(prompt)} className="rounded-full border border-white/10 px-3 py-1 text-xs text-zinc-300 hover:border-violet-400">{prompt}</button>)}</div>}
      </section>
      <Panel title={`任务图 · ${tasks.length}`} empty={loading ? "正在读取项目…" : "创建项目后将显示可并行和依赖任务。"}>{tasks.map((task) => <div key={task.id} className="rounded-lg border border-white/10 p-3"><div className="flex justify-between gap-2"><p className="text-sm font-medium">{task.objective || task.capability_id || task.id}</p><span className={`rounded px-2 py-0.5 text-xs ${statusClass(task.status)}`}>{task.status || "proposed"}</span></div>{task.depends_on?.length ? <p className="mt-2 text-xs text-zinc-400">依赖：{task.depends_on.join("、")}</p> : null}</div>)}</Panel>
      <Panel title={`资产 DAG · ${artifacts.length}`} empty="生成或上传资产后，这里会展示版本、选择状态及其下游影响。">{artifacts.map((artifact) => <button key={artifact.id} onClick={() => threadId && void studioClient.selectArtifact(threadId, artifact.id).then(() => refresh()).catch((error: Error) => toast.error(error.message))} className="w-full rounded-lg border border-white/10 p-3 text-left hover:border-violet-400"><div className="flex justify-between"><p className="text-sm">{artifact.artifact_type || "artifact"} v{artifact.version || 1}</p>{selected.has(artifact.id) ? <span className="text-xs text-violet-300">已选择</span> : null}</div><p className="mt-1 text-xs text-zinc-400">{artifact.status || "draft"}</p></button>)}</Panel>
      <Panel title={`Skill · ${skills.length}`} empty="未发现启用的 Skill。">{skills.map((skill, index) => <div key={String(skill.id || index)} className="rounded-lg border border-white/10 p-3 text-sm"><Sparkles className="mr-2 inline h-4 w-4 text-violet-300" />{String(skill.name || skill.id || "Skill")}</div>)}</Panel>
      {plan.length > 0 && <section className="rounded-xl border border-violet-400/30 bg-violet-500/5 p-4 lg:col-span-3"><h2 className="mb-2 text-sm font-medium text-violet-200">Planner 建议的任务图变更</h2><div className="grid gap-2 md:grid-cols-3">{plan.map((item, index) => <div key={`${item.task_id || item.objective}-${index}`} className="rounded-lg bg-black/20 p-3 text-xs"><p>{item.operation || "add_task"}</p><p className="mt-1 text-zinc-300">{item.objective || item.capability_id || item.task_id}</p></div>)}</div></section>}
    </div>
  </main>;
}

function Panel({ title, empty, children }: { title: string; empty: string; children: React.ReactNode }) {
  const hasChildren = Array.isArray(children) && children.length > 0;
  return <section className="min-h-64 rounded-xl border border-white/10 bg-white/[0.03] p-4"><h2 className="mb-3 font-medium">{title}</h2><div className="space-y-2">{hasChildren ? children : <p className="pt-8 text-sm text-zinc-500">{empty}</p>}</div></section>;
}
