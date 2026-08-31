import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Loader2, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import {
  videoRuntimeClient,
  type BuildPlanSnapshot,
  type BuildSnapshot,
  type ChangePreview,
  type RuntimeProject,
  type RuntimeBuildStep,
  type RuntimeValidation,
  type RuntimeVersion,
} from "@/features/video-runtime/client";

const starterSpec = JSON.stringify({
  title: "15 秒电影短片", language: "zh-CN", target_duration_seconds: 15,
  aspect_ratio: "16:9", resolution: "1080p", workflow_id: "cuti.seedance-story",
  style_id: "cuti.cinematic",
  characters: [{ id: "hero", name: "主角", appearance: "黑色短发的年轻旅行者", clothing: "蓝色风衣", personality: "沉静", voice: "Wise_Woman" }],
  shots: [
    { id: "01", order: 1, duration_seconds: 5, beat: "主角抵达", visual_prompt: "电影感，主角在雨夜抵达老车站", narration: "他终于抵达了这座车站。", character_ids: ["hero"], transition: "cut" },
    { id: "02", order: 2, duration_seconds: 5, beat: "等待", visual_prompt: "同一角色在站台时钟下等待，服装和面貌保持一致", narration: "时间在雨声中慢慢过去。", character_ids: ["hero"], transition: "cut" },
    { id: "03", order: 3, duration_seconds: 5, beat: "启程", visual_prompt: "同一角色登上驶来的列车，暖光穿过雨幕", narration: "列车终于带他驶向远方。", character_ids: ["hero"], transition: "cut" },
  ],
  audio: { narration_voice: "Wise_Woman", bgm_prompt: "克制、温暖的电影钢琴配乐，无人声", subtitles: true },
  providers: { video: "seedance-2.0", image: "gpt-image-2", music: "suno" },
  automation: { mode: "automatic", max_artifact_retries: 1 },
}, null, 2);

export default function IncrementalVideoWorkspacePage() {
  const { language } = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();
  const { projectId } = useParams<{ projectId?: string }>();
  const automaticBuild = Boolean((location.state as { automaticBuild?: boolean } | null)?.automaticBuild);
  const [project, setProject] = useState<RuntimeProject | null>(null);
  const [versions, setVersions] = useState<RuntimeVersion[]>([]);
  const [title, setTitle] = useState("");
  const [change, setChange] = useState("");
  const [targetIds, setTargetIds] = useState<string[]>([]);
  const [preview, setPreview] = useState<ChangePreview | null>(null);
  const [build, setBuild] = useState<BuildSnapshot | null>(null);
  const [initialPlan, setInitialPlan] = useState<BuildPlanSnapshot | null>(null);
  const [buildSteps, setBuildSteps] = useState<RuntimeBuildStep[]>([]);
  const [validations, setValidations] = useState<RuntimeValidation[]>([]);
  const [videoSpecJson, setVideoSpecJson] = useState(starterSpec);
  const [busy, setBusy] = useState(false);
  const activeBuildId = build?.buildId;
  const activeBuildStatus = build?.status;
  const artifactCount = project?.artifactCount;

  const refresh = useCallback(async () => {
    if (!projectId) return;
    const [nextProject, nextVersions] = await Promise.all([
      videoRuntimeClient.project(projectId), videoRuntimeClient.versions(projectId),
    ]);
    setProject(nextProject);
    setVersions(nextVersions);
    const activeBuildId = nextProject.activeBuildIds.at(-1);
    if (activeBuildId) {
      const [nextBuild, steps] = await Promise.all([
        videoRuntimeClient.build(projectId, activeBuildId),
        videoRuntimeClient.buildSteps(projectId, activeBuildId),
      ]);
      setBuild(nextBuild);
      setBuildSteps(steps);
    }
  }, [projectId]);

  useEffect(() => { void refresh().catch((error: Error) => toast.error(error.message)); }, [refresh]);
  useEffect(() => {
    // DeepSeek creates the Build asynchronously after this page opens. Discover
    // it until either an active Build or committed artifacts become visible.
    if (!projectId || build || (artifactCount !== undefined && artifactCount > 0)) return;
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 2000);
    return () => window.clearInterval(timer);
  }, [artifactCount, build, projectId, refresh]);
  useEffect(() => {
    if (!projectId || !activeBuildId || !activeBuildStatus || !["queued", "running", "waiting_external"].includes(activeBuildStatus)) return;
    const poll = () => Promise.all([
      videoRuntimeClient.build(projectId, activeBuildId),
      videoRuntimeClient.buildSteps(projectId, activeBuildId),
    ]).then(([nextBuild, steps]) => {
      setBuild(nextBuild); setBuildSteps(steps);
      if (["completed", "failed", "cancelled"].includes(nextBuild.status)) {
        void refresh();
        void videoRuntimeClient.buildValidations(projectId, activeBuildId).then(setValidations);
      }
    });
    void poll();
    const timer = window.setInterval(() => void poll(), 2000);
    return () => window.clearInterval(timer);
  }, [activeBuildId, activeBuildStatus, projectId, refresh]);

  const create = async (event: FormEvent) => {
    event.preventDefault();
    if (!title.trim()) return;
    setBusy(true);
    try {
      const next = await videoRuntimeClient.createProject(title.trim());
      navigate(`/${language}/video/${next.projectId}`);
    } catch (error) { toast.error(error instanceof Error ? error.message : "创建失败"); }
    finally { setBusy(false); }
  };

  const runPreview = async () => {
    if (!project || !change.trim()) return;
    setBusy(true);
    try { setPreview(await videoRuntimeClient.preview(project.projectId, change.trim(), targetIds)); }
    catch (error) { toast.error(error instanceof Error ? error.message : "影响分析失败"); }
    finally { setBusy(false); }
  };

  const planInitialBuild = async () => {
    if (!project) return;
    setBusy(true);
    try {
      const parsed = JSON.parse(videoSpecJson) as Record<string, unknown>;
      setInitialPlan(await videoRuntimeClient.plan(project.projectId, project.currentVersionId, parsed));
    } catch (error) { toast.error(error instanceof Error ? error.message : "VideoSpec 校验失败"); }
    finally { setBusy(false); }
  };

  const startInitialBuild = async () => {
    if (!initialPlan) return;
    setBusy(true);
    try { setBuild(await videoRuntimeClient.startBuild(initialPlan)); setBuildSteps([]); }
    catch (error) { toast.error(error instanceof Error ? error.message : "构建启动失败"); }
    finally { setBusy(false); }
  };

  const buckets = useMemo(() => preview ? [
    ["保留", preview.reusedArtifactIds, "text-emerald-300"],
    ["验证", preview.validationArtifactIds, "text-amber-300"],
    ["重建", preview.staleArtifactIds, "text-red-300"],
  ] as const : [], [preview]);

  if (!projectId) return <main className="grid min-h-screen place-items-center bg-zinc-950 text-zinc-100">
    <form onSubmit={create} className="w-full max-w-lg rounded-2xl border border-white/10 bg-white/[0.03] p-8">
      <p className="text-xs uppercase tracking-[0.25em] text-violet-300">Video Agent Harness</p>
      <h1 className="mt-2 text-2xl font-semibold">创建长期视频项目</h1>
      <input className="mt-6 h-11 w-full rounded-lg border border-white/10 bg-black/30 px-3" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="项目名称" />
      <Button className="mt-4" disabled={busy}>{busy && <Loader2 className="animate-spin" />}创建</Button>
    </form>
  </main>;

  return <main className="min-h-screen bg-zinc-950 p-5 text-zinc-100">
    <header className="mb-5 flex items-center justify-between">
      <div><p className="text-xs uppercase tracking-[0.2em] text-violet-300">Incremental Video Build</p><h1 className="text-xl font-semibold">{project?.title || "加载中…"}</h1><p className="text-xs text-zinc-400">当前版本 {project?.currentVersionId}</p></div>
      <div className="flex gap-2"><Button variant="outline" asChild><Link to={`/${language}/create`}>Agent 对话</Link></Button><Button variant="outline" onClick={() => void refresh()}><RefreshCw className="h-4 w-4" />刷新</Button></div>
    </header>
    <div className="grid gap-4 lg:grid-cols-[1.2fr_1fr]">
      <div className="space-y-4">
      {project && project.artifactCount === 0 && <section className="rounded-xl border border-violet-400/30 bg-violet-400/[0.04] p-4">
        <h2 className="font-medium">从零制作完整视频</h2>
        <p className="mt-1 text-xs text-zinc-400">{automaticBuild && !build ? "DeepSeek 正在生成 VideoSpec，并将自动完成计划与构建，无需再次提交。" : "填写或修改 VideoSpec；系统将自动生成角色图、3 个连续镜头、旁白、BGM、字幕和最终 MP4。"}</p>
        <textarea className="mt-3 min-h-80 w-full rounded-lg border border-white/10 bg-black/30 p-3 font-mono text-xs" value={videoSpecJson} onChange={(event) => setVideoSpecJson(event.target.value)} />
        <div className="mt-3 flex items-center justify-between">
          <span className="text-sm text-zinc-400">{initialPlan ? `${initialPlan.shotCount} 镜头 · 预计 $${initialPlan.estimatedCost}` : "先校验并生成计划"}</span>
          <div className="flex gap-2"><Button variant="outline" disabled={busy} onClick={() => void planInitialBuild()}>生成计划</Button><Button disabled={busy || !initialPlan} onClick={() => void startInitialBuild()}>全自动制作</Button></div>
        </div>
      </section>}
      <section className="rounded-xl border border-white/10 p-4">
        <h2 className="font-medium">修改影响预览</h2>
        <textarea className="mt-3 min-h-24 w-full rounded-lg border border-white/10 bg-black/30 p-3" value={change} onChange={(e) => setChange(e.target.value)} placeholder="例如：把第 3 镜头角色服装改成红色，其他镜头保持不变" />
        <p className="mt-3 text-xs text-zinc-400">选择发生变化的产物</p>
        <div className="mt-2 grid gap-2 sm:grid-cols-2">{project?.artifacts.map((artifact) => <label key={artifact.id} className="flex gap-2 rounded border border-white/10 p-2 text-sm"><input type="checkbox" checked={targetIds.includes(artifact.id)} onChange={(e) => setTargetIds((ids) => e.target.checked ? [...ids, artifact.id] : ids.filter((id) => id !== artifact.id))} />{artifact.title || artifact.type} v{artifact.version}</label>)}</div>
        <Button className="mt-4" disabled={busy || targetIds.length === 0} onClick={() => void runPreview()}>分析影响</Button>
        {preview && <div className="mt-4 grid gap-3 sm:grid-cols-3">{buckets.map(([label, ids, color]) => <div key={label} className="rounded-lg bg-white/[0.04] p-3"><p className={color}>{label} · {ids.length}</p><p className="mt-2 break-all text-xs text-zinc-400">{ids.join("\n") || "无"}</p></div>)}</div>}
        {preview && <div className="mt-4 flex items-center justify-between rounded-lg border border-violet-400/20 p-3"><span className="text-sm">预计成本：{preview.estimatedCost}</span><Button onClick={() => void videoRuntimeClient.rebuild(preview, crypto.randomUUID()).then(setBuild).catch((error: Error) => toast.error(error.message))}>确认局部重建</Button></div>}
        {build && <div className="mt-3 rounded-lg border border-white/10 p-3 text-sm"><p>Build {build.buildId}: {build.status} · {Math.round(build.progress * 100)}%</p><p className="text-zinc-400">{build.message}</p>{build.estimatedCost !== undefined && <p className="mt-1 text-xs text-zinc-500">预计 ${build.estimatedCost} · 实际 ${build.actualCost || 0}</p>}{build.error && <p className="mt-1 text-red-300">{build.error}</p>}<div className="mt-3 space-y-1">{buildSteps.map((step) => <div key={step.id} className="flex justify-between rounded bg-white/[0.04] px-2 py-1 text-xs"><span>{step.plan_step_id}</span><span className={step.status === "completed" ? "text-emerald-300" : step.status === "failed" ? "text-red-300" : "text-zinc-400"}>{step.status} · {step.attempt}</span></div>)}</div>{validations.length > 0 && <div className="mt-3 rounded bg-white/[0.04] p-2 text-xs"><p>连续性与质量检查</p>{validations.map((item) => <p key={item.id} className={item.passed ? "text-emerald-300" : "text-red-300"}>{item.validator_id}: {item.passed ? "通过" : item.issues.join("；")}</p>)}</div>}{["queued", "running", "waiting_external"].includes(build.status) && <Button variant="destructive" size="sm" className="mt-2" onClick={() => void videoRuntimeClient.cancel(build.projectId, build.buildId).then(setBuild)}>取消</Button>}</div>}
      </section>
      </div>
      <div className="space-y-4">
        <section className="rounded-xl border border-white/10 p-4"><h2 className="font-medium">媒体产物</h2><div className="mt-3 grid gap-3">{project?.artifacts.filter((artifact) => artifact.uri).map((artifact) => <div key={artifact.id} className="rounded bg-white/[0.04] p-2"><p className="mb-2 text-xs text-zinc-400">{artifact.title || artifact.type} · v{artifact.version}</p>{artifact.type.includes("video") ? <video className="w-full rounded" src={artifact.uri} controls preload="metadata" /> : artifact.type.includes("image") || artifact.type.includes("frame") ? <img className="w-full rounded" src={artifact.uri} alt={artifact.title || artifact.type} /> : artifact.type.includes("audio") ? <audio className="w-full" src={artifact.uri} controls preload="metadata" /> : <a className="text-xs text-violet-300 underline" href={artifact.uri} target="_blank" rel="noreferrer">打开产物</a>}</div>)}</div></section>
        <section className="rounded-xl border border-white/10 p-4"><h2 className="font-medium">Artifact 依赖</h2><div className="mt-3 space-y-2">{project?.artifactEdges.map((edge) => <div key={edge.id} className="rounded bg-white/[0.04] p-2 text-xs"><span className="text-violet-300">{edge.invalidation_policy}</span> · {edge.source_version_id.slice(0, 8)} → {edge.target_version_id.slice(0, 8)}</div>)}</div></section>
        <section className="rounded-xl border border-white/10 p-4"><h2 className="font-medium">作品版本</h2><div className="mt-3 space-y-2">{versions.map((version) => <div key={version.id} className="flex items-center justify-between rounded bg-white/[0.04] p-2 text-xs"><div><p>{version.id}</p><p className="text-zinc-500">{new Date(version.createdAt).toLocaleString()}</p></div>{version.isCurrent ? <span className="text-emerald-300">当前</span> : <Button size="sm" variant="outline" onClick={() => project && void videoRuntimeClient.restore(project.projectId, version.id, project.currentVersionId).then(() => refresh())}>恢复</Button>}</div>)}</div></section>
      </div>
    </div>
  </main>;
}
