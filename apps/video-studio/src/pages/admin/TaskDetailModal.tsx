import { useEffect, useState } from 'react';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Loader2, CheckCircle, AlertCircle, Clock, Video, Image, Music, Users, FileText, RefreshCw, ChevronRight, Copy } from "lucide-react";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Label } from "@/components/ui/label";
import { useToast } from "@/hooks/use-toast";
import { api, CUTI_VIDEO_API_BASE_URL } from "@/services/api";
import { LazyVideo } from "@/components/admin/LazyVideo";

/** 可复制的 ID 展示（Run ID / Thread ID / 用户ID） */
function CopyableId({ value }: { value: string | null | undefined }) {
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);
  if (value == null || value === '') return <span className="text-sm text-muted-foreground">—</span>;
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <span className="font-mono text-sm break-all select-all flex-1 min-w-0">{value}</span>
      <button
        type="button"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
            toast({ title: '已复制到剪贴板' });
            setTimeout(() => setCopied(false), 2000);
          } catch {
            toast({ title: '复制失败', variant: 'destructive' });
          }
        }}
        className="shrink-0 p-1 rounded hover:bg-muted"
        title="复制"
      >
        {copied ? <CheckCircle className="h-4 w-4 text-green-600" /> : <Copy className="h-4 w-4 text-muted-foreground" />}
      </button>
    </div>
  );
}

const GENERATION_ROUTING_FIELD_ORDER = [
  'recommended_generation_mode',
  'image_generation_tool',
  'normal_video_tool',
  'lipsync_video_tool',
  'rationale',
] as const;

/** 与用户端分镜/镜头 LazyShotsSection 一致：枚举值 → 中文展示 */
function formatGenerationModeLabelZh(mode: string | null | undefined): string {
  if (mode == null || mode === '') return '—';
  const m = String(mode).toLowerCase();
  if (m === 'normal') return '普通';
  if (m === 'lipsync') return '口型同步';
  if (m === 'empty_shot') return '空镜';
  return String(mode);
}

const GENERATION_ROUTING_FIELD_LABELS: Record<(typeof GENERATION_ROUTING_FIELD_ORDER)[number], string> = {
  recommended_generation_mode: '建议镜头路径',
  image_generation_tool: '静图候选',
  normal_video_tool: '普通视频候选',
  lipsync_video_tool: '口型视频候选',
  rationale: '说明',
};

/** Admin：生成模式（与分镜一致中文）+ 候选模型（per-shot routing JSON） */
function ShotModeAndRoutingPanel({ shot }: { shot: any }) {
  const gm = shot?.generation_mode;
  const routing = shot?.generation_routing;
  const showRouting = routing != null && typeof routing === 'object';
  const r = showRouting ? (routing as Record<string, unknown>) : null;
  return (
    <div className="space-y-2 w-full">
      <div className="rounded-lg border-2 border-primary bg-primary/10 px-3 py-2 shadow-sm dark:border-primary/80 dark:bg-primary/15">
        <div className="text-[10px] font-bold uppercase tracking-wider text-primary mb-0.5">生成模式</div>
        <div className="text-base font-extrabold text-primary tracking-tight">
          {formatGenerationModeLabelZh(gm)}
        </div>
      </div>
      <div className="rounded-lg border border-dashed border-muted-foreground/40 bg-muted/30 px-2 py-1.5">
        <div className="text-[10px] font-semibold text-muted-foreground mb-1">候选模型（per-shot）</div>
        {!showRouting ? (
          <span className="text-xs text-muted-foreground">暂无候选模型（未跑路由或未落库）</span>
        ) : (
          <div className="space-y-2">
            <div className="grid gap-1 text-xs">
              {GENERATION_ROUTING_FIELD_ORDER.map((key) => {
                const v = r![key];
                if (v === undefined || v === null || v === '') return null;
                const display =
                  key === 'recommended_generation_mode'
                    ? formatGenerationModeLabelZh(typeof v === 'string' ? v : String(v))
                    : typeof v === 'object'
                      ? JSON.stringify(v)
                      : String(v);
                return (
                  <div key={key} className="flex flex-wrap gap-x-2 gap-y-0.5">
                    <span className="text-violet-800 dark:text-violet-200 shrink-0 font-medium">{GENERATION_ROUTING_FIELD_LABELS[key]}</span>
                    <span className="break-words text-foreground font-mono">{display}</span>
                  </div>
                );
              })}
            </div>
            <pre className="text-[11px] font-mono px-1 pb-1 whitespace-pre-wrap break-words overflow-x-auto max-h-40 overflow-y-auto leading-relaxed border-t border-violet-500/20 pt-2 text-muted-foreground">
              {Object.keys(r!).length === 0 ? '{}' : JSON.stringify(r, null, 2)}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

interface TaskDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  taskId: string | null;
  /** 可选：直接传入任务详情数据（如用户端 conversation/task-detail 返回），有则不再请求 admin 接口 */
  initialTaskData?: any | null;
}

export default function TaskDetailModal({ isOpen, onClose, taskId, initialTaskData }: TaskDetailModalProps) {
  const { toast } = useToast();
  const [loading, setLoading] = useState(false);
  const [taskData, setTaskData] = useState<any>(null);
  const [regeneratingVideos, setRegeneratingVideos] = useState(false);
  const [recalculatingSummary, setRecalculatingSummary] = useState(false);
  const [selectedVideoVersion, setSelectedVideoVersion] = useState<Record<string, string>>({});
  /** 加入一致性测试集弹窗：type=image|video, versionUuid, shotNumber, 选中的 datasetId、prompt_source */
  const [addToConsistencyOpen, setAddToConsistencyOpen] = useState(false);
  const [addToConsistencyType, setAddToConsistencyType] = useState<'image' | 'video'>('image');
  const [addToConsistencyVersionUuid, setAddToConsistencyVersionUuid] = useState<string | null>(null);
  const [addToConsistencyShotNumber, setAddToConsistencyShotNumber] = useState<number>(1);
  const [addToConsistencyDatasetId, setAddToConsistencyDatasetId] = useState<string>('');
  const [addToConsistencyPromptSource, setAddToConsistencyPromptSource] = useState<string>('attempt_1');
  const [addToConsistencyDatasets, setAddToConsistencyDatasets] = useState<Array<{ dataset_id: string; name: string }>>([]);
  const [addToConsistencySubmitting, setAddToConsistencySubmitting] = useState(false);

  useEffect(() => {
    if (isOpen && taskId) {
      setSelectedVideoVersion({});
      if (initialTaskData != null) {
        setTaskData(initialTaskData);
      } else {
        loadTaskDetail();
      }
    }
  }, [isOpen, taskId, initialTaskData]);

  useEffect(() => {
    if (addToConsistencyOpen && addToConsistencyType) {
      api.admin.listConsistencyDatasets(addToConsistencyType).then((res) => {
        if (res.code === 0 && res.data) {
          setAddToConsistencyDatasets(res.data.map((d: any) => ({ dataset_id: d.dataset_id, name: d.name })));
          setAddToConsistencyDatasetId(res.data[0]?.dataset_id ?? '');
        }
      }).catch(() => toast({ title: '获取测试集列表失败', variant: 'destructive' }));
    }
  }, [addToConsistencyOpen, addToConsistencyType]);

  const loadTaskDetail = async () => {
    if (!taskId) return;
    
    setLoading(true);
    try {
      const currentLang = localStorage.getItem('language') || 'en';
      const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/admin/error-tracking/tasks/${taskId}/full`, {
        credentials: 'include',
        headers: {
          'X-App-Language': currentLang,
        },
      });
      
      if (!response.ok) throw new Error('Failed to load task detail');
      
      const result = await response.json();
      if (result.code === 0) {
        setTaskData(result.data);
      }
    } catch (error) {
      console.error('Failed to load task detail:', error);
      toast({
        title: "加载失败",
        description: "无法加载任务详情",
        variant: "destructive",
      });
    } finally {
      setLoading(false);
    }
  };

  const recalculateConsistencySummary = async () => {
    if (!taskId) return;
    setRecalculatingSummary(true);
    try {
      const response = await fetch(`${CUTI_VIDEO_API_BASE_URL}/admin/error-tracking/tasks/${taskId}/recalculate-consistency-summary`, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      const result = await response.json();
      if (result.code === 0 && result.data?.tool_consistency_summary != null) {
        setTaskData((prev: any) => (prev ? { ...prev, tool_consistency_summary: result.data.tool_consistency_summary } : prev));
        toast({ title: '已重新统计一致性汇总' });
      } else {
        throw new Error(result.message || '重新统计失败');
      }
    } catch (e: any) {
      toast({ title: e?.message || '重新统计失败', variant: 'destructive' });
    } finally {
      setRecalculatingSummary(false);
    }
  };

    // 从 characters_data 构建 character_version_id -> { name, version_number } 映射，用于关键帧依赖展示
  const characterVersionMap = (() => {
    const map: Record<string, { name: string; version_number: number }> = {};
    if (!taskData?.characters_data) return map;
    for (const char of taskData.characters_data) {
      for (const ver of char.versions || []) {
        if (ver.uuid) map[ver.uuid] = { name: char.name || char.uuid, version_number: ver.version_number ?? 0 };
      }
    }
    return map;
  })();
  // 角色 uuid -> { name, image_url }，用于场景/镜头中的 character_ids 展示为名称+头像
  const characterMap = (() => {
    const map: Record<string, { name: string; image_url?: string }> = {};
    if (!taskData?.characters_data) return map;
    for (const char of taskData.characters_data) {
      if (char.uuid) map[char.uuid] = { name: char.name || char.uuid, image_url: char.image_url };
    }
    return map;
  })();

  /** 单行展示：表名 + 可单独选中的 UUID，便于复制 */
  const UuidRow = ({ table, uuid }: { table: string; uuid: string }) => (
    <div className="flex items-baseline gap-2 flex-wrap">
      <span className="text-muted-foreground shrink-0">{table}:</span>
      <span className="font-mono text-xs break-all select-text" title={uuid}>{uuid}</span>
    </div>
  );

  /** 音乐线等：单字段展示，对象/数组用格式化 JSON 块 */
  const FieldOrJson = ({ label, value }: { label: string; value: unknown }) => {
    if (value == null || value === '') return null;
    const isObj = typeof value === 'object';
    return (
      <div className="space-y-1">
        {label ? <div className="text-sm font-semibold text-muted-foreground">{label}</div> : null}
        {isObj ? (
          <pre className="text-xs bg-muted/50 p-3 rounded-md overflow-auto max-w-full whitespace-pre-wrap break-words">
            {JSON.stringify(value, null, 2)}
          </pre>
        ) : (
          <div className="text-sm break-words">{String(value)}</div>
        )}
      </div>
    );
  };

  /** 从 version 取耗时（优先 version，其次 metrics 内） */
  const getDurationSec = (version: { tool_duration_sec?: number | null; video_tool_metrics?: Record<string, unknown>; image_tool_metrics?: Record<string, unknown> } | null) => {
    if (!version) return null;
    const v = version.tool_duration_sec ?? (version.video_tool_metrics ?? version.image_tool_metrics)?.tool_duration_sec;
    return v != null ? Number(v) : null;
  };
  /** 从 version 取成本（优先 version，其次 metrics 内） */
  const getCost = (version: { tool_cost?: number | null; video_tool_metrics?: Record<string, unknown>; image_tool_metrics?: Record<string, unknown> } | null) => {
    if (!version) return null;
    const v = version.tool_cost ?? (version.video_tool_metrics ?? version.image_tool_metrics)?.tool_cost;
    return v != null ? Number(v) : null;
  };

  /** 多维度一致性展示：优先使用后端生成的 consistency_display_lines；按「尝试 N」分块展示，key 用不同样式，建议 prompt 不截断 */
  const ConsistencyBlock = ({
    metrics,
    consistencyDisplayLines,
  }: {
    metrics?: Record<string, unknown> | null;
    consistencyDisplayLines?: string[] | null;
    isVideo?: boolean;
  }) => {
    if (Array.isArray(consistencyDisplayLines) && consistencyDisplayLines.length > 0) {
      const blocks: string[][] = [];
      let current: string[] = [];
      for (const line of consistencyDisplayLines) {
        if (line.startsWith('尝试 ')) {
          if (current.length) blocks.push(current);
          current = [line];
        } else {
          current.push(line);
        }
      }
      if (current.length) blocks.push(current);
      return (
        <div className="rounded bg-muted/40 p-1.5 space-y-3 text-[11px] font-mono">
          {blocks.map((block, bi) => (
            <div key={bi} className="space-y-0.5 border-b border-border/50 pb-2 last:border-0 last:pb-0">
              {block.map((line, i) => {
                const isAttemptTitle = line.startsWith('尝试 ');
                const match = line.match(/^(\s*)([^:]+):\s*(.*)$/);
                if (isAttemptTitle) {
                  return <div key={i} className="leading-relaxed font-semibold text-foreground">{line}</div>;
                }
                if (match) {
                  const [, indent, label, value] = match;
                  const labelKey = (label || '').trim();
                  const url = (value || '').trim();
                  const isImageLine = labelKey === '当次图片' && url.startsWith('http');
                  const isVideoLine = labelKey === '当次视频' && url.startsWith('http');
                  if (isImageLine) {
                    return (
                      <div key={i} className="leading-relaxed flex gap-1.5 flex-wrap items-start">
                        <span className="text-muted-foreground font-medium shrink-0">{indent}{label}:</span>
                        <span className="break-words flex items-center gap-2 flex-wrap">
                          <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline truncate max-w-[200px]">{url}</a>
                          <a href={url} target="_blank" rel="noopener noreferrer" className="shrink-0 w-14 h-14 rounded border overflow-hidden bg-muted">
                            <img src={url} alt="当次尝试" className="w-full h-full object-cover" />
                          </a>
                        </span>
                      </div>
                    );
                  }
                  if (isVideoLine) {
                    return (
                      <div key={i} className="leading-relaxed flex flex-col gap-1">
                        <span className="text-muted-foreground font-medium shrink-0">{indent}{label}:</span>
                        <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary underline truncate max-w-[280px] text-[11px]">{url}</a>
                        <LazyVideo src={url} className="max-w-[240px] max-h-[135px] rounded border bg-black" />
                      </div>
                    );
                  }
                  return (
                    <div key={i} className="leading-relaxed flex gap-1.5 flex-wrap">
                      <span className="text-muted-foreground font-medium shrink-0">{indent}{label}:</span>
                      <span className="break-words">{value}</span>
                    </div>
                  );
                }
                return <div key={i} className="leading-relaxed">{line}</div>;
              })}
            </div>
          ))}
        </div>
      );
    }
    return <MetricsSummary metrics={metrics} />;
  };

  /** 格式化 image_tool_metrics / video_tool_metrics，无值也展示为 — */
  const MetricsSummary = ({ metrics }: { metrics?: Record<string, unknown> | null }) => {
    if (!metrics || typeof metrics !== "object") return <span className="text-muted-foreground">—</span>;
    const success = metrics.success as boolean | undefined;
    const finalModel = metrics.final_model as string | undefined;
    const totalAttempts = metrics.total_attempts as number | undefined;
    const consistencyPass = metrics.consistency_pass as number | undefined;
    const consistencyChecks = metrics.consistency_checks as number | undefined;
    const details = (metrics.consistency_details as Array<Record<string, unknown>>) || [];
    const failureReasons = (metrics.failure_reasons as string[]) || [];
    const bestEffort = metrics.best_effort_selected as boolean | undefined;
    return (
      <div className="space-y-1.5 text-[11px]">
        <div className="flex flex-wrap gap-x-3 gap-y-0.5">
          {success != null && <span>{success ? "成功" : "失败"}</span>}
          {finalModel && <span>模型: {finalModel}</span>}
          {totalAttempts != null && <span>尝试: {totalAttempts}</span>}
          {(consistencyChecks != null || consistencyPass != null) && (
            <span>一致性: {consistencyPass ?? 0}/{consistencyChecks ?? 0}</span>
          )}
          {bestEffort && <span className="text-amber-600">(best-effort)</span>}
        </div>
        {details.length > 0 && (
          <div className="rounded bg-muted/40 p-1.5 space-y-2">
            {details.map((d, i) => (
              <div key={i} className="space-y-1">
                <div className="flex flex-wrap gap-x-2 gap-y-0.5 items-baseline">
                  {d.model != null && <span className="font-medium">{String(d.model)}</span>}
                  {d.passed != null && <span>{d.passed ? "通过" : "未通过"}</span>}
                  {d.has_character != null && <span>检角色:{d.has_character ? "是" : "否"}</span>}
                </div>
                {(d.face_level != null || d.accessories_level != null || d.clothing_level != null || d.style_level != null || d.style_consistency != null || d.severe_abnormality != null || d.severe_abnormality_reason != null || d.artifact != null || d.artifact_level != null || d.first_frame_consistency != null || d.camera_movement != null || d.action != null) && (
                  <div className="grid grid-cols-1 gap-y-0.5 pl-0.5 text-muted-foreground mb-2">
                    {d.face_level != null && <div><span className="font-medium text-foreground">脸:</span> {String(d.face_level)}</div>}
                    {d.accessories_level != null && <div><span className="font-medium text-foreground">配饰:</span> {String(d.accessories_level)}</div>}
                    {d.clothing_level != null && <div><span className="font-medium text-foreground">服装:</span> {String(d.clothing_level)}</div>}
                    {(d.style_level != null || d.style_consistency != null) && <div><span className="font-medium text-foreground">风格:</span> {String(d.style_level ?? d.style_consistency)}</div>}
                    {(d.severe_abnormality != null || d.artifact != null) && (
                      <div><span className="font-medium text-foreground">整图画面:</span> {String(d.severe_abnormality ?? d.artifact)}</div>
                    )}
                    {d.severe_abnormality_reason != null && String(d.severe_abnormality_reason).trim() !== '' && (
                      <div><span className="font-medium text-foreground">整图说明:</span> {String(d.severe_abnormality_reason)}</div>
                    )}
                    {d.artifact_level != null && <div><span className="font-medium text-foreground">变形:</span> {String(d.artifact_level)}</div>}
                    {d.first_frame_consistency != null && <div><span className="font-medium text-foreground">首帧:</span> {String(d.first_frame_consistency)}</div>}
                    {d.camera_movement != null && <div><span className="font-medium text-foreground">镜头:</span> {String(d.camera_movement)}</div>}
                    {d.action != null && <div><span className="font-medium text-foreground">动作:</span> {String(d.action)}</div>}
                  </div>
                )}
                <div className="pl-0.5 text-muted-foreground"><span className="font-medium text-foreground">汇总:</span> {(d.reason_overall != null && String(d.reason_overall).trim() !== '') ? String(d.reason_overall) : (d.reason != null && String(d.reason).trim() !== '') ? String(d.reason) : '—'}</div>
                {typeof d.image_url === 'string' && d.image_url.startsWith('http') && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="text-muted-foreground text-[10px]">当次图片:</span>
                    <a href={d.image_url} target="_blank" rel="noopener noreferrer" className="text-primary underline truncate max-w-[200px] text-[10px]">{d.image_url}</a>
                    <a href={d.image_url} target="_blank" rel="noopener noreferrer" className="w-12 h-12 rounded border overflow-hidden bg-muted shrink-0">
                      <img src={d.image_url} alt="当次尝试" className="w-full h-full object-cover" />
                    </a>
                  </div>
                )}
                {typeof d.video_url === 'string' && d.video_url.startsWith('http') && (
                  <div className="flex flex-col gap-0.5">
                    <span className="text-muted-foreground text-[10px]">当次视频:</span>
                    <a href={d.video_url} target="_blank" rel="noopener noreferrer" className="text-primary underline truncate max-w-[240px] text-[10px]">{d.video_url}</a>
                    <LazyVideo src={d.video_url} className="max-w-[200px] max-h-[112px] rounded border bg-black" />
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
        {failureReasons.length > 0 && (
          <div className="text-red-600/90">失败原因: {failureReasons.join("; ")}</div>
        )}
      </div>
    );
  };

  const getStatusBadge = (status: string) => {
    switch (status) {
      case 'completed':
        return <Badge className="bg-green-500"><CheckCircle className="w-3 h-3 mr-1" />完成</Badge>;
      case 'failed':
        return <Badge className="bg-red-500"><AlertCircle className="w-3 h-3 mr-1" />失败</Badge>;
      case 'cancelled':
        return <Badge className="bg-gray-500">已取消</Badge>;
      case 'interrupted':
        return <Badge className="bg-amber-500">已暂停</Badge>;
      case 'processing':
      case 'running':
      case 'queued':
        return <Badge className="bg-blue-500"><Clock className="w-3 h-3 mr-1" />处理中</Badge>;
      default:
        return <Badge>{status}</Badge>;
    }
  };

  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-6xl h-[90vh]">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Video className="w-5 h-5" />
            任务详情
          </DialogTitle>
        </DialogHeader>

        {loading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-8 h-8 animate-spin" />
          </div>
        ) : taskData ? (
          <ScrollArea className="flex-1">
            <div className="space-y-6 pr-6">
              {/* 基础信息卡片 */}
              <Card>
                <CardHeader>
                  <CardTitle>基础信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div className="grid grid-cols-2 gap-4">
                    <div>
                      <div className="text-sm text-muted-foreground">Run ID</div>
                      <CopyableId value={taskData.task_id} />
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">Thread ID</div>
                      <CopyableId value={taskData.thread_id} />
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">用户ID</div>
                      <CopyableId value={taskData.user_id} />
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">状态</div>
                      <div>{getStatusBadge(taskData.task_status)}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">语言</div>
                      <div className="text-sm">{taskData.detected_language || '-'}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">开始时间</div>
                      <div className="text-sm">{new Date(taskData.task_start_time).toLocaleString()}</div>
                    </div>
                    <div>
                      <div className="text-sm text-muted-foreground">完成时间</div>
                      <div className="text-sm">
                        {taskData.task_finish_time ? new Date(taskData.task_finish_time).toLocaleString() : '-'}
                      </div>
                    </div>
                    {taskData.cost != null && (
                      <div>
                        <div className="text-sm text-muted-foreground">成本</div>
                        <div className="text-sm">${Number(taskData.cost).toFixed(4)}</div>
                      </div>
                    )}
                  </div>
                  {taskData.tool_consistency_summary && (
                    <div>
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <span className="text-sm text-muted-foreground">一致性汇总</span>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          disabled={recalculatingSummary}
                          onClick={recalculateConsistencySummary}
                          className="shrink-0"
                        >
                          {recalculatingSummary ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
                          <span className="ml-1">{recalculatingSummary ? '统计中…' : '重新统计'}</span>
                        </Button>
                      </div>
                      <div className="p-2 bg-muted/50 rounded text-xs font-mono space-y-1">
                        {Array.isArray((taskData.tool_consistency_summary as { summary_lines?: string[] }).summary_lines) &&
                         (taskData.tool_consistency_summary as { summary_lines?: string[] }).summary_lines!.length > 0
                          ? (taskData.tool_consistency_summary as { summary_lines: string[] }).summary_lines.map((line, i) => (
                              <div key={i}>{line}</div>
                            ))
                          : (
                            <>
                              {taskData.tool_consistency_summary.character != null && (() => {
                                const c = taskData.tool_consistency_summary.character as { pass_count?: number; total_calls?: number; total_image_generations?: number };
                                const total = c.total_calls ?? 0;
                                const actual = c.total_image_generations ?? total;
                                return <div>角色: {c.pass_count ?? 0} / {total}{actual > total ? `（实际 ${actual} 次）` : ''}</div>;
                              })()}
                              {taskData.tool_consistency_summary.keyframe != null && (() => {
                                const k = taskData.tool_consistency_summary.keyframe as { pass_count?: number; total_calls?: number; total_image_generations?: number };
                                const total = k.total_calls ?? 0;
                                const actual = k.total_image_generations ?? total;
                                return <div>关键帧: {k.pass_count ?? 0} / {total}{actual > total ? `（实际 ${actual} 次）` : ''}</div>;
                              })()}
                              {taskData.tool_consistency_summary.video != null && (taskData.tool_consistency_summary.video as { total_calls?: number }).total_calls != null && (() => {
                                const v = taskData.tool_consistency_summary.video as { pass_count?: number; total_calls?: number; total_video_attempts?: number; first_frame_violation_count?: number };
                                const total = v.total_calls ?? 0;
                                const actual = v.total_video_attempts ?? total;
                                return (
                                  <div>
                                    视频: {v.pass_count ?? 0} / {total}{actual > total ? `（实际 ${actual} 次）` : ''}
                                    {(v.first_frame_violation_count ?? 0) > 0 && `，首帧违规 ${v.first_frame_violation_count} 次`}
                                  </div>
                                );
                              })()}
                              {taskData.tool_consistency_summary.reflection != null && (((taskData.tool_consistency_summary.reflection as { reflection_count?: number }).reflection_count ?? 0) > 0 || ((taskData.tool_consistency_summary.reflection as { regenerated_count?: number }).regenerated_count ?? 0) > 0) && (
                                <div>反思 {(taskData.tool_consistency_summary.reflection as { reflection_count?: number }).reflection_count ?? 0} 次 重生 {(taskData.tool_consistency_summary.reflection as { regenerated_count?: number }).regenerated_count ?? 0}</div>
                              )}
                            </>
                          )}
                      </div>
                    </div>
                  )}
                  <div>
                    <div className="text-sm text-muted-foreground mb-2">用户输入（全部）</div>
                    <div className="p-3 bg-secondary rounded-md text-sm space-y-3">
                      {taskData.task_input && (
                        <div>
                          <div className="text-xs font-semibold text-muted-foreground mb-1">文本</div>
                          <div className="whitespace-pre-wrap break-words">{taskData.task_input}</div>
                        </div>
                      )}
                      {taskData.user_input_data && (
                        <>
                          {Array.isArray(taskData.user_input_data.images) && taskData.user_input_data.images.length > 0 && (
                            <div>
                              <div className="text-xs font-semibold text-muted-foreground mb-1 flex items-center gap-1">
                                <Image className="w-3.5 h-3.5" /> 图片 ({taskData.user_input_data.images.length})
                              </div>
                              <div className="flex flex-wrap gap-2 mt-1">
                                {taskData.user_input_data.images.map((img: { url?: string }, i: number) => (
                                  <a key={i} href={img.url} target="_blank" rel="noopener noreferrer" className="block w-20 h-20 rounded border overflow-hidden flex-shrink-0 hover:opacity-80">
                                    <img src={img.url} alt={`用户图片 ${i + 1}`} className="w-full h-full object-cover" />
                                  </a>
                                ))}
                              </div>
                            </div>
                          )}
                          {Array.isArray(taskData.user_input_data.audio_files) && taskData.user_input_data.audio_files.length > 0 && (
                            <div>
                              <div className="text-xs font-semibold text-muted-foreground mb-1 flex items-center gap-1">
                                <Music className="w-3.5 h-3.5" /> 歌曲/音频 ({taskData.user_input_data.audio_files.length})
                              </div>
                              <div className="space-y-2 mt-1">
                                {taskData.user_input_data.audio_files.map((audio: { url?: string; name?: string }, i: number) => (
                                  <div key={i} className="flex items-center gap-2 p-2 bg-muted/50 rounded">
                                    {audio.url && <audio src={audio.url} controls className="max-w-full h-8 flex-1" />}
                                    {audio.name && <span className="text-xs truncate">{audio.name}</span>}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                          {Array.isArray(taskData.user_input_data.video_files) && taskData.user_input_data.video_files.length > 0 && (
                            <div>
                              <div className="text-xs font-semibold text-muted-foreground mb-1 flex items-center gap-1">
                                <Video className="w-3.5 h-3.5" /> 视频 ({taskData.user_input_data.video_files.length})
                              </div>
                              <div className="space-y-2 mt-1">
                                {taskData.user_input_data.video_files.map((v: { url?: string; name?: string }, i: number) => (
                                  <div key={i} className="rounded border overflow-hidden">
                                    {v.url && (
                                      <LazyVideo src={v.url} className="w-full max-h-40 object-contain" />
                                    )}
                                    {v.name && <div className="text-xs p-1 truncate">{v.name}</div>}
                                  </div>
                                ))}
                              </div>
                            </div>
                          )}
                        </>
                      )}
                      {(!taskData.task_input || taskData.task_input.trim() === '') && (!taskData.user_input_data?.images?.length && !taskData.user_input_data?.audio_files?.length && !taskData.user_input_data?.video_files?.length) && (
                        <span className="text-muted-foreground">无</span>
                      )}
                    </div>
                  </div>

                  {(taskData.final_video_url || taskData.latest_final_video_url) && (
                    <div className="space-y-4">
                      {taskData.latest_final_video_url && (
                        <div>
                          <div className="text-sm text-muted-foreground mb-2">最新最终视频（与用户端一致）</div>
                          <LazyVideo
                            src={taskData.latest_final_video_url}
                            className="w-full max-h-[300px] rounded-md"
                          />
                        </div>
                      )}
                      {taskData.final_video_url && taskData.final_video_url !== taskData.latest_final_video_url && (
                        <div>
                          <div className="text-sm text-muted-foreground mb-2">该次任务最终视频</div>
                          <LazyVideo
                            src={taskData.final_video_url}
                            className="w-full max-h-[300px] rounded-md"
                          />
                        </div>
                      )}
                    </div>
                  )}
                </CardContent>
              </Card>

              {/* 详细内容 Tabs */}
              <Tabs defaultValue="story" className="w-full">
                <TabsList className="grid w-full grid-cols-9">
                  <TabsTrigger value="story">故事</TabsTrigger>
                  <TabsTrigger value="music-layer">音乐线</TabsTrigger>
                  <TabsTrigger value="characters">角色</TabsTrigger>
                  <TabsTrigger value="scenes-shots">镜头详情</TabsTrigger>
                  <TabsTrigger value="shot-overview">按镜头显示</TabsTrigger>
                  <TabsTrigger value="keyframes">关键帧</TabsTrigger>
                  <TabsTrigger value="videos">视频</TabsTrigger>
                  <TabsTrigger value="edits">编辑记录</TabsTrigger>
                  <TabsTrigger value="other">其他</TabsTrigger>
                </TabsList>

                <TabsContent value="story" className="space-y-4">
                  {taskData.story_outline_data ? (
                    <Card>
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                          <FileText className="w-4 h-4" />
                          故事大纲
                        </CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div>
                          <div className="text-sm font-semibold mb-1">标题</div>
                          <div className="text-sm">{taskData.story_outline_data.title || '-'}</div>
                        </div>
                        <div>
                          <div className="text-sm font-semibold mb-1">主题</div>
                          <div className="text-sm">{taskData.story_outline_data.theme || '-'}</div>
                        </div>
                        {(taskData.story_outline_data.themes && taskData.story_outline_data.themes.length > 0) && (
                          <div>
                            <div className="text-sm font-semibold mb-1">主题列表</div>
                            <div className="text-sm">{taskData.story_outline_data.themes.join('、')}</div>
                          </div>
                        )}
                        {taskData.story_outline_data.target_audience && (
                          <div>
                            <div className="text-sm font-semibold mb-1">目标受众</div>
                            <div className="text-sm">{taskData.story_outline_data.target_audience}</div>
                          </div>
                        )}
                        {taskData.story_outline_data.narrative_structure && (
                          <div>
                            <div className="text-sm font-semibold mb-1">叙事结构</div>
                            <div className="text-sm">{taskData.story_outline_data.narrative_structure}</div>
                          </div>
                        )}
                        {taskData.story_outline_data.structure && (
                          <div>
                            <div className="text-sm font-semibold mb-1">结构</div>
                            <div className="text-sm">{taskData.story_outline_data.structure}</div>
                          </div>
                        )}
                        {taskData.story_outline_data.analysis_id && (
                          <div>
                            <div className="text-sm font-semibold mb-1">分析ID</div>
                            <UuidRow table="video_analysis" uuid={taskData.story_outline_data.analysis_id} />
                          </div>
                        )}
                        {(taskData.story_outline_data.audio_transcription_uuid != null) && (
                          <div>
                            <div className="text-sm font-semibold mb-1">关联转录 (音乐线)</div>
                            <UuidRow table="video_audio_transcription" uuid={taskData.story_outline_data.audio_transcription_uuid} />
                          </div>
                        )}
                        <div>
                          <div className="text-sm font-semibold mb-1">描述</div>
                          <div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md">
                            {taskData.story_outline_data.description || '-'}
                          </div>
                        </div>
                        <div>
                          <div className="text-sm font-semibold mb-1">核心信息</div>
                          <div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md">
                            {taskData.story_outline_data.key_message || '-'}
                          </div>
                        </div>
                        <div>
                          <div className="text-sm font-semibold mb-1">风格指南</div>
                          <div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md">
                            {taskData.story_outline_data.style_guide || '-'}
                          </div>
                        </div>
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <div className="text-sm font-semibold mb-1">总时长</div>
                            <div className="text-sm">{taskData.story_outline_data.total_duration || 0}秒</div>
                          </div>
                        </div>
                        {taskData.story_outline_data.uuid && (
                          <div className="pt-2 border-t space-y-0.5">
                            <UuidRow table="video_story_outline" uuid={taskData.story_outline_data.uuid} />
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无故事大纲数据
                      </CardContent>
                    </Card>
                  )}

                  {/* 章节（大纲下全部章节，与故事大纲同风格：每项字段单独一行带标签） */}
                  {taskData.chapters_data && taskData.chapters_data.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="text-base">章节</CardTitle>
                        <CardDescription>大纲下全部章节，含关联段落 (audio_section_uuid)</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        {taskData.chapters_data.map((ch: any) => (
                          <div key={ch.uuid} className="rounded-lg border p-4 bg-muted/10 space-y-3">
                            <div>
                              <div className="text-sm font-semibold mb-1">顺序</div>
                              <div className="text-sm">{ch.order ?? '-'}</div>
                            </div>
                            <div>
                              <div className="text-sm font-semibold mb-1">标题</div>
                              <div className="text-sm">{ch.title ?? '-'}</div>
                            </div>
                            <div>
                              <div className="text-sm font-semibold mb-1">描述</div>
                              <div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md">{ch.description ?? '-'}</div>
                            </div>
                            <div>
                              <div className="text-sm font-semibold mb-1">时长（秒）</div>
                              <div className="text-sm">{ch.duration != null ? ch.duration : '-'}</div>
                            </div>
                            {(ch.audio_segment_ids && ch.audio_segment_ids.length > 0) && (
                              <div>
                                <div className="text-sm font-semibold mb-1">关联音频片段 ID</div>
                                <div className="text-xs font-mono break-all">{ch.audio_segment_ids.join(', ')}</div>
                              </div>
                            )}
                            {ch.audio_section_uuid != null && (
                              <div>
                                <div className="text-sm font-semibold mb-1">关联段落 (audio_section_uuid)</div>
                                <UuidRow table="video_audio_section" uuid={ch.audio_section_uuid} />
                              </div>
                            )}
                            <div className="grid grid-cols-2 gap-4 text-sm">
                              {ch.created_at != null && (
                                <div>
                                  <div className="text-sm font-semibold mb-1">创建时间</div>
                                  <div className="text-xs text-muted-foreground">{ch.created_at}</div>
                                </div>
                              )}
                              {ch.updated_at != null && (
                                <div>
                                  <div className="text-sm font-semibold mb-1">更新时间</div>
                                  <div className="text-xs text-muted-foreground">{ch.updated_at}</div>
                                </div>
                              )}
                            </div>
                            {ch.uuid && (
                              <div className="pt-2 border-t space-y-0.5">
                                <UuidRow table="video_chapters" uuid={ch.uuid} />
                              </div>
                            )}
                          </div>
                        ))}
                      </CardContent>
                    </Card>
                  )}

                  {taskData.analysis_data && (
                    <Card>
                      <CardHeader>
                        <CardTitle>分析结果</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-4">
                          <div>
                            <div className="text-sm font-semibold mb-1">视频类型</div>
                            <div className="text-sm">{taskData.analysis_data.video_type || '-'}</div>
                          </div>
                          <div>
                            <div className="text-sm font-semibold mb-1">时长</div>
                            <div className="text-sm">{taskData.analysis_data.duration || 0}秒</div>
                          </div>
                          <div>
                            <div className="text-sm font-semibold mb-1">主角</div>
                            <div className="text-sm">{taskData.analysis_data.main_character || '-'}</div>
                          </div>
                          <div>
                            <div className="text-sm font-semibold mb-1">目标受众</div>
                            <div className="text-sm">{taskData.analysis_data.target_audience || '-'}</div>
                          </div>
                        </div>
                        <div>
                          <div className="text-sm font-semibold mb-1">目的</div>
                          <div className="text-sm p-3 bg-secondary rounded-md">
                            {taskData.analysis_data.purpose || '-'}
                          </div>
                        </div>
                        {taskData.analysis_data.key_elements && (
                          <div>
                            <div className="text-sm font-semibold mb-1">关键元素</div>
                            <div className="text-sm p-3 bg-secondary rounded-md">
                              {Array.isArray(taskData.analysis_data.key_elements) ? (
                                <ul className="list-disc list-inside space-y-1">
                                  {taskData.analysis_data.key_elements.map((element: string, idx: number) => (
                                    <li key={idx}>{element}</li>
                                  ))}
                                </ul>
                              ) : (
                                <span>{taskData.analysis_data.key_elements}</span>
                              )}
                            </div>
                          </div>
                        )}
                        {taskData.analysis_data.style_preferences && (
                          <div>
                            <div className="text-sm font-semibold mb-1">风格偏好</div>
                            <div className="text-sm p-3 bg-secondary rounded-md">
                              {Array.isArray(taskData.analysis_data.style_preferences) ? (
                                <ul className="list-disc list-inside space-y-1">
                                  {taskData.analysis_data.style_preferences.map((style: string, idx: number) => (
                                    <li key={idx}>{style}</li>
                                  ))}
                                </ul>
                              ) : (
                                <span>{taskData.analysis_data.style_preferences}</span>
                              )}
                            </div>
                          </div>
                        )}
                        {taskData.analysis_data.content_category && (
                          <div>
                            <div className="text-sm font-semibold mb-1">内容类别</div>
                            <div className="text-sm">{taskData.analysis_data.content_category}</div>
                          </div>
                        )}
                        {taskData.analysis_data.hidden_style_description && (
                          <div>
                            <div className="text-sm font-semibold mb-1">精选风格描述</div>
                            <div className="text-sm p-3 bg-secondary rounded-md">{taskData.analysis_data.hidden_style_description}</div>
                          </div>
                        )}
                        {taskData.analysis_data.curated_style_prompt_id && (
                          <div>
                            <div className="text-sm font-semibold mb-1">精选风格ID</div>
                            <div className="text-xs text-muted-foreground font-mono">{taskData.analysis_data.curated_style_prompt_id}</div>
                          </div>
                        )}
                        {taskData.analysis_data.uuid && (
                          <div className="pt-2 border-t space-y-0.5">
                            <UuidRow table="video_analysis" uuid={taskData.analysis_data.uuid} />
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                {/* 音乐线：与故事大纲同样式 — 中文标签、长文框、URL 可点击 */}
                <TabsContent value="music-layer" className="space-y-4 min-w-0 overflow-x-hidden">
                  {((taskData.audio_transcriptions_data && taskData.audio_transcriptions_data.length > 0) ||
                    (taskData.audio_sections_data && taskData.audio_sections_data.length > 0) ||
                    (taskData.audio_segments_data && taskData.audio_segments_data.length > 0) ||
                    (taskData.audio_segment_id_to_info && Object.keys(taskData.audio_segment_id_to_info).length > 0)) ? (
                    <div className="space-y-6 min-w-0">
                      {/* 整曲转录：与故事大纲同风格，字段中文标签 + URL 可点击 */}
                      {taskData.audio_transcriptions_data && taskData.audio_transcriptions_data.length > 0 && (
                        <Card>
                          <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                              <Music className="w-4 h-4" />
                              整曲转录
                            </CardTitle>
                            <CardDescription>整曲级信息，对应 video_audio_transcription</CardDescription>
                          </CardHeader>
                          <CardContent className="space-y-4">
                            {taskData.audio_transcriptions_data.map((trans: any) => (
                              <div key={trans.uuid} className="space-y-4">
                                <div>
                                  <div className="text-sm font-semibold mb-1">歌曲名</div>
                                  <div className="text-sm">{trans.song_name ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">整曲 BPM</div>
                                  <div className="text-sm">{trans.global_bpm != null ? trans.global_bpm : '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">流派</div>
                                  <div className="text-sm">{trans.genre ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">整曲情绪</div>
                                  <div className="text-sm">{trans.global_emotion ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">建议核心主题</div>
                                  <div className="text-sm">{trans.suggested_global_theme ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">建议色彩</div>
                                  <div className="text-sm">{trans.suggested_color_palette ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">转录文本</div>
                                  <div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md break-words">{trans.text ?? '-'}</div>
                                </div>
                                <div className="grid grid-cols-2 gap-4">
                                  <div>
                                    <div className="text-sm font-semibold mb-1">总时长</div>
                                    <div className="text-sm">{trans.duration != null ? trans.duration : '-'}</div>
                                  </div>
                                  <div>
                                    <div className="text-sm font-semibold mb-1">是否纯音乐</div>
                                    <div className="text-sm">{trans.is_instrumental == null ? '-' : (trans.is_instrumental ? '是' : '否')}</div>
                                  </div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">任务类型</div>
                                  <div className="text-sm">{trans.task ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">语言</div>
                                  <div className="text-sm">{trans.language ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">文件名</div>
                                  <div className="text-sm">{trans.filename ?? '-'}</div>
                                </div>
                                <div>
                                  <div className="text-sm font-semibold mb-1">音频链接</div>
                                  <div className="text-sm">
                                    {trans.audio_url ? (
                                      <a href={trans.audio_url} target="_blank" rel="noopener noreferrer" className="text-primary underline break-all hover:opacity-80">{trans.audio_url}</a>
                                    ) : '-'}
                                  </div>
                                </div>
                                <div className="grid grid-cols-2 gap-4 text-sm">
                                  {trans.created_at != null && (
                                    <div>
                                      <div className="text-sm font-semibold mb-1">创建时间</div>
                                      <div className="text-xs text-muted-foreground">{trans.created_at}</div>
                                    </div>
                                  )}
                                  {trans.updated_at != null && (
                                    <div>
                                      <div className="text-sm font-semibold mb-1">更新时间</div>
                                      <div className="text-xs text-muted-foreground">{trans.updated_at}</div>
                                    </div>
                                  )}
                                </div>
                                {trans.additional_data != null && (
                                  <Collapsible defaultOpen={false} className="group">
                                    <CollapsibleTrigger className="flex items-center gap-1 text-sm font-semibold text-muted-foreground hover:text-foreground">
                                      <ChevronRight className="h-4 w-4 shrink-0 transition-transform group-data-[state=open]:rotate-90" />
                                      扩展数据
                                    </CollapsibleTrigger>
                                    <CollapsibleContent>
                                      <div className="pt-1 pl-5">
                                        <FieldOrJson label="" value={trans.additional_data} />
                                      </div>
                                    </CollapsibleContent>
                                  </Collapsible>
                                )}
                                {trans.uuid && (
                                  <div className="pt-2 border-t space-y-0.5">
                                    <UuidRow table="video_audio_transcription" uuid={trans.uuid} />
                                  </div>
                                )}
                              </div>
                            ))}
                          </CardContent>
                        </Card>
                      )}
                      {/* 段落：与故事大纲同风格，中文标签 */}
                      <Card>
                        <CardHeader>
                          <CardTitle className="text-base">段落</CardTitle>
                          <CardDescription>曲式段落，对应 video_audio_section；无数据时表示尚未按 Song Structure 落库</CardDescription>
                        </CardHeader>
                        <CardContent>
                          {taskData.audio_sections_data && taskData.audio_sections_data.length > 0 ? (
                            <div className="space-y-4">
                              {taskData.audio_sections_data
                                .sort((a: any, b: any) => (a.start_time ?? 0) - (b.start_time ?? 0))
                                .map((sec: any) => (
                                  <div key={sec.uuid} className="rounded-lg border p-4 bg-muted/10 space-y-3 min-w-0">
                                    <div><div className="text-sm font-semibold mb-1">段落类型</div><div className="text-sm">{sec.section_type ?? '-'}</div></div>
                                    <div><div className="text-sm font-semibold mb-1">起止时间（秒）</div><div className="text-sm">{sec.start_time ?? '-'} — {sec.end_time ?? '-'}</div></div>
                                    {sec.musical_features != null && <div><div className="text-sm font-semibold mb-1">音乐特征</div><div className="text-sm p-3 bg-secondary rounded-md break-words">{sec.musical_features}</div></div>}
                                    {sec.section_emotion != null && <div><div className="text-sm font-semibold mb-1">段落情绪</div><div className="text-sm">{sec.section_emotion}</div></div>}
                                    {sec.suggested_visual_intensity != null && <div><div className="text-sm font-semibold mb-1">建议视觉强度</div><div className="text-sm">{sec.suggested_visual_intensity}</div></div>}
                                    {sec.suggested_rhythmic_strategy != null && <div><div className="text-sm font-semibold mb-1">建议节奏策略</div><div className="text-sm">{sec.suggested_rhythmic_strategy}</div></div>}
                                    {sec.suggested_visual_theme != null && <div><div className="text-sm font-semibold mb-1">建议视觉主题</div><div className="text-sm">{sec.suggested_visual_theme}</div></div>}
                                    {sec.suggested_context != null && <div><div className="text-sm font-semibold mb-1">建议场景</div><div className="text-sm p-3 bg-secondary rounded-md break-words">{sec.suggested_context}</div></div>}
                                    {sec.uuid && <div className="pt-2 border-t"><UuidRow table="video_audio_section" uuid={sec.uuid} /></div>}
                                  </div>
                                ))}
                            </div>
                          ) : (
                            <p className="text-sm text-muted-foreground">暂无段落数据（transcribe 未返回 Song Structure 或未写入 video_audio_section）</p>
                          )}
                        </CardContent>
                      </Card>
                      {/* 切片：与故事大纲同风格，中文标签 */}
                      {((taskData.audio_segments_data && taskData.audio_segments_data.length > 0) || (taskData.audio_segment_id_to_info && Object.keys(taskData.audio_segment_id_to_info).length > 0)) && (
                        <Card>
                          <CardHeader>
                            <CardTitle className="text-base">切片</CardTitle>
                            <CardDescription>按音乐结构划分的片段，对应 video_audio_segment</CardDescription>
                          </CardHeader>
                          <CardContent>
                            <div className="space-y-4 max-h-[480px] overflow-y-auto min-w-0">
                              {taskData.audio_segments_data && taskData.audio_segments_data.length > 0
                                ? taskData.audio_segments_data
                                    .sort((a: any, b: any) => (a?.segment_id ?? 0) - (b?.segment_id ?? 0))
                                    .map((seg: any) => (
                                      <div key={seg.uuid} className="rounded-lg border p-4 bg-muted/10 space-y-3 min-w-0">
                                        <div><div className="text-sm font-semibold mb-1">切片序号</div><div className="text-sm">{seg.segment_id ?? '-'}</div></div>
                                        <div><div className="text-sm font-semibold mb-1">歌词/描述</div><div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md break-words">{seg.text ?? '-'}</div></div>
                                        <div><div className="text-sm font-semibold mb-1">起止与时长</div><div className="text-sm">{seg.start ?? '-'} — {seg.end ?? '-'}（{seg.duration ?? '-'}）</div></div>
                                        <div className="grid grid-cols-2 gap-4">
                                          {seg.emotion != null && <div><div className="text-sm font-semibold mb-1">情绪</div><div className="text-sm">{seg.emotion}</div></div>}
                                          {seg.tempo != null && <div><div className="text-sm font-semibold mb-1">节奏</div><div className="text-sm">{seg.tempo}</div></div>}
                                          {seg.vocal_presence != null && <div><div className="text-sm font-semibold mb-1">是否人声</div><div className="text-sm">{seg.vocal_presence ? '有' : '无'}</div></div>}
                                        </div>
                                        {seg.uuid && <div className="pt-2 border-t"><UuidRow table="video_audio_segment" uuid={seg.uuid} /></div>}
                                      </div>
                                    ))
                                : taskData.audio_segment_id_to_info && Object.entries(taskData.audio_segment_id_to_info)
                                    .sort(([, a]: [string, any], [, b]: [string, any]) => (a?.segment_id ?? 0) - (b?.segment_id ?? 0))
                                    .map(([segId, info]: [string, any]) => (
                                      <div key={segId} className="rounded-lg border p-4 bg-muted/10 space-y-3 min-w-0">
                                        <div><div className="text-sm font-semibold mb-1">切片序号</div><div className="text-sm">{info.segment_id ?? '-'}</div></div>
                                        <div><div className="text-sm font-semibold mb-1">歌词/描述</div><div className="text-sm whitespace-pre-wrap p-3 bg-secondary rounded-md break-words">{info.text ?? '-'}</div></div>
                                        <div><div className="text-sm font-semibold mb-1">起止与时长</div><div className="text-sm">{info.start ?? '-'} — {info.end ?? '-'}（{info.duration ?? '-'}）</div></div>
                                        <div className="grid grid-cols-2 gap-4">
                                          {info.emotion != null && <div><div className="text-sm font-semibold mb-1">情绪</div><div className="text-sm">{info.emotion}</div></div>}
                                          {info.tempo != null && <div><div className="text-sm font-semibold mb-1">节奏</div><div className="text-sm">{info.tempo}</div></div>}
                                          {(info as any).vocal_presence != null && <div><div className="text-sm font-semibold mb-1">是否人声</div><div className="text-sm">{(info as any).vocal_presence ? '有' : '无'}</div></div>}
                                        </div>
                                        <div className="pt-2 border-t"><UuidRow table="video_audio_segment" uuid={segId} /></div>
                                      </div>
                                    ))}
                            </div>
                          </CardContent>
                        </Card>
                      )}
                    </div>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无音乐线数据（转录/段落/切片）。若有转录则需后端返回 audio_transcriptions_data / audio_segments_data。
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                <TabsContent value="characters" className="space-y-4">
                  {taskData.characters_data && taskData.characters_data.length > 0 ? (
                    <>
                      <div className="grid grid-cols-2 gap-4">
                        {taskData.characters_data.map((character: any) => {
                          return (
                            <Card key={character.uuid}>
                              <CardHeader>
                                <CardTitle className="text-base flex items-center gap-2">
                                  <Users className="w-4 h-4" />
                                  {character.name}
                                </CardTitle>
                              </CardHeader>
                              <CardContent className="space-y-3">
                                {/* 版本选择 */}
                                {character.versions && character.versions.length > 1 ? (
                                  <Tabs defaultValue={character.versions.findIndex((v: any) => v.is_current) >= 0 ? `v${character.versions[character.versions.findIndex((v: any) => v.is_current)].version_number}` : `v${character.versions[0].version_number}`} className="w-full">
                                    <TabsList className="grid w-full" style={{ gridTemplateColumns: `repeat(${character.versions.length}, 1fr)` }}>
                                      {character.versions.map((version: any) => (
                                        <TabsTrigger key={version.uuid} value={`v${version.version_number}`} className="text-xs">
                                          v{version.version_number}
                                          {version.is_current && <Badge variant="default" className="ml-1 text-xs">当前</Badge>}
                                        </TabsTrigger>
                                      ))}
                                    </TabsList>
                                    {character.versions.map((version: any) => (
                                      <TabsContent key={version.uuid} value={`v${version.version_number}`} className="space-y-3 mt-3">
                                        {version.character_image_url && (
                                          <div className="space-y-2">
                                            <div className="flex items-center gap-2">
                                              <span className="text-xs font-semibold text-muted-foreground">主图</span>
                                              {version.reference_image_urls?.length && !version.t2i_prompt && (
                                                <Badge variant="secondary" className="text-[10px]">用户上传</Badge>
                                              )}
                                            </div>
                                            <img
                                              src={version.character_image_url}
                                              alt={`${character.name} v${version.version_number}`}
                                              className="w-full aspect-square object-cover rounded-md"
                                            />
                                          </div>
                                        )}
                                        
                                        {/* 该版本对应的多视角图 */}
                                        {version.multiview && version.multiview.multi_view_image_url && (
                                          <div className="space-y-2">
                                            <div className="flex items-center gap-2">
                                              <div className="text-xs font-semibold text-muted-foreground">多视角图</div>
                                              <Badge variant="outline" className="text-xs">
                                                版本 v{version.multiview.version_number}
                                              </Badge>
                                              {version.multiview.success ? (
                                                <Badge variant="default" className="text-xs">成功</Badge>
                                              ) : (
                                                <Badge variant="destructive" className="text-xs">失败</Badge>
                                              )}
                                            </div>
                                            <img
                                              src={version.multiview.multi_view_image_url}
                                              alt={`${character.name} v${version.version_number} 多视角图`}
                                              className="w-full aspect-video object-cover rounded-md border"
                                            />
                                            {version.multiview.multi_view_prompt && (
                                              <div className="text-xs p-2 bg-secondary rounded break-words">
                                                {version.multiview.multi_view_prompt}
                                              </div>
                                            )}
                                            <div className="text-xs text-red-500">错误: {(version.multiview?.error_msg != null && version.multiview.error_msg !== '') ? version.multiview.error_msg : '—'}</div>
                                            <div className="text-xs text-amber-600 font-mono break-all mt-0.5">原始错误: {(version.multiview?.raw_error_msg != null && version.multiview.raw_error_msg !== '') ? version.multiview.raw_error_msg : '—'}</div>
                                          </div>
                                        )}
                                        
                                        {version.t2i_prompt && (
                                          <div className="text-xs p-2 bg-secondary rounded break-words">
                                            <div className="font-semibold mb-1">Prompt</div>
                                            {version.t2i_prompt}
                                          </div>
                                        )}
                                        {/* 角色版本：依赖（参考图），与关键帧一致 */}
                                        {version.reference_image_urls && version.reference_image_urls.length > 0 && (
                                          <div className="border rounded-lg p-2 bg-muted/30 space-y-2">
                                            <div className="text-xs font-medium text-muted-foreground">依赖 · 参考图</div>
                                            <div className="flex flex-wrap gap-1">
                                              {version.reference_image_urls.map((url: string, i: number) => (
                                                <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-12 h-12 rounded border overflow-hidden flex-shrink-0">
                                                  <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                                </a>
                                              ))}
                                            </div>
                                          </div>
                                        )}
                                        {/* 角色版本：工具与参数（与视频一致：每项一行、标签 muted） */}
                                        <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                          <div className="font-medium text-muted-foreground">工具与参数</div>
                                          <div className="space-y-0.5">
                                            {version.model != null && version.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{version.model}</span></div>}
                                            {version.image_generation_tool != null && version.image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{version.image_generation_tool}</span></div>}
                                            {version.provider != null && version.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{version.provider}</span></div>}
                                            {version.aspect_ratio != null && version.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{version.aspect_ratio}</span></div>}
                                            {version.resolution != null && version.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{version.resolution}</span></div>}
                                            {version.seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{version.seed}</span></div>}
                                          </div>
                                          <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(version.error_msg != null && version.error_msg !== '') ? version.error_msg : '—'}</div>
                                          <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(version.raw_error_msg != null && version.raw_error_msg !== '') ? version.raw_error_msg : '—'}</div>
                                        </div>
                                        <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                          <div className="font-medium text-muted-foreground">Metrics</div>
                                          <div className="space-y-0.5">
                                            <div>耗时: {getDurationSec(version) != null ? `${getDurationSec(version)!.toFixed(2)}s` : '—'}</div>
                                            <div>成本: {getCost(version) != null ? `$${getCost(version)!.toFixed(4)}` : '—'}</div>
                                          </div>
                                          <div className="pt-0.5 border-t border-border/50">
                                            <ConsistencyBlock metrics={version.image_tool_metrics} consistencyDisplayLines={version.consistency_display_lines} />
                                          </div>
                                        </div>
                                        {/* 多视角图版本参数 */}
                                        {version.multiview && (version.multiview.model || version.multiview.provider) && (
                                          <div className="text-xs text-muted-foreground">
                                            多视角图: {[version.multiview.model, version.multiview.provider].filter(Boolean).join(' · ')}
                                            {version.multiview.aspect_ratio && ` · ${version.multiview.aspect_ratio}`}
                                            {version.multiview.resolution && ` · ${version.multiview.resolution}`}
                                          </div>
                                        )}
                                      </TabsContent>
                                    ))}
                                  </Tabs>
                                ) : (
                                  <>
                                    {character.image_url && (
                                      <div className="space-y-2">
                                        <div className="flex items-center gap-2">
                                          <span className="text-xs font-semibold text-muted-foreground">主图</span>
                                          {character.versions?.[0]?.reference_image_urls?.length && !character.versions[0].t2i_prompt && (
                                            <Badge variant="secondary" className="text-[10px]">用户上传</Badge>
                                          )}
                                        </div>
                                        <img
                                          src={character.image_url}
                                          alt={character.name}
                                          className="w-full aspect-square object-cover rounded-md"
                                        />
                                      </div>
                                    )}
                                    {character.versions && character.versions[0]?.multiview && character.versions[0].multiview.multi_view_image_url && (
                                      <div className="space-y-2">
                                        <div className="flex items-center gap-2">
                                          <div className="text-xs font-semibold text-muted-foreground">多视角图</div>
                                          <Badge variant="outline" className="text-xs">版本 v{character.versions[0].multiview.version_number}</Badge>
                                          {character.versions[0].multiview.success ? <Badge variant="default" className="text-xs">成功</Badge> : <Badge variant="destructive" className="text-xs">失败</Badge>}
                                        </div>
                                        <img src={character.versions[0].multiview.multi_view_image_url} alt={`${character.name} 多视角图`} className="w-full aspect-video object-cover rounded-md border" />
                                        {character.versions[0].multiview.multi_view_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{character.versions[0].multiview.multi_view_prompt}</div>}
                                        <div className="text-xs text-red-500">错误: {(character.versions[0].multiview?.error_msg != null && character.versions[0].multiview.error_msg !== '') ? character.versions[0].multiview.error_msg : '—'}</div>
                                        <div className="text-xs text-amber-600 font-mono break-all">原始错误: {(character.versions[0].multiview?.raw_error_msg != null && character.versions[0].multiview.raw_error_msg !== '') ? character.versions[0].multiview.raw_error_msg : '—'}</div>
                                      </div>
                                    )}
                                    {character.versions && character.versions[0] && (
                                      <>
                                        {character.versions[0].t2i_prompt && (
                                          <div className="text-xs p-2 bg-secondary rounded break-words">
                                            <div className="font-semibold mb-1">Prompt</div>
                                            {character.versions[0].t2i_prompt}
                                          </div>
                                        )}
                                        {/* 单版本：依赖（参考图），与关键帧一致 */}
                                        {character.versions[0].reference_image_urls && character.versions[0].reference_image_urls.length > 0 && (
                                          <div className="border rounded-lg p-2 bg-muted/30 space-y-2">
                                            <div className="text-xs font-medium text-muted-foreground">依赖 · 参考图</div>
                                            <div className="flex flex-wrap gap-1">
                                              {character.versions[0].reference_image_urls.map((url: string, i: number) => (
                                                <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-12 h-12 rounded border overflow-hidden flex-shrink-0">
                                                  <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                                </a>
                                              ))}
                                            </div>
                                          </div>
                                        )}
                                        {/* 单版本：工具与参数（与视频一致：每项一行、标签 muted） */}
                                        <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                          <div className="font-medium text-muted-foreground">工具与参数</div>
                                          <div className="space-y-0.5">
                                            {character.versions[0].model != null && character.versions[0].model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{character.versions[0].model}</span></div>}
                                            {character.versions[0].image_generation_tool != null && character.versions[0].image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{character.versions[0].image_generation_tool}</span></div>}
                                            {character.versions[0].provider != null && character.versions[0].provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{character.versions[0].provider}</span></div>}
                                            {character.versions[0].aspect_ratio != null && character.versions[0].aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{character.versions[0].aspect_ratio}</span></div>}
                                            {character.versions[0].resolution != null && character.versions[0].resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{character.versions[0].resolution}</span></div>}
                                            {character.versions[0].seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{character.versions[0].seed}</span></div>}
                                          </div>
                                          <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(character.versions[0].error_msg != null && character.versions[0].error_msg !== '') ? character.versions[0].error_msg : '—'}</div>
                                          <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(character.versions[0].raw_error_msg != null && character.versions[0].raw_error_msg !== '') ? character.versions[0].raw_error_msg : '—'}</div>
                                        </div>
                                        <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                          <div className="font-medium text-muted-foreground">Metrics</div>
                                          <div className="space-y-0.5">
                                            <div>耗时: {getDurationSec(character.versions[0]) != null ? `${getDurationSec(character.versions[0])!.toFixed(2)}s` : '—'}</div>
                                            <div>成本: {getCost(character.versions[0]) != null ? `$${getCost(character.versions[0])!.toFixed(4)}` : '—'}</div>
                                          </div>
                                          <div className="pt-0.5 border-t border-border/50">
                                            <ConsistencyBlock metrics={character.versions[0].image_tool_metrics} consistencyDisplayLines={character.versions[0].consistency_display_lines} />
                                          </div>
                                        </div>
                                      </>
                                    )}
                                  </>
                                )}
                                
                                <div className="space-y-2">
                                  <div>
                                    <div className="text-xs font-semibold text-muted-foreground mb-1">描述</div>
                                    <div className="text-sm">{character.description}</div>
                                  </div>
                                  {character.personality && (
                                    <div>
                                      <div className="text-xs font-semibold text-muted-foreground mb-1">性格</div>
                                      <div className="text-sm">{character.personality}</div>
                                    </div>
                                  )}
                                  {character.appearance && (
                                    <div>
                                      <div className="text-xs font-semibold text-muted-foreground mb-1">外观</div>
                                      <div className="text-sm">{character.appearance}</div>
                                    </div>
                                  )}
                                  {character.role && (
                                    <div>
                                      <div className="text-xs font-semibold text-muted-foreground mb-1">角色</div>
                                      <div className="text-sm">{character.role}</div>
                                    </div>
                                  )}
                                </div>
                                {(character.uuid || (character.versions && character.versions.length > 0)) && (
                                  <div className="mt-2 pt-2 border-t text-xs space-y-1">
                                    {character.uuid && <UuidRow table="video_characters" uuid={character.uuid} />}
                                    {character.versions?.map((v: any) => v.uuid).filter(Boolean).map((uid: string) => (
                                      <UuidRow key={uid} table="video_character_generation_versions" uuid={uid} />
                                    ))}
                                  </div>
                                )}
                              </CardContent>
                            </Card>
                          );
                        })}
                      </div>
                      
                      {/* 融合图 */}
                      {taskData.fusion_images_data && taskData.fusion_images_data.length > 0 && (
                        <Card>
                          <CardHeader>
                            <CardTitle className="flex items-center gap-2">
                              <Image className="w-4 h-4" />
                              融合图
                            </CardTitle>
                            <CardDescription>
                              共 {taskData.fusion_images_data.length} 张融合图
                            </CardDescription>
                          </CardHeader>
                          <CardContent>
                            <div className="grid grid-cols-2 gap-4">
                              {taskData.fusion_images_data.map((fusion: any) => {
                                // 获取参与融合的角色名称
                                const characterNames = taskData.characters_data
                                  ?.filter((char: any) => 
                                    Array.isArray(fusion.character_ids) && 
                                    fusion.character_ids.includes(char.uuid)
                                  )
                                  .map((char: any) => char.name)
                                  .join(', ') || fusion.character_ids?.join(', ') || '-';
                                
                                return (
                                  <div key={fusion.uuid} className="space-y-2">
                                    <div className="flex items-center gap-2">
                                      <Badge variant="outline" className="text-xs">
                                        {fusion.image_type === 'main' ? '主图融合' : '多视角融合'}
                                      </Badge>
                                      {fusion.success ? (
                                        <Badge variant="default" className="text-xs">成功</Badge>
                                      ) : (
                                        <Badge variant="destructive" className="text-xs">失败</Badge>
                                      )}
                                    </div>
                                    {fusion.fusion_image_url && (
                                      <img
                                        src={fusion.fusion_image_url}
                                        alt={`融合图 ${fusion.fusion_key}`}
                                        className="w-full aspect-video object-cover rounded-md border"
                                      />
                                    )}
                                    <div className="text-xs text-muted-foreground">
                                      <div>参与角色: {characterNames}</div>
                                      {fusion.fusion_prompt && (
                                        <div className="mt-1 p-2 bg-secondary rounded text-xs break-words">
                                          {fusion.fusion_prompt}
                                        </div>
                                      )}
                                      <div className="mt-1 text-red-500 text-xs">错误: {(fusion.error_msg != null && fusion.error_msg !== '') ? fusion.error_msg : '—'}</div>
                                      <div className="mt-1 text-amber-600 text-xs font-mono break-all">原始错误: {(fusion.raw_error_msg != null && fusion.raw_error_msg !== '') ? fusion.raw_error_msg : '—'}</div>
                                      {fusion.uuid && <div className="mt-1"><UuidRow table="video_character_fusion_images" uuid={fusion.uuid} /></div>}
                                    </div>
                                  </div>
                                );
                              })}
                            </div>
                          </CardContent>
                        </Card>
                      )}
                    </>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无角色数据
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                <TabsContent value="scenes-shots" className="space-y-4">
                  {((taskData.scenes_data && taskData.scenes_data.length > 0) || (taskData.shots_data && taskData.shots_data.length > 0)) ? (
                    <div className="space-y-6">
                      {/* 场景按 scene_number 排序（后端已排序），其下展示该场景的镜头 */}
                      {(taskData.scenes_data || []).map((scene: any) => {
                        const sceneShots = (taskData.shots_data || []).filter((s: any) => s.scene_id === scene.uuid);
                        return (
                          <Card key={scene.uuid}>
                            <CardHeader>
                              <CardTitle className="text-base">
                                场景 {scene.scene_number}: {scene.title}
                              </CardTitle>
                              <CardDescription className="text-xs">
                                时长: {scene.duration}s
                                {scene.is_bridge && ' · 衔接片段'}
                                {scene.generation_mode && ` · ${scene.generation_mode}`}
                                {scene.uuid && <div className="mt-1"><UuidRow table="video_scenes" uuid={scene.uuid} /></div>}
                              </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-4">
                              <div className="text-sm">{scene.description}</div>
                              {(scene.camera_angle || scene.character_action || scene.visual_style || scene.transition_style) && (
                                <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
                                  {scene.camera_angle && <div><span className="font-medium">镜头角度:</span> {scene.camera_angle}</div>}
                                  {scene.character_action && <div><span className="font-medium">角色动作:</span> {scene.character_action}</div>}
                                  {scene.visual_style && <div><span className="font-medium">视觉风格:</span> {scene.visual_style}</div>}
                                  {scene.transition_style && <div><span className="font-medium">转场:</span> {scene.transition_style}</div>}
                                </div>
                              )}
                              {scene.chapter_id && taskData.chapter_id_to_info?.[scene.chapter_id] && (
                                <div className="text-xs text-muted-foreground">
                                  <span className="font-medium">章节 {taskData.chapter_id_to_info[scene.chapter_id].order}:</span> {taskData.chapter_id_to_info[scene.chapter_id].title}
                                  {taskData.chapter_id_to_info[scene.chapter_id].description && ` — ${taskData.chapter_id_to_info[scene.chapter_id].description}`}
                                </div>
                              )}
                              {scene.chapter_id && !taskData.chapter_id_to_info?.[scene.chapter_id] && <div className="text-xs text-muted-foreground"><span className="font-medium">章节:</span> {scene.chapter_id}</div>}
                              {scene.audio_segment_ids && scene.audio_segment_ids.length > 0 && (
                                <div className="text-xs text-muted-foreground space-y-1">
                                  <span className="font-medium">音频:</span>
                                  {scene.audio_segment_ids.map((aid: string) => {
                                    const info = taskData.audio_segment_id_to_info?.[aid];
                                    return info ? (
                                      <div key={aid} className="pl-2 space-y-1">
                                                <div>片段 {info.segment_id}: {info.text ? (info.text.slice(0, 80) + (info.text.length > 80 ? '…' : '')) : `${info.duration}s`}
                                          {(info.emotion || info.tempo) && <span className="text-muted-foreground ml-1">({[info.emotion, info.tempo].filter(Boolean).join(' · ')})</span>}
                                          {info.vocal_presence === true && <Badge variant="secondary" className="ml-1 text-[10px]">人声</Badge>}
                                          {info.vocal_presence === false && <Badge variant="outline" className="ml-1 text-[10px]">无人声</Badge>}
                                        </div>
                                        <div className="space-y-0.5 mt-0.5">
                                          <UuidRow table="video_audio_segment" uuid={aid} />
                                          {(info as any).music_generation_uuid && <UuidRow table="video_music_generations" uuid={(info as any).music_generation_uuid} />}
                                          {(info as any).music_generation_version_uuid && <UuidRow table="video_music_generation_versions" uuid={(info as any).music_generation_version_uuid} />}
                                        </div>
                                        {(info.segment_audio_url || info.parent_audio_url) && (
                                          <>
                                            {info.segment_audio_url ? <span className="text-[10px] text-muted-foreground mr-1">分段音频</span> : <span className="text-[10px] text-muted-foreground mr-1">完整音频</span>}
                                            <audio src={info.segment_audio_url || info.parent_audio_url} controls className="w-full max-w-md h-8" />
                                          </>
                                        )}
                                      </div>
                                    ) : <div key={aid} className="pl-2">{aid}</div>;
                                  })}
                                </div>
                              )}
                              {scene.character_ids && scene.character_ids.length > 0 && (
                                <div className="flex flex-wrap items-center gap-2">
                                  <span className="text-xs font-medium text-muted-foreground">角色:</span>
                                  {scene.character_ids.map((cid: string) => {
                                    const info = characterMap[cid];
                                    return (
                                      <div key={cid} className="flex items-center gap-1.5 rounded border px-2 py-1 bg-muted/50">
                                        {info?.image_url && <img src={info.image_url} alt="" className="w-6 h-6 rounded object-cover" />}
                                        <span className="text-xs">{info ? info.name : cid}</span>
                                      </div>
                                    );
                                  })}
                                </div>
                              )}
                              {/* 该场景下的镜头（按 shot_number 展示） */}
                              {sceneShots.length > 0 && (
                                <div className="border-t pt-4 space-y-3">
                                  <div className="text-xs font-semibold text-muted-foreground">镜头</div>
                                  {sceneShots.sort((a: any, b: any) => (a.shot_number ?? 0) - (b.shot_number ?? 0)).map((shot: any) => (
                                    <div key={shot.uuid} className="rounded-lg border p-3 bg-muted/20 space-y-2">
                                      <div className="flex items-center gap-2 flex-wrap">
                                        <span className="font-medium text-sm">镜头 {shot.shot_number}</span>
                                        {shot.uuid && <UuidRow table="video_detailed_shots" uuid={shot.uuid} />}
                                        {shot.is_bridge && <Badge variant="outline" className="text-xs">衔接</Badge>}
                                        {shot.shot_type && <Badge variant="secondary" className="text-xs">{shot.shot_type}</Badge>}
                                        <span className="text-xs text-muted-foreground">{shot.duration != null ? `${Number(shot.duration).toFixed(2)}s` : ''}</span>
                                      </div>
                                      <ShotModeAndRoutingPanel shot={shot} />
                                      {shot.scene_description && (
                                        <div className="text-sm"><span className="text-muted-foreground">场景描述:</span> <span className="whitespace-pre-wrap break-words">{shot.scene_description}</span></div>
                                      )}
                                      {shot.narration != null && shot.narration !== '' && (
                                        <div className="text-sm"><span className="text-muted-foreground">旁白:</span> <span className="break-words">{shot.narration}</span></div>
                                      )}
                                      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs text-muted-foreground">
                                        {shot.camera_position && <div><span className="font-medium">机位:</span> {shot.camera_position}</div>}
                                        {shot.camera_movement && <div><span className="font-medium">镜头运动:</span> {shot.camera_movement}</div>}
                                        {shot.camera_angle && <div><span className="font-medium">镜头角度:</span> {shot.camera_angle}</div>}
                                        {shot.subject_angle && <div><span className="font-medium">主体角度:</span> {shot.subject_angle}</div>}
                                        {shot.subject_pose && <div><span className="font-medium">主体姿势:</span> {shot.subject_pose}</div>}
                                        {shot.lighting && <div><span className="font-medium">光影:</span> {shot.lighting}</div>}
                                        {shot.visual_effects && <div><span className="font-medium">特效:</span> {shot.visual_effects}</div>}
                                        {shot.transition && <div><span className="font-medium">转场:</span> {shot.transition}</div>}
                                        {shot.dialogue && <div><span className="font-medium">台词:</span> {shot.dialogue}</div>}
                                        {shot.sound_effects && <div><span className="font-medium">音效:</span> {shot.sound_effects}</div>}
                                      </div>
                                      {shot.style_guide && <div className="text-xs text-muted-foreground"><span className="font-medium">风格指导:</span> {shot.style_guide}</div>}
                                      {shot.audio_segment_ids && shot.audio_segment_ids.length > 0 && (
                                        <div className="text-xs text-muted-foreground space-y-0.5">
                                          <span className="font-medium">音频:</span>
                                          {shot.audio_segment_ids.map((aid: string) => {
                                            const info = taskData.audio_segment_id_to_info?.[aid];
                                            return info ? (
                                              <div key={aid} className="pl-2 space-y-1">
                                                <div>片段 {info.segment_id}: {info.text ? (info.text.slice(0, 60) + (info.text.length > 60 ? '…' : '')) : `${info.duration}s`}
                                                  {(info.emotion || info.tempo) && <span className="text-muted-foreground ml-1">({[info.emotion, info.tempo].filter(Boolean).join(' · ')})</span>}
                                                  {info.vocal_presence === true && <Badge variant="secondary" className="ml-1 text-[10px]">人声</Badge>}
                                                  {info.vocal_presence === false && <Badge variant="outline" className="ml-1 text-[10px]">无人声</Badge>}
                                                </div>
                                                <div className="space-y-0.5 mt-0.5">
                                                  <UuidRow table="video_audio_segment" uuid={aid} />
                                                  {(info as any).music_generation_uuid && <UuidRow table="video_music_generations" uuid={(info as any).music_generation_uuid} />}
                                                  {(info as any).music_generation_version_uuid && <UuidRow table="video_music_generation_versions" uuid={(info as any).music_generation_version_uuid} />}
                                                </div>
                                                {(info.segment_audio_url || info.parent_audio_url) && (
                                                  <>
                                                    {info.segment_audio_url ? <span className="text-[10px] text-muted-foreground mr-1">分段音频</span> : <span className="text-[10px] text-muted-foreground mr-1">完整音频</span>}
                                                    <audio src={info.segment_audio_url || info.parent_audio_url} controls className="w-full max-w-md h-8" />
                                                  </>
                                                )}
                                              </div>
                                            ) : <div key={aid} className="pl-2">{aid}</div>;
                                          })}
                                        </div>
                                      )}
                                      {shot.chapter_id && taskData.chapter_id_to_info?.[shot.chapter_id] && (
                                        <div className="text-xs text-muted-foreground"><span className="font-medium">章节 {taskData.chapter_id_to_info[shot.chapter_id].order}:</span> {taskData.chapter_id_to_info[shot.chapter_id].title}</div>
                                      )}
                                      {shot.character_ids && shot.character_ids.length > 0 && (
                                        <div className="flex flex-wrap items-center gap-2">
                                          <span className="text-xs font-medium text-muted-foreground">角色:</span>
                                          {shot.character_ids.map((cid: string) => {
                                            const info = characterMap[cid];
                                            return (
                                              <div key={cid} className="flex items-center gap-1.5 rounded border px-2 py-1 bg-background">
                                                {info?.image_url && <img src={info.image_url} alt="" className="w-6 h-6 rounded object-cover" />}
                                                <span className="text-xs">{info ? info.name : cid}</span>
                                              </div>
                                            );
                                          })}
                                        </div>
                                      )}
                                    </div>
                                  ))}
                                </div>
                              )}
                            </CardContent>
                          </Card>
                        );
                      })}
                      {/* 无场景但有镜头时，单独列出未归属的镜头（scene_id 不在 scenes 中或为空） */}
                      {taskData.shots_data && taskData.shots_data.length > 0 && (!taskData.scenes_data || taskData.scenes_data.length === 0) && (
                        <Card>
                          <CardHeader><CardTitle className="text-base">镜头</CardTitle></CardHeader>
                          <CardContent className="space-y-3">
                            {(taskData.shots_data as any[]).sort((a: any, b: any) => (a.shot_number ?? 0) - (b.shot_number ?? 0)).map((shot: any) => (
                              <div key={shot.uuid} className="rounded-lg border p-3 space-y-2">
                                <div className="flex items-center gap-2 flex-wrap"><span className="font-medium">镜头 {shot.shot_number}</span>
                                {shot.uuid && <UuidRow table="video_detailed_shots" uuid={shot.uuid} />}{shot.shot_type && <Badge variant="secondary" className="text-xs">{shot.shot_type}</Badge>}<span className="text-xs text-muted-foreground">{shot.duration != null ? `${Number(shot.duration).toFixed(2)}s` : ''}</span></div>
                                <ShotModeAndRoutingPanel shot={shot} />
                                {shot.scene_description && <div className="text-sm whitespace-pre-wrap break-words">{shot.scene_description}</div>}
                                {shot.narration != null && shot.narration !== '' && <div className="text-sm">{shot.narration}</div>}
                                {shot.audio_segment_ids && shot.audio_segment_ids.length > 0 && (
                                  <div className="text-xs text-muted-foreground space-y-0.5">
                                    <span className="font-medium">音频:</span>
                                    {shot.audio_segment_ids.map((aid: string) => {
                                      const info = taskData.audio_segment_id_to_info?.[aid];
                                      return info ? (
                                        <div key={aid} className="pl-2 space-y-1">
                                          <div>片段 {info.segment_id}: {info.text ? (info.text.slice(0, 60) + (info.text.length > 60 ? '…' : '')) : `${info.duration}s`}
                                            {(info.emotion || info.tempo) && <span className="text-muted-foreground ml-1">({[info.emotion, info.tempo].filter(Boolean).join(' · ')})</span>}
                                          </div>
                                          <div className="space-y-0.5 mt-0.5">
                                            <UuidRow table="video_audio_segment" uuid={aid} />
                                            {(info as any).music_generation_uuid && <UuidRow table="video_music_generations" uuid={(info as any).music_generation_uuid} />}
                                            {(info as any).music_generation_version_uuid && <UuidRow table="video_music_generation_versions" uuid={(info as any).music_generation_version_uuid} />}
                                          </div>
                                          {(info.segment_audio_url || info.parent_audio_url) && (
                                            <>
                                              {info.segment_audio_url ? <span className="text-[10px] text-muted-foreground mr-1">分段音频</span> : <span className="text-[10px] text-muted-foreground mr-1">完整音频</span>}
                                              <audio src={info.segment_audio_url || info.parent_audio_url} controls className="w-full max-w-md h-8" />
                                            </>
                                          )}
                                        </div>
                                      ) : <div key={aid} className="pl-2">{aid}</div>;
                                    })}
                                  </div>
                                )}
                                {shot.chapter_id && taskData.chapter_id_to_info?.[shot.chapter_id] && <div className="text-xs text-muted-foreground">章节 {taskData.chapter_id_to_info[shot.chapter_id].order}: {taskData.chapter_id_to_info[shot.chapter_id].title}</div>}
                                {shot.character_ids && shot.character_ids.length > 0 && (
                                  <div className="flex flex-wrap gap-2">
                                    {shot.character_ids.map((cid: string) => {
                                      const info = characterMap[cid];
                                      return (
                                        <div key={cid} className="flex items-center gap-1.5 rounded border px-2 py-1">
                                          {info?.image_url && <img src={info.image_url} alt="" className="w-6 h-6 rounded object-cover" />}
                                          <span className="text-xs">{info ? info.name : cid}</span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            ))}
                          </CardContent>
                        </Card>
                      )}
                    </div>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无场景与镜头数据
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                {/* 按镜头显示：镜头 + 关键帧 + 视频 合并展示 */}
                <TabsContent value="shot-overview" className="space-y-4">
                  {(() => {
                    const shotNumbers = new Set<number>();
                    (taskData.shots_data || []).forEach((s: any) => { if (s?.shot_number != null) shotNumbers.add(Number(s.shot_number)); });
                    (taskData.keyframes_data || []).forEach((k: any) => { if (k?.shot_number != null) shotNumbers.add(Number(k.shot_number)); });
                    (taskData.videos_data || []).forEach((v: any) => { if (v?.shot_number != null) shotNumbers.add(Number(v.shot_number)); });
                    const sortedShots = Array.from(shotNumbers).sort((a, b) => a - b);
                    if (sortedShots.length === 0) {
                      return (
                        <Card>
                          <CardContent className="py-8 text-center text-muted-foreground">
                            暂无镜头/关键帧/视频数据
                          </CardContent>
                        </Card>
                      );
                    }
                    return (
                      <div className="space-y-6">
                        {sortedShots.map((shotNum) => {
                          const shot = (taskData.shots_data || []).find((s: any) => Number(s?.shot_number) === shotNum);
                          const keyframe = (taskData.keyframes_data || []).find((k: any) => Number(k?.shot_number) === shotNum);
                          const video = (taskData.videos_data || []).find((v: any) => Number(v?.shot_number) === shotNum);
                          return (
                            <Card key={shotNum} className="overflow-hidden">
                              <CardHeader className="py-3 bg-muted/40">
                                <CardTitle className="text-lg">镜头 {shotNum}</CardTitle>
                                <CardDescription className="text-xs font-mono break-all space-y-0.5">
                                  设计信息 · 关键帧 · 视频 对照
                                  {shot?.uuid && <div className="mt-1"><UuidRow table="video_detailed_shots" uuid={shot.uuid} /></div>}
                                  {keyframe?.uuid && <UuidRow table="video_keyframes" uuid={keyframe.uuid} />}
                                  {video?.uuid && <UuidRow table="video_generations" uuid={video.uuid} />}
                                </CardDescription>
                              </CardHeader>
                              <CardContent className="p-4">
                                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                                  {/* 左：镜头信息（与场景与镜头 tab 一致） */}
                                  <div className="rounded-lg border bg-muted/20 p-3 space-y-2 min-h-[120px]">
                                    <div className="text-sm font-semibold text-muted-foreground border-b pb-1">镜头设计</div>
                                    {shot ? (
                                      <>
                                        <div className="flex flex-wrap items-center gap-2">
                                          {shot.is_bridge && <Badge variant="outline" className="text-xs">衔接</Badge>}
                                          {shot.shot_type && <Badge variant="secondary" className="text-xs">{shot.shot_type}</Badge>}
                                          <span className="text-xs text-muted-foreground">{shot.duration != null ? `${Number(shot.duration).toFixed(2)}s` : ''}</span>
                                        </div>
                                        <ShotModeAndRoutingPanel shot={shot} />
                                        {shot.scene_description && (
                                          <div className="text-xs"><span className="text-muted-foreground">场景描述:</span> <span className="whitespace-pre-wrap break-words">{shot.scene_description}</span></div>
                                        )}
                                        {shot.narration != null && shot.narration !== '' && (
                                          <div className="text-xs"><span className="text-muted-foreground">旁白:</span> <span className="break-words">{shot.narration}</span></div>
                                        )}
                                        <div className="grid grid-cols-1 gap-1 text-xs text-muted-foreground">
                                          {shot.camera_position && <div><span className="font-medium">机位:</span> {shot.camera_position}</div>}
                                          {shot.camera_movement && <div><span className="font-medium">镜头运动:</span> {shot.camera_movement}</div>}
                                          {shot.camera_angle && <div><span className="font-medium">镜头角度:</span> {shot.camera_angle}</div>}
                                          {shot.subject_angle && <div><span className="font-medium">主体角度:</span> {shot.subject_angle}</div>}
                                          {shot.subject_pose && <div><span className="font-medium">主体姿势:</span> {shot.subject_pose}</div>}
                                          {shot.lighting && <div><span className="font-medium">光影:</span> {shot.lighting}</div>}
                                          {shot.visual_effects && <div><span className="font-medium">特效:</span> {shot.visual_effects}</div>}
                                          {shot.transition && <div><span className="font-medium">转场:</span> {shot.transition}</div>}
                                          {shot.dialogue && <div><span className="font-medium">台词:</span> {shot.dialogue}</div>}
                                          {shot.sound_effects && <div><span className="font-medium">音效:</span> {shot.sound_effects}</div>}
                                        </div>
                                        {shot.style_guide && <div className="text-xs text-muted-foreground"><span className="font-medium">风格指导:</span> {shot.style_guide}</div>}
                                        {shot.audio_segment_ids && shot.audio_segment_ids.length > 0 && (
                                          <div className="text-xs text-muted-foreground space-y-0.5">
                                            <span className="font-medium">音频:</span>
                                            {shot.audio_segment_ids.map((aid: string) => {
                                              const info = taskData.audio_segment_id_to_info?.[aid];
                                              return info ? (
                                                <div key={aid} className="pl-2 space-y-1">
                                                  <div>片段 {info.segment_id}: {info.text ? (info.text.slice(0, 50) + (info.text.length > 50 ? '…' : '')) : `${info.duration}s`}
                                                    {(info.emotion || info.tempo) && <span className="text-muted-foreground ml-1">({[info.emotion, info.tempo].filter(Boolean).join(' · ')})</span>}
                                                  </div>
                                                  <div className="space-y-0.5 mt-0.5">
                                                    <UuidRow table="video_audio_segment" uuid={aid} />
                                                    {(info as any).music_generation_uuid && <UuidRow table="video_music_generations" uuid={(info as any).music_generation_uuid} />}
                                                    {(info as any).music_generation_version_uuid && <UuidRow table="video_music_generation_versions" uuid={(info as any).music_generation_version_uuid} />}
                                                  </div>
                                                  {(info.segment_audio_url || info.parent_audio_url) && (
                                                    <>
                                                      {info.segment_audio_url ? <span className="text-[10px] text-muted-foreground mr-1">分段音频</span> : <span className="text-[10px] text-muted-foreground mr-1">完整音频</span>}
                                                      <audio src={info.segment_audio_url || info.parent_audio_url} controls className="w-full max-w-md h-8" />
                                                    </>
                                                  )}
                                                </div>
                                              ) : <div key={aid} className="pl-2">{aid}</div>;
                                            })}
                                          </div>
                                        )}
                                        {shot.chapter_id && taskData.chapter_id_to_info?.[shot.chapter_id] && (
                                          <div className="text-xs text-muted-foreground">章节 {taskData.chapter_id_to_info[shot.chapter_id].order}: {taskData.chapter_id_to_info[shot.chapter_id].title}</div>
                                        )}
                                        {shot.character_ids && shot.character_ids.length > 0 && (
                                          <div className="flex flex-wrap gap-1">
                                            {shot.character_ids.map((cid: string) => {
                                              const info = characterMap[cid];
                                              return (
                                                <div key={cid} className="flex items-center gap-1 rounded border px-1.5 py-0.5 bg-background text-xs">
                                                  {info?.image_url && <img src={info.image_url} alt="" className="w-4 h-4 rounded object-cover" />}
                                                  <span>{info ? info.name : cid}</span>
                                                </div>
                                              );
                                            })}
                                          </div>
                                        )}
                                      </>
                                    ) : (
                                      <div className="text-xs text-muted-foreground">暂无该镜头设计数据</div>
                                    )}
                                  </div>
                                  {/* 中：关键帧（与关键帧 tab 一致：含版本切换 + 图 + prompt + 工具与参数 + 依赖） */}
                                  <div className="rounded-lg border bg-muted/20 p-3 space-y-2 min-h-[120px]">
                                    <div className="text-sm font-semibold text-muted-foreground border-b pb-1">关键帧</div>
                                    {keyframe ? (
                                      keyframe.versions && keyframe.versions.length > 1 ? (
                                        <Tabs defaultValue={keyframe.versions.findIndex((v: any) => v.is_current) >= 0 ? `v${keyframe.versions[keyframe.versions.findIndex((v: any) => v.is_current)].version_number}` : `v${keyframe.versions[0].version_number}`} className="w-full">
                                          <TabsList className="grid w-full grid-cols-4 max-h-8 text-xs" style={{ gridTemplateColumns: `repeat(${Math.min(keyframe.versions.length, 6)}, 1fr)` }}>
                                            {keyframe.versions.map((version: any) => (
                                              <TabsTrigger key={version.uuid} value={`v${version.version_number}`} className="text-xs truncate">
                                                v{version.version_number}
                                                {version.is_current && <Badge variant="default" className="ml-0.5 text-[10px]">当前</Badge>}
                                              </TabsTrigger>
                                            ))}
                                          </TabsList>
                                          {keyframe.versions.map((version: any) => (
                                            <TabsContent key={version.uuid} value={`v${version.version_number}`} className="space-y-2 mt-2">
                                              {version.keyframe_url && <img src={version.keyframe_url} alt={`Shot ${shotNum} v${version.version_number}`} className="w-full aspect-video object-cover rounded border" />}
                                              {version.t2i_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{version.t2i_prompt}</div>}
                                              <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                <div className="font-medium text-muted-foreground">工具与参数</div>
                                                <div className="space-y-0.5">
                                                  {version.model != null && version.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{version.model}</span></div>}
                                                  {version.image_generation_tool != null && version.image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{version.image_generation_tool}</span></div>}
                                                  {version.provider != null && version.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{version.provider}</span></div>}
                                                  {version.aspect_ratio != null && version.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{version.aspect_ratio}</span></div>}
                                                  {version.resolution != null && version.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{version.resolution}</span></div>}
                                                  {version.seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{version.seed}</span></div>}
                                                </div>
                                                <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(version.error_msg != null && version.error_msg !== '') ? version.error_msg : '—'}</div>
                                                <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(version.raw_error_msg != null && version.raw_error_msg !== '') ? version.raw_error_msg : '—'}</div>
                                              </div>
                                              <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                <div className="font-medium text-muted-foreground">Metrics</div>
                                                <div className="space-y-0.5">
                                                  <div>耗时: {getDurationSec(version) != null ? `${getDurationSec(version)!.toFixed(2)}s` : '—'}</div>
                                                  <div>成本: {getCost(version) != null ? `$${getCost(version)!.toFixed(4)}` : '—'}</div>
                                                </div>
                                                <div className="pt-0.5 border-t border-border/50">
                                                  <ConsistencyBlock metrics={version.image_tool_metrics} consistencyDisplayLines={version.consistency_display_lines} />
                                                </div>
                                              </div>
                                              {((version.reference_image_urls && version.reference_image_urls.length > 0) || (version.character_version_ids && version.character_version_ids.length > 0)) && (
                                                <div className="border rounded p-2 bg-muted/30 text-xs">
                                                  <div className="font-medium text-muted-foreground mb-1">依赖</div>
                                                  {version.reference_image_urls?.length > 0 && (
                                                    <div className="flex flex-wrap gap-1">
                                                      {version.reference_image_urls.map((url: string, i: number) => (
                                                        <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-10 h-10 rounded border overflow-hidden flex-shrink-0">
                                                          <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                                        </a>
                                                      ))}
                                                    </div>
                                                  )}
                                                  {version.character_version_ids?.length > 0 && (
                                                    <div className="flex flex-wrap gap-1 mt-1">
                                                      {version.character_version_ids.map((vid: string) => {
                                                        const cinfo = characterVersionMap[vid];
                                                        return <Badge key={vid} variant="outline" className="text-xs font-normal">{cinfo ? `${cinfo.name} v${cinfo.version_number}` : vid}</Badge>;
                                                      })}
                                                    </div>
                                                  )}
                                                </div>
                                              )}
                                              <UuidRow table="video_keyframe_versions" uuid={version.uuid} />
                                              <Button variant="outline" size="sm" className="mt-1 text-xs" onClick={() => { setAddToConsistencyType('image'); setAddToConsistencyVersionUuid(version.uuid); setAddToConsistencyShotNumber(shotNum); setAddToConsistencyPromptSource('attempt_1'); setAddToConsistencyOpen(true); }}>
                                                加入图片一致性测试集
                                              </Button>
                                            </TabsContent>
                                          ))}
                                        </Tabs>
                                      ) : (
                                        <>
                                          {(keyframe.versions && keyframe.versions.length > 0) ? (() => {
                                            const ver = keyframe.versions[0];
                                            return (
                                              <>
                                                {ver?.keyframe_url && <img src={ver.keyframe_url} alt={`Shot ${shotNum}`} className="w-full aspect-video object-cover rounded border" />}
                                                {ver?.t2i_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{ver.t2i_prompt}</div>}
                                                <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                  <div className="font-medium text-muted-foreground">工具与参数</div>
                                                  <div className="space-y-0.5">
                                                    {ver?.model != null && ver.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{ver.model}</span></div>}
                                                    {ver?.image_generation_tool != null && ver.image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{ver.image_generation_tool}</span></div>}
                                                    {ver?.provider != null && ver.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{ver.provider}</span></div>}
                                                    {ver?.aspect_ratio != null && ver.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{ver.aspect_ratio}</span></div>}
                                                    {ver?.resolution != null && ver.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{ver.resolution}</span></div>}
                                                    {ver?.seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{ver.seed}</span></div>}
                                                  </div>
                                                  <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(ver?.error_msg != null && ver?.error_msg !== '') ? ver.error_msg : '—'}</div>
                                                  <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(ver?.raw_error_msg != null && ver?.raw_error_msg !== '') ? ver.raw_error_msg : '—'}</div>
                                                </div>
                                                {((ver?.reference_image_urls && ver.reference_image_urls.length > 0) || (ver?.character_version_ids && ver.character_version_ids.length > 0)) && (
                                                  <div className="border rounded p-2 bg-muted/30 text-xs">
                                                    <div className="font-medium text-muted-foreground mb-1">依赖</div>
                                                    {ver?.reference_image_urls?.length > 0 && (
                                                      <div className="flex flex-wrap gap-1">
                                                        {ver.reference_image_urls.map((url: string, i: number) => (
                                                          <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-10 h-10 rounded border overflow-hidden flex-shrink-0">
                                                            <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                                          </a>
                                                        ))}
                                                      </div>
                                                    )}
                                                    {ver?.character_version_ids?.length > 0 && (
                                                      <div className="flex flex-wrap gap-1 mt-1">
                                                        {ver.character_version_ids.map((vid: string) => {
                                                          const cinfo = characterVersionMap[vid];
                                                          return <Badge key={vid} variant="outline" className="text-xs font-normal">{cinfo ? `${cinfo.name} v${cinfo.version_number}` : vid}</Badge>;
                                                        })}
                                                      </div>
                                                    )}
                                                  </div>
                                                )}
                                                <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                  <div className="font-medium text-muted-foreground">Metrics</div>
                                                  <div className="space-y-0.5">
                                                    <div>耗时: {getDurationSec(ver) != null ? `${getDurationSec(ver)!.toFixed(2)}s` : '—'}</div>
                                                    <div>成本: {getCost(ver) != null ? `$${getCost(ver)!.toFixed(4)}` : '—'}</div>
                                                  </div>
                                                  <div className="pt-0.5 border-t border-border/50">
                                                    <ConsistencyBlock metrics={ver?.image_tool_metrics} consistencyDisplayLines={ver?.consistency_display_lines} />
                                                  </div>
                                                </div>
                                                {ver?.uuid && <UuidRow table="video_keyframe_versions" uuid={ver.uuid} />}
                                                {ver?.uuid && (
                                                  <Button variant="outline" size="sm" className="mt-1 text-xs" onClick={() => { setAddToConsistencyType('image'); setAddToConsistencyVersionUuid(ver.uuid); setAddToConsistencyShotNumber(shotNum); setAddToConsistencyPromptSource('attempt_1'); setAddToConsistencyOpen(true); }}>
                                                    加入图片一致性测试集
                                                  </Button>
                                                )}
                                              </>
                                            );
                                          })() : (
                                            <>
                                              {keyframe.keyframe_url && <img src={keyframe.keyframe_url} alt={`Shot ${shotNum}`} className="w-full aspect-video object-cover rounded border" />}
                                              {keyframe.t2i_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{keyframe.t2i_prompt}</div>}
                                              {keyframe.versions?.[0] && (
                                                <div className="border rounded p-2 bg-muted/30 text-xs space-y-1">
                                                  <div className="font-medium text-muted-foreground">工具与参数</div>
                                                  <div className="flex flex-wrap gap-x-2 gap-y-1">
                                                    {keyframe.versions[0].model && <span>模型: {keyframe.versions[0].model}</span>}
                                                    {keyframe.versions[0].image_generation_tool && <span>工具: {keyframe.versions[0].image_generation_tool}</span>}
                                                    {keyframe.versions[0].provider && <span>Provider: {keyframe.versions[0].provider}</span>}
                                                    {keyframe.versions[0].aspect_ratio && <span>比例: {keyframe.versions[0].aspect_ratio}</span>}
                                                    {keyframe.versions[0].resolution != null && <span>分辨率: {keyframe.versions[0].resolution}</span>}
                                                    {keyframe.versions[0].seed != null && <span>Seed: {keyframe.versions[0].seed}</span>}
                                                  </div>
                                                  <div className="text-red-500">错误: {(keyframe.versions[0].error_msg != null && keyframe.versions[0].error_msg !== '') ? keyframe.versions[0].error_msg : '—'}</div>
                                                  <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(keyframe.versions[0].raw_error_msg != null && keyframe.versions[0].raw_error_msg !== '') ? keyframe.versions[0].raw_error_msg : '—'}</div>
                                                </div>
                                              )}
                                              {keyframe.versions?.[0] && (
                                                <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                  <div className="font-medium text-muted-foreground">Metrics</div>
                                                  <div className="space-y-0.5">
                                                    <div>耗时: {getDurationSec(keyframe.versions[0]) != null ? `${getDurationSec(keyframe.versions[0])!.toFixed(2)}s` : '—'}</div>
                                                    <div>成本: {getCost(keyframe.versions[0]) != null ? `$${getCost(keyframe.versions[0])!.toFixed(4)}` : '—'}</div>
                                                  </div>
                                                  <div className="pt-0.5 border-t border-border/50">
                                                    <ConsistencyBlock metrics={keyframe.versions[0].image_tool_metrics} consistencyDisplayLines={keyframe.versions[0].consistency_display_lines} />
                                                  </div>
                                                </div>
                                              )}
                                              {keyframe.versions?.[0]?.uuid && (
                                                <Button variant="outline" size="sm" className="mt-1 text-xs" onClick={() => { setAddToConsistencyType('image'); setAddToConsistencyVersionUuid(keyframe.versions[0].uuid); setAddToConsistencyShotNumber(shotNum); setAddToConsistencyPromptSource('attempt_1'); setAddToConsistencyOpen(true); }}>
                                                  加入图片一致性测试集
                                                </Button>
                                              )}
                                            </>
                                          )}
                                        </>
                                      )
                                    ) : (
                                      <div className="text-xs text-muted-foreground">暂无关键帧</div>
                                    )}
                                  </div>
                                  {/* 右：视频（与视频 tab 一致：含版本切换 + 播放器 + motion prompt + 工具与参数 + 关键帧图） */}
                                  <div className="rounded-lg border bg-muted/20 p-3 space-y-2 min-h-[120px]">
                                    <div className="text-sm font-semibold text-muted-foreground border-b pb-1">视频</div>
                                    {video ? (
                                      (() => {
                                        const vVersions = video.versions || [];
                                        if (vVersions.length === 0) return <div className="text-xs text-muted-foreground">暂无版本</div>;
                                        if (vVersions.length > 1) {
                                          const currentVer = vVersions.find((v: any) => v.is_current) || vVersions[vVersions.length - 1];
                                          return (
                                            <Tabs
                                              value={selectedVideoVersion[video.uuid] ? `v-${selectedVideoVersion[video.uuid]}` : (currentVer ? `v-${currentVer.uuid}` : undefined)}
                                              onValueChange={(val) => {
                                                const uuid = val.replace(/^v-/, '');
                                                if (uuid) setSelectedVideoVersion((prev) => ({ ...prev, [video.uuid]: uuid }));
                                              }}
                                              className="w-full"
                                            >
                                              <TabsList className="grid w-full grid-cols-4 max-h-8 text-xs" style={{ gridTemplateColumns: `repeat(${Math.min(vVersions.length, 6)}, 1fr)` }}>
                                                {vVersions.map((version: any) => (
                                                  <TabsTrigger key={version.uuid} value={`v-${version.uuid}`} className="text-xs truncate">
                                                    v{version.version_number}
                                                    {version.is_current && <Badge variant="default" className="ml-0.5 text-[10px]">当前</Badge>}
                                                  </TabsTrigger>
                                                ))}
                                              </TabsList>
                                              {vVersions.map((version: any) => (
                                                <TabsContent key={version.uuid} value={`v-${version.uuid}`} className="space-y-2 mt-2">
                                                  {version.video_url && <LazyVideo src={version.video_url} className="w-full aspect-video rounded border" />}
                                                  <div className="text-xs text-muted-foreground">时长: {version.duration}s</div>
                                                  {version.motion_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{version.motion_prompt}</div>}
                                                  {version.keyframe_url && (
                                                    <div className="space-y-0.5">
                                                      <div className="text-xs font-medium text-muted-foreground">关键帧</div>
                                                      <img src={version.keyframe_url} alt={`Shot ${shotNum} 关键帧`} className="w-full max-h-32 object-contain rounded border bg-muted/50" />
                                                      <a href={version.keyframe_url} target="_blank" rel="noopener noreferrer" className="text-xs text-primary underline">链接</a>
                                                    </div>
                                                  )}
                                                  <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                    <div className="font-medium text-muted-foreground">工具与参数</div>
                                                    <div className="space-y-0.5">
                                                      {version.model != null && version.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{version.model}</span></div>}
                                                      {version.video_generation_tool != null && version.video_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{version.video_generation_tool}</span></div>}
                                                      {version.provider != null && version.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{version.provider}</span></div>}
                                                      {version.resolution != null && version.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{version.resolution}</span></div>}
                                                      {version.aspect_ratio != null && version.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{version.aspect_ratio}</span></div>}
                                                      {version.generation_mode != null && version.generation_mode !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模式:</span><span className="break-words">{version.generation_mode}</span></div>}
                                                      <div className="leading-relaxed flex gap-1.5 flex-wrap pt-0.5 border-t border-border/50"><span className="text-muted-foreground font-medium shrink-0">原因:</span><span className="break-words">{(version.consistency_reason != null && String(version.consistency_reason).trim() !== '') ? version.consistency_reason : '—'}</span></div>
                                                    </div>
                                                    <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(version.error_msg != null && version.error_msg !== '') ? version.error_msg : '—'}</div>
                                                    <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(version.raw_error_msg != null && version.raw_error_msg !== '') ? version.raw_error_msg : '—'}</div>
                                                  </div>
                                                  <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                                    <div className="font-medium text-muted-foreground">Metrics</div>
                                                    <div className="space-y-0.5">
                                                      <div>耗时: {getDurationSec(version) != null ? `${getDurationSec(version)!.toFixed(2)}s` : '—'}</div>
                                                      <div>成本: {getCost(version) != null ? `$${getCost(version)!.toFixed(4)}` : '—'}</div>
                                                    </div>
                                                    <div className="pt-0.5 border-t border-border/50">
                                                      <ConsistencyBlock metrics={version.video_tool_metrics as Record<string, unknown>} consistencyDisplayLines={version.consistency_display_lines} />
                                                    </div>
                                                  </div>
                                                  <UuidRow table="video_generation_versions" uuid={version.uuid} />
                                                  <Button variant="outline" size="sm" className="mt-1 text-xs" onClick={() => { setAddToConsistencyType('video'); setAddToConsistencyVersionUuid(version.uuid); setAddToConsistencyShotNumber(shotNum); setAddToConsistencyPromptSource('attempt_1'); setAddToConsistencyOpen(true); }}>
                                                    加入视频一致性测试集
                                                  </Button>
                                                </TabsContent>
                                              ))}
                                            </Tabs>
                                          );
                                        }
                                        const vVer = vVersions[0];
                                        return (
                                          <>
                                            {vVer.video_url && <LazyVideo src={vVer.video_url} className="w-full aspect-video rounded border" />}
                                            <div className="text-xs text-muted-foreground">时长: {vVer.duration}s</div>
                                            {vVer.motion_prompt && <div className="text-xs p-2 bg-secondary rounded break-words">{vVer.motion_prompt}</div>}
                                            {vVer.keyframe_url && (
                                              <div className="space-y-0.5">
                                                <div className="text-xs font-medium text-muted-foreground">关键帧</div>
                                                <img src={vVer.keyframe_url} alt={`Shot ${shotNum} 关键帧`} className="w-full max-h-32 object-contain rounded border bg-muted/50" />
                                                <a href={vVer.keyframe_url} target="_blank" rel="noopener noreferrer" className="text-xs text-primary underline">链接</a>
                                              </div>
                                            )}
                                            <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                              <div className="font-medium text-muted-foreground">工具与参数</div>
                                              <div className="space-y-0.5">
                                                {vVer.model != null && vVer.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{vVer.model}</span></div>}
                                                {vVer.video_generation_tool != null && vVer.video_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{vVer.video_generation_tool}</span></div>}
                                                {vVer.provider != null && vVer.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{vVer.provider}</span></div>}
                                                {vVer.resolution != null && vVer.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{vVer.resolution}</span></div>}
                                                {vVer.aspect_ratio != null && vVer.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{vVer.aspect_ratio}</span></div>}
                                                {vVer.generation_mode != null && vVer.generation_mode !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模式:</span><span className="break-words">{vVer.generation_mode}</span></div>}
                                                <div className="leading-relaxed flex gap-1.5 flex-wrap pt-0.5 border-t border-border/50"><span className="text-muted-foreground font-medium shrink-0">原因:</span><span className="break-words">{(vVer.consistency_reason != null && String(vVer.consistency_reason).trim() !== '') ? vVer.consistency_reason : '—'}</span></div>
                                              </div>
                                              <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(vVer.error_msg != null && vVer.error_msg !== '') ? vVer.error_msg : '—'}</div>
                                              <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(vVer.raw_error_msg != null && vVer.raw_error_msg !== '') ? vVer.raw_error_msg : '—'}</div>
                                            </div>
                                            <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                              <div className="font-medium text-muted-foreground">Metrics</div>
                                              <div className="space-y-0.5">
                                                <div>耗时: {getDurationSec(vVer) != null ? `${getDurationSec(vVer)!.toFixed(2)}s` : '—'}</div>
                                                <div>成本: {getCost(vVer) != null ? `$${getCost(vVer)!.toFixed(4)}` : '—'}</div>
                                              </div>
                                              <div className="pt-0.5 border-t border-border/50">
                                                <ConsistencyBlock metrics={vVer.video_tool_metrics as Record<string, unknown>} consistencyDisplayLines={vVer.consistency_display_lines} />
                                              </div>
                                            </div>
                                            <UuidRow table="video_generation_versions" uuid={vVer.uuid} />
                                            <Button variant="outline" size="sm" className="mt-1 text-xs" onClick={() => { setAddToConsistencyType('video'); setAddToConsistencyVersionUuid(vVer.uuid); setAddToConsistencyShotNumber(shotNum); setAddToConsistencyPromptSource('attempt_1'); setAddToConsistencyOpen(true); }}>
                                              加入视频一致性测试集
                                            </Button>
                                          </>
                                        );
                                      })()
                                    ) : (
                                      <div className="text-xs text-muted-foreground">暂无视频</div>
                                    )}
                                  </div>
                                </div>
                              </CardContent>
                            </Card>
                          );
                        })}
                      </div>
                    );
                  })()}
                </TabsContent>

                <TabsContent value="keyframes" className="space-y-4">
                  {taskData.keyframes_data && taskData.keyframes_data.length > 0 ? (
                    <div className="grid grid-cols-2 gap-4">
                      {taskData.keyframes_data.map((keyframe: any) => (
                        <Card key={keyframe.uuid}>
                          <CardHeader>
                            <CardTitle className="text-base flex items-center gap-2">
                              <Image className="w-4 h-4" />
                              Shot {keyframe.shot_number}
                            </CardTitle>
                            <div className="text-xs text-muted-foreground space-y-1 mt-1">
                              {keyframe.uuid && <UuidRow table="video_keyframes" uuid={keyframe.uuid} />}
                              {keyframe.versions?.map((v: any) => v.uuid).filter(Boolean).map((uid: string) => (
                                <UuidRow key={uid} table="video_keyframe_versions" uuid={uid} />
                              ))}
                            </div>
                          </CardHeader>
                          <CardContent className="space-y-3">
                            {/* 版本选择 */}
                            {keyframe.versions && keyframe.versions.length > 1 ? (
                              <Tabs defaultValue={keyframe.versions.findIndex((v: any) => v.is_current) >= 0 ? `v${keyframe.versions[keyframe.versions.findIndex((v: any) => v.is_current)].version_number}` : `v${keyframe.versions[0].version_number}`} className="w-full">
                                <TabsList className="grid w-full" style={{ gridTemplateColumns: `repeat(${keyframe.versions.length}, 1fr)` }}>
                                  {keyframe.versions.map((version: any) => (
                                    <TabsTrigger key={version.uuid} value={`v${version.version_number}`} className="text-xs">
                                      v{version.version_number}
                                      {version.is_current && <Badge variant="default" className="ml-1 text-xs">当前</Badge>}
                                    </TabsTrigger>
                                  ))}
                                </TabsList>
                                {keyframe.versions.map((version: any) => (
                                  <TabsContent key={version.uuid} value={`v${version.version_number}`} className="space-y-3 mt-3">
                                    {version.keyframe_url && (
                                      <img
                                        src={version.keyframe_url}
                                        alt={`Keyframe ${keyframe.shot_number} v${version.version_number}`}
                                        className="w-full aspect-video object-cover rounded-md"
                                      />
                                    )}
                                    {version.t2i_prompt && (
                                      <div className="text-xs text-muted-foreground p-2 bg-secondary rounded break-words">
                                        {version.t2i_prompt}
                                      </div>
                                    )}
                                    {/* 关键帧版本：工具与参数（与视频一致：每项一行、标签 muted） */}
                                    <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                      <div className="font-medium text-muted-foreground">工具与参数</div>
                                      <div className="space-y-0.5">
                                        {version.model != null && version.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{version.model}</span></div>}
                                        {version.image_generation_tool != null && version.image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{version.image_generation_tool}</span></div>}
                                        {version.provider != null && version.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{version.provider}</span></div>}
                                        {version.aspect_ratio != null && version.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{version.aspect_ratio}</span></div>}
                                        {version.resolution != null && version.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{version.resolution}</span></div>}
                                        {version.seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{version.seed}</span></div>}
                                      </div>
                                      <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(version.error_msg != null && version.error_msg !== '') ? version.error_msg : '—'}</div>
                                      <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(version.raw_error_msg != null && version.raw_error_msg !== '') ? version.raw_error_msg : '—'}</div>
                                    </div>
                                    <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                      <div className="font-medium text-muted-foreground">Metrics</div>
                                      <div className="space-y-0.5">
                                        <div>耗时: {getDurationSec(version) != null ? `${getDurationSec(version)!.toFixed(2)}s` : '—'}</div>
                                        <div>成本: {getCost(version) != null ? `$${getCost(version)!.toFixed(4)}` : '—'}</div>
                                      </div>
                                      <div className="pt-0.5 border-t border-border/50">
                                        <ConsistencyBlock metrics={version.image_tool_metrics} consistencyDisplayLines={version.consistency_display_lines} />
                                      </div>
                                    </div>
                                    {/* 关键帧依赖图：参考图 + 依赖的角色版本 */}
                                    {((version.reference_image_urls && version.reference_image_urls.length > 0) || (version.character_version_ids && version.character_version_ids.length > 0)) && (
                                      <div className="border rounded-lg p-2 bg-muted/30 space-y-2">
                                        <div className="text-xs font-medium text-muted-foreground">依赖</div>
                                        {version.reference_image_urls && version.reference_image_urls.length > 0 && (
                                          <div className="flex flex-wrap gap-1 items-center">
                                            <span className="text-xs text-muted-foreground mr-1">参考图:</span>
                                            {version.reference_image_urls.map((url: string, i: number) => (
                                              <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-12 h-12 rounded border overflow-hidden flex-shrink-0 hover:opacity-80">
                                                <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                              </a>
                                            ))}
                                          </div>
                                        )}
                                        {version.character_version_ids && version.character_version_ids.length > 0 && (
                                          <div className="flex flex-wrap gap-1 items-center">
                                            <span className="text-xs text-muted-foreground mr-1">角色版本:</span>
                                            {version.character_version_ids.map((vid: string) => {
                                              const info = characterVersionMap[vid];
                                              return (
                                                <Badge key={vid} variant="outline" className="text-xs font-normal">
                                                  {info ? `${info.name} v${info.version_number}` : vid}
                                                </Badge>
                                              );
                                            })}
                                          </div>
                                        )}
                                      </div>
                                    )}
                                  </TabsContent>
                                ))}
                              </Tabs>
                            ) : (
                              <>
                                {keyframe.keyframe_url && (
                                  <img
                                    src={keyframe.keyframe_url}
                                    alt={`Keyframe ${keyframe.shot_number}`}
                                    className="w-full aspect-video object-cover rounded-md"
                                  />
                                )}
                                {keyframe.t2i_prompt && (
                                  <div className="text-xs text-muted-foreground">
                                    {keyframe.t2i_prompt}
                                  </div>
                                )}
                                {/* 单版本时的工具与参数 + 错误 + Metrics */}
                                {keyframe.versions && keyframe.versions[0] && (
                                  <>
                                    {(keyframe.versions[0].model || keyframe.versions[0].image_generation_tool || keyframe.versions[0].provider) && (
                                      <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                        <div className="font-medium text-muted-foreground">工具与参数</div>
                                        <div className="space-y-0.5">
                                          {keyframe.versions[0].model != null && keyframe.versions[0].model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{keyframe.versions[0].model}</span></div>}
                                          {keyframe.versions[0].image_generation_tool != null && keyframe.versions[0].image_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{keyframe.versions[0].image_generation_tool}</span></div>}
                                          {keyframe.versions[0].provider != null && keyframe.versions[0].provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{keyframe.versions[0].provider}</span></div>}
                                          {keyframe.versions[0].aspect_ratio != null && keyframe.versions[0].aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{keyframe.versions[0].aspect_ratio}</span></div>}
                                          {keyframe.versions[0].resolution != null && keyframe.versions[0].resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{keyframe.versions[0].resolution}</span></div>}
                                          {keyframe.versions[0].seed != null && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Seed:</span><span>{keyframe.versions[0].seed}</span></div>}
                                        </div>
                                        <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(keyframe.versions[0].error_msg != null && keyframe.versions[0].error_msg !== '') ? keyframe.versions[0].error_msg : '—'}</div>
                                        <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(keyframe.versions[0].raw_error_msg != null && keyframe.versions[0].raw_error_msg !== '') ? keyframe.versions[0].raw_error_msg : '—'}</div>
                                      </div>
                                    )}
                                    <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                      <div className="font-medium text-muted-foreground">Metrics</div>
                                      <div className="space-y-0.5">
                                        <div>耗时: {getDurationSec(keyframe.versions[0]) != null ? `${getDurationSec(keyframe.versions[0])!.toFixed(2)}s` : '—'}</div>
                                        <div>成本: {getCost(keyframe.versions[0]) != null ? `$${getCost(keyframe.versions[0])!.toFixed(4)}` : '—'}</div>
                                      </div>
                                      <div className="pt-0.5 border-t border-border/50">
                                        <ConsistencyBlock metrics={keyframe.versions[0].image_tool_metrics} consistencyDisplayLines={keyframe.versions[0].consistency_display_lines} />
                                      </div>
                                    </div>
                                  </>
                                )}
                                {/* 单版本时的依赖图 */}
                                {((keyframe.reference_image_urls && keyframe.reference_image_urls.length > 0) || (keyframe.character_version_ids && keyframe.character_version_ids.length > 0)) && (
                                  <div className="border rounded-lg p-2 bg-muted/30 space-y-2">
                                    <div className="text-xs font-medium text-muted-foreground">依赖</div>
                                    {keyframe.reference_image_urls && keyframe.reference_image_urls.length > 0 && (
                                      <div className="flex flex-wrap gap-1 items-center">
                                        <span className="text-xs text-muted-foreground mr-1">参考图:</span>
                                        {keyframe.reference_image_urls.map((url: string, i: number) => (
                                          <a key={i} href={url} target="_blank" rel="noopener noreferrer" className="block w-12 h-12 rounded border overflow-hidden flex-shrink-0 hover:opacity-80">
                                            <img src={url} alt={`参考${i + 1}`} className="w-full h-full object-cover" />
                                          </a>
                                        ))}
                                      </div>
                                    )}
                                    {keyframe.character_version_ids && keyframe.character_version_ids.length > 0 && (
                                      <div className="flex flex-wrap gap-1 items-center">
                                        <span className="text-xs text-muted-foreground mr-1">角色版本:</span>
                                        {keyframe.character_version_ids.map((vid: string) => {
                                          const info = characterVersionMap[vid];
                                          return (
                                            <Badge key={vid} variant="outline" className="text-xs font-normal">
                                              {info ? `${info.name} v${info.version_number}` : vid}
                                            </Badge>
                                          );
                                        })}
                                      </div>
                                    )}
                                  </div>
                                )}
                              </>
                            )}
                          </CardContent>
                        </Card>
                      ))}
                    </div>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无关键帧数据
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                <TabsContent value="videos" className="space-y-4">
                  {taskData.videos_data && taskData.videos_data.length > 0 ? (
                    <>
                      <div className="flex items-center justify-between">
                        <p className="text-sm text-muted-foreground">按当前选中 shot 版本先 sync 再合成最终视频（与用户端「合并视频」一致）</p>
                        <Button
                          size="sm"
                          disabled={regeneratingVideos || !taskData.task_id}
                          onClick={async () => {
                            if (!taskData.task_id) {
                              toast({ title: "无法合并视频", description: "缺少 run_id", variant: "destructive" });
                              return;
                            }
                            setRegeneratingVideos(true);
                            try {
                              const videos = taskData.videos_data.map((video: any) => {
                                const versions = video.versions || [];
                                const selected = selectedVideoVersion[video.uuid]
                                  ? versions.find((v: any) => v.uuid === selectedVideoVersion[video.uuid])
                                  : versions.find((v: any) => v.is_current) || versions[0];
                                if (!selected?.uuid) return null;
                                return { uuid: video.uuid, selected_version: { uuid: selected.uuid } };
                              }).filter(Boolean);
                              if (videos.length === 0) {
                                toast({ title: "无法合并视频", description: "没有可用的视频版本", variant: "destructive" });
                                return;
                              }
                              await api.admin.adminVideoAssembly({
                                run_id: taskData.task_id,
                                videos,
                              });
                              toast({ title: "合并视频成功", description: "已按选中版本合成最终视频，请刷新查看" });
                              loadTaskDetail();
                            } catch (e: any) {
                              toast({
                                title: "合并视频失败",
                                description: e?.message || String(e),
                                variant: "destructive",
                              });
                            } finally {
                              setRegeneratingVideos(false);
                            }
                          }}
                        >
                          {regeneratingVideos ? <Loader2 className="w-4 h-4 animate-spin mr-1" /> : <RefreshCw className="w-4 h-4 mr-1" />}
                          合并视频
                        </Button>
                      </div>
                      <div className="grid grid-cols-2 gap-4">
                        {taskData.videos_data.map((video: any) => (
                          <Card key={video.uuid}>
                            <CardHeader>
                              <CardTitle className="text-base flex items-center gap-2">
                                <Video className="w-4 h-4" />
                                Shot {video.shot_number}
                              </CardTitle>
                              <CardDescription className="text-xs">
                                时长: {video.duration}s
                                <div className="mt-1 space-y-1">
                                  {video.uuid && <UuidRow table="video_generations" uuid={video.uuid} />}
                                  {video.versions?.map((v: any) => v.uuid).filter(Boolean).map((uid: string) => (
                                    <UuidRow key={uid} table="video_generation_versions" uuid={uid} />
                                  ))}
                                </div>
                              </CardDescription>
                            </CardHeader>
                            <CardContent className="space-y-3">
                              {/* 版本选择 */}
                              {video.versions && video.versions.length > 1 ? (
                                <Tabs
                                  value={selectedVideoVersion[video.uuid] ? `v-${selectedVideoVersion[video.uuid]}` : (() => {
                                    const current = video.versions.find((v: any) => v.is_current) || video.versions[0];
                                    return current ? `v-${current.uuid}` : undefined;
                                  })()}
                                  onValueChange={(val) => {
                                    const uuid = val.replace(/^v-/, '');
                                    if (uuid) setSelectedVideoVersion((prev) => ({ ...prev, [video.uuid]: uuid }));
                                  }}
                                  className="w-full"
                                >
                                  <TabsList className="grid w-full" style={{ gridTemplateColumns: `repeat(${video.versions.length}, 1fr)` }}>
                                    {video.versions.map((version: any) => (
                                      <TabsTrigger key={version.uuid} value={`v-${version.uuid}`} className="text-xs">
                                        v{version.version_number}
                                        {version.is_current && <Badge variant="default" className="ml-1 text-xs">当前</Badge>}
                                      </TabsTrigger>
                                    ))}
                                  </TabsList>
                                  {video.versions.map((version: any) => (
                                    <TabsContent key={version.uuid} value={`v-${version.uuid}`} className="space-y-3 mt-3">
                                      {version.video_url && (
                                        <LazyVideo
                                          src={version.video_url}
                                          className="w-full aspect-video rounded-md"
                                        />
                                      )}
                                      <div className="text-xs text-muted-foreground">
                                        时长: {version.duration}s
                                      </div>
                                      {version.motion_prompt && (
                                        <div className="text-xs text-muted-foreground p-2 bg-secondary rounded break-words">
                                          {version.motion_prompt}
                                        </div>
                                      )}
                                      {/* 关键帧：只展示图，与关键帧 tab 一致 */}
                                      {version.keyframe_url && (
                                        <div className="space-y-1">
                                          <div className="text-xs font-medium text-muted-foreground">关键帧</div>
                                          <img
                                            src={version.keyframe_url}
                                            alt={`Shot ${video.shot_number} 关键帧`}
                                            className="w-full max-h-48 object-contain rounded border bg-muted/50"
                                          />
                                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                            <a href={version.keyframe_url} target="_blank" rel="noopener noreferrer" className="text-primary underline">链接</a>
                                            {version.keyframe_version_ids && version.keyframe_version_ids.length > 0 && (
                                              <span className="truncate">版本: {version.keyframe_version_ids.join(', ')}</span>
                                            )}
                                          </div>
                                        </div>
                                      )}
                                      {/* 工具与参数：与关键帧一致，每项一行、标签 muted */}
                                      <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                        <div className="font-medium text-muted-foreground">工具与参数</div>
                                        <div className="space-y-0.5">
                                          {version.model != null && version.model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{version.model}</span></div>}
                                          {version.video_generation_tool != null && version.video_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{version.video_generation_tool}</span></div>}
                                          {version.provider != null && version.provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{version.provider}</span></div>}
                                          {version.resolution != null && version.resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{version.resolution}</span></div>}
                                          {version.aspect_ratio != null && version.aspect_ratio !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">比例:</span><span className="break-words">{version.aspect_ratio}</span></div>}
                                          {version.generation_mode != null && version.generation_mode !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模式:</span><span className="break-words">{version.generation_mode}</span></div>}
                                        </div>
                                        <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(version.error_msg != null && version.error_msg !== '') ? version.error_msg : '—'}</div>
                                        <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(version.raw_error_msg != null && version.raw_error_msg !== '') ? version.raw_error_msg : '—'}</div>
                                      </div>
                                      <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                        <div className="font-medium text-muted-foreground">Metrics</div>
                                        <div className="space-y-0.5">
                                          <div>耗时: {getDurationSec(version) != null ? `${getDurationSec(version)!.toFixed(2)}s` : '—'}</div>
                                          <div>成本: {getCost(version) != null ? `$${getCost(version)!.toFixed(4)}` : '—'}</div>
                                        </div>
                                        <div className="pt-0.5 border-t border-border/50">
                                          <ConsistencyBlock metrics={version.video_tool_metrics as Record<string, unknown>} consistencyDisplayLines={version.consistency_display_lines} />
                                        </div>
                                      </div>
                                    </TabsContent>
                                  ))}
                                </Tabs>
                              ) : (
                                <>
                                  {video.video_url && (
                                    <LazyVideo
                                      src={video.video_url}
                                      className="w-full aspect-video rounded-md"
                                    />
                                  )}
                                  <div className="text-xs text-muted-foreground">时长: {video.duration}s</div>
                                  {video.motion_prompt && <div className="text-xs text-muted-foreground p-2 bg-secondary rounded break-words">{video.motion_prompt}</div>}
                                  {video.versions && video.versions[0] && (
                                    <>
                                      {video.versions[0].keyframe_url && (
                                        <div className="space-y-1">
                                          <div className="text-xs font-medium text-muted-foreground">关键帧</div>
                                          <img src={video.versions[0].keyframe_url} alt={`Shot ${video.shot_number} 关键帧`} className="w-full max-h-48 object-contain rounded border bg-muted/50" />
                                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                                            <a href={video.versions[0].keyframe_url} target="_blank" rel="noopener noreferrer" className="text-primary underline">链接</a>
                                            {video.versions[0].keyframe_version_ids && video.versions[0].keyframe_version_ids.length > 0 && (
                                              <span className="truncate">版本: {video.versions[0].keyframe_version_ids.join(', ')}</span>
                                            )}
                                          </div>
                                        </div>
                                      )}
                                      <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                        <div className="font-medium text-muted-foreground">工具与参数</div>
                                        <div className="space-y-0.5">
                                          {video.versions[0].model != null && video.versions[0].model !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模型:</span><span className="break-words">{video.versions[0].model}</span></div>}
                                          {video.versions[0].video_generation_tool != null && video.versions[0].video_generation_tool !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">工具:</span><span className="break-words">{video.versions[0].video_generation_tool}</span></div>}
                                          {video.versions[0].provider != null && video.versions[0].provider !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">Provider:</span><span className="break-words">{video.versions[0].provider}</span></div>}
                                          {video.versions[0].resolution != null && video.versions[0].resolution !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">分辨率:</span><span className="break-words">{video.versions[0].resolution}</span></div>}
                                          {video.versions[0].generation_mode != null && video.versions[0].generation_mode !== '' && <div className="leading-relaxed flex gap-1.5 flex-wrap"><span className="text-muted-foreground font-medium shrink-0">模式:</span><span className="break-words">{video.versions[0].generation_mode}</span></div>}
                                        </div>
                                        <div className="text-red-500 pt-0.5 border-t border-border/50">错误: {(video.versions[0].error_msg != null && video.versions[0].error_msg !== '') ? video.versions[0].error_msg : '—'}</div>
                                        <div className="text-amber-600 text-[11px] font-mono break-all">原始错误: {(video.versions[0].raw_error_msg != null && video.versions[0].raw_error_msg !== '') ? video.versions[0].raw_error_msg : '—'}</div>
                                      </div>
                                      <div className="border rounded p-2.5 bg-muted/30 text-xs space-y-2">
                                        <div className="font-medium text-muted-foreground">Metrics</div>
                                        <div className="space-y-0.5">
                                          <div>耗时: {getDurationSec(video.versions[0]) != null ? `${getDurationSec(video.versions[0])!.toFixed(2)}s` : '—'}</div>
                                          <div>成本: {getCost(video.versions[0]) != null ? `$${getCost(video.versions[0])!.toFixed(4)}` : '—'}</div>
                                        </div>
                                        <div className="pt-0.5 border-t border-border/50">
                                          <ConsistencyBlock metrics={video.versions[0].video_tool_metrics as Record<string, unknown>} consistencyDisplayLines={video.versions[0].consistency_display_lines} />
                                        </div>
                                      </div>
                                    </>
                                  )}
                                </>
                              )}
                            </CardContent>
                          </Card>
                        ))}
                      </div>
                    </>
                  ) : (
                    <Card>
                      <CardContent className="py-8 text-center text-muted-foreground">
                        暂无视频数据
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                <TabsContent value="edits" className="space-y-4">
                  {/* Shot 编辑记录 */}
                  {taskData.shot_edits && taskData.shot_edits.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                          <Video className="w-4 h-4" />
                          Shot 编辑记录
                        </CardTitle>
                        <CardDescription>共 {taskData.shot_edits.length} 次编辑</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-3">
                          {taskData.shot_edits.map((edit: any) => (
                            <div key={edit.uuid} className="border rounded-lg p-3 space-y-2 bg-muted/20">
                              <div className="flex justify-between items-center flex-wrap gap-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-semibold">Shot {edit.shot_number}</span>
                                  <Badge variant="outline" className="text-xs">{edit.model}</Badge>
                                </div>
                                <div className="flex gap-1.5 items-center text-xs text-muted-foreground">
                                  {new Date(edit.created_at).toLocaleString()}
                                  <Badge variant={edit.user_action === 're-gen' ? 'secondary' : 'default'} className="text-[10px]">{edit.user_action}</Badge>
                                  <Badge variant={edit.success ? 'default' : 'destructive'} className="text-[10px]">{edit.success ? '成功' : '失败'}</Badge>
                                </div>
                              </div>
                              {(edit.user_feedback || edit.edit_instruction || edit.error_msg) && (
                                <div className="space-y-0.5 text-xs">
                                  {edit.user_feedback && <div><span className="font-medium text-muted-foreground">用户反馈:</span> {edit.user_feedback}</div>}
                                  {edit.edit_instruction && <div><span className="font-medium text-muted-foreground">修改指令:</span> {edit.edit_instruction}</div>}
                                  <div className="text-red-500"><span className="font-medium">错误:</span> {(edit.error_msg != null && edit.error_msg !== '') ? edit.error_msg : '—'}</div>
                                </div>
                              )}
                              <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1.5">
                                  <Badge variant="outline" className="text-[10px] bg-muted">旧 v{edit.old_version_number}</Badge>
                                  {edit.old_video_url && <LazyVideo src={edit.old_video_url} className="w-full aspect-video rounded border" />}
                                  {edit.old_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-muted/50 rounded whitespace-pre-wrap break-words border">{edit.old_prompt}</div>}
                                </div>
                                <div className="space-y-1.5">
                                  <Badge className="text-[10px]">新 v{edit.new_version_number}</Badge>
                                  {edit.new_video_url && <LazyVideo src={edit.new_video_url} className="w-full aspect-video rounded border" />}
                                  {edit.new_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-primary/5 rounded whitespace-pre-wrap break-words border border-primary/20">{edit.new_prompt}</div>}
                                </div>
                              </div>
                              {edit.uuid && <div className="pt-1.5 border-t"><UuidRow table="video_shot_edit_records" uuid={edit.uuid} /></div>}
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* Storyboard 编辑记录 */}
                  {taskData.storyboard_edits && taskData.storyboard_edits.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                          <Image className="w-4 h-4" />
                          Storyboard 编辑记录
                        </CardTitle>
                        <CardDescription>共 {taskData.storyboard_edits.length} 次编辑</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-3">
                          {taskData.storyboard_edits.map((edit: any) => (
                            <div key={edit.uuid} className="border rounded-lg p-3 space-y-2 bg-muted/20">
                              <div className="flex justify-between items-center flex-wrap gap-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-semibold">Shot {edit.shot_number}</span>
                                  <Badge variant="outline" className="text-xs">{edit.model}</Badge>
                                </div>
                                <div className="flex gap-1.5 items-center text-xs text-muted-foreground">
                                  {new Date(edit.created_at).toLocaleString()}
                                  <Badge variant={edit.user_action === 're-gen' ? 'secondary' : 'default'} className="text-[10px]">{edit.user_action}</Badge>
                                  <Badge variant={edit.success ? 'default' : 'destructive'} className="text-[10px]">{edit.success ? '成功' : '失败'}</Badge>
                                </div>
                              </div>
                              {(edit.user_feedback || edit.edit_instruction || edit.error_msg) && (
                                <div className="space-y-0.5 text-xs">
                                  {edit.user_feedback && <div><span className="font-medium text-muted-foreground">用户反馈:</span> {edit.user_feedback}</div>}
                                  {edit.edit_instruction && <div><span className="font-medium text-muted-foreground">修改指令:</span> {edit.edit_instruction}</div>}
                                  <div className="text-red-500"><span className="font-medium">错误:</span> {(edit.error_msg != null && edit.error_msg !== '') ? edit.error_msg : '—'}</div>
                                </div>
                              )}
                              <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1.5">
                                  <Badge variant="outline" className="text-[10px] bg-muted">旧 v{edit.old_version_number}</Badge>
                                  {edit.old_image_url && <img src={edit.old_image_url} className="w-full aspect-video object-cover rounded border" alt="旧关键帧" />}
                                  {edit.old_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-muted/50 rounded whitespace-pre-wrap break-words border">{edit.old_prompt}</div>}
                                </div>
                                <div className="space-y-1.5">
                                  <Badge className="text-[10px]">新 v{edit.new_version_number}</Badge>
                                  {edit.new_image_url && <img src={edit.new_image_url} className="w-full aspect-video object-cover rounded border" alt="新关键帧" />}
                                  {edit.new_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-primary/5 rounded whitespace-pre-wrap break-words border border-primary/20">{edit.new_prompt}</div>}
                                </div>
                              </div>
                              {edit.uuid && <div className="pt-1.5 border-t"><UuidRow table="video_storyboard_edit_records" uuid={edit.uuid} /></div>}
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* Character 编辑记录 */}
                  {taskData.character_edits && taskData.character_edits.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2">
                          <Users className="w-4 h-4" />
                          Character 编辑记录
                        </CardTitle>
                        <CardDescription>共 {taskData.character_edits.length} 次编辑</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="space-y-3">
                          {taskData.character_edits.map((edit: any) => (
                            <div key={edit.uuid} className="border rounded-lg p-3 space-y-2 bg-muted/20">
                              <div className="flex justify-between items-center flex-wrap gap-2">
                                <div className="flex items-center gap-2">
                                  <span className="text-sm font-semibold">{edit.character_name}</span>
                                  <Badge variant="outline" className="text-xs">{edit.model}</Badge>
                                </div>
                                <div className="flex gap-1.5 items-center text-xs text-muted-foreground">
                                  {new Date(edit.created_at).toLocaleString()}
                                  <Badge variant={edit.user_action === 're-gen' ? 'secondary' : 'default'} className="text-[10px]">{edit.user_action}</Badge>
                                  <Badge variant={edit.success ? 'default' : 'destructive'} className="text-[10px]">{edit.success ? '成功' : '失败'}</Badge>
                                </div>
                              </div>
                              {(edit.user_feedback || edit.edit_instruction || edit.error_msg) && (
                                <div className="space-y-0.5 text-xs">
                                  {edit.user_feedback && <div><span className="font-medium text-muted-foreground">用户反馈:</span> {edit.user_feedback}</div>}
                                  {edit.edit_instruction && <div><span className="font-medium text-muted-foreground">修改指令:</span> {edit.edit_instruction}</div>}
                                  <div className="text-red-500"><span className="font-medium">错误:</span> {(edit.error_msg != null && edit.error_msg !== '') ? edit.error_msg : '—'}</div>
                                </div>
                              )}
                              <div className="grid grid-cols-2 gap-4">
                                <div className="space-y-1.5">
                                  <Badge variant="outline" className="text-[10px] bg-muted">旧 v{edit.old_version_number}</Badge>
                                  {edit.old_image_url && <img src={edit.old_image_url} className="w-full aspect-square object-cover rounded border max-h-40" alt="旧角色" />}
                                  {edit.old_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-muted/50 rounded whitespace-pre-wrap break-words border">{edit.old_prompt}</div>}
                                </div>
                                <div className="space-y-1.5">
                                  <Badge className="text-[10px]">新 v{edit.new_version_number}</Badge>
                                  {edit.new_image_url && <img src={edit.new_image_url} className="w-full aspect-square object-cover rounded border max-h-40" alt="新角色" />}
                                  {edit.new_prompt && <div className="text-xs max-h-32 overflow-y-auto p-2 bg-primary/5 rounded whitespace-pre-wrap break-words border border-primary/20">{edit.new_prompt}</div>}
                                </div>
                              </div>
                              {edit.uuid && <div className="pt-1.5 border-t"><UuidRow table="video_character_edit_records" uuid={edit.uuid} /></div>}
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* 无编辑记录提示 */}
                  {(!taskData.shot_edits || taskData.shot_edits.length === 0) &&
                   (!taskData.storyboard_edits || taskData.storyboard_edits.length === 0) &&
                   (!taskData.character_edits || taskData.character_edits.length === 0) && (
                    <Card>
                      <CardContent className="py-12 text-center text-muted-foreground">
                        该任务暂无编辑记录
                      </CardContent>
                    </Card>
                  )}
                </TabsContent>

                <TabsContent value="other" className="space-y-4">
                  {/* 旁白 - 紧凑卡片，与镜头风格一致 */}
                  {taskData.narrations_data && taskData.narrations_data.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle>旁白</CardTitle>
                        <CardDescription>共 {taskData.narrations_data.length} 条</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                          {taskData.narrations_data.map((narration: any) => (
                            <div key={narration.uuid} className="rounded-lg border p-2 bg-muted/20 space-y-1">
                              <div className="text-xs font-medium text-muted-foreground">Shot {narration.shot_number}{narration.duration != null ? ` · ${narration.duration}s` : ''}</div>
                              <div className="text-sm line-clamp-2 break-words">{narration.narration_text}</div>
                              {narration.audio_url && <audio src={narration.audio_url} controls className="w-full h-8 mt-1" />}
                              <div className="text-xs font-mono text-muted-foreground break-all">UUID: {narration.uuid}{narration.version_uuid ? ` · 版本: ${narration.version_uuid}` : ''}</div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* 音效 - 紧凑 */}
                  {taskData.audio_effects_data && taskData.audio_effects_data.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle>音效</CardTitle>
                        <CardDescription>共 {taskData.audio_effects_data.length} 条</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                          {taskData.audio_effects_data.map((effect: any) => (
                            <div key={effect.uuid} className="rounded-lg border p-2 bg-muted/20 space-y-1">
                              <div className="text-xs font-medium text-muted-foreground">Shot {effect.shot_number}{effect.duration != null ? ` · ${effect.duration}s` : ''}</div>
                              <div className="text-sm line-clamp-2 break-words">{effect.audio_prompt}</div>
                              {effect.audio_url && <audio src={effect.audio_url} controls className="w-full h-8 mt-1" />}
                              <div className="text-xs font-mono text-muted-foreground break-all">UUID: {effect.uuid}{effect.version_uuid ? ` · 版本: ${effect.version_uuid}` : ''}</div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* 音乐 - 紧凑 */}
                  {taskData.music_data && taskData.music_data.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle className="flex items-center gap-2"><Music className="w-4 h-4" />音乐</CardTitle>
                        <CardDescription>共 {taskData.music_data.length} 条</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                          {taskData.music_data.map((music: any) => (
                            <div key={music.uuid} className="rounded-lg border p-2 bg-muted/20 space-y-1">
                              <div className="text-xs text-muted-foreground">{music.duration != null ? `${music.duration}s` : ''}</div>
                              <div className="text-sm line-clamp-2 break-words">{music.music_prompt}</div>
                              {music.music_url && <audio src={music.music_url} controls className="w-full h-8 mt-1" />}
                              <div className="text-xs font-mono text-muted-foreground break-all">UUID: {music.uuid}{music.version_uuid ? ` · 版本: ${music.version_uuid}` : ''}</div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* 视频片段 - 紧凑，小预览 */}
                  {taskData.video_segments_data && taskData.video_segments_data.length > 0 && (
                    <Card>
                      <CardHeader>
                        <CardTitle>视频片段</CardTitle>
                        <CardDescription>共 {taskData.video_segments_data.length} 个片段</CardDescription>
                      </CardHeader>
                      <CardContent>
                        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
                          {taskData.video_segments_data.map((segment: any) => (
                            <div key={segment.uuid} className="rounded-lg border p-2 bg-muted/20 space-y-1">
                              <div className="text-xs font-medium text-muted-foreground">片段 {segment.segment_number}</div>
                              {segment.video_url && (
                                <LazyVideo src={segment.video_url} className="w-full aspect-video rounded object-cover max-h-24" />
                              )}
                              <div className="text-xs font-mono text-muted-foreground break-all">UUID: {segment.uuid}{segment.version_uuid ? ` · 版本: ${segment.version_uuid}` : ''}</div>
                            </div>
                          ))}
                        </div>
                      </CardContent>
                    </Card>
                  )}

                  {/* 视频合成信息 */}
                  {taskData.video_assembly_data && (
                    <Card>
                      <CardHeader>
                        <CardTitle>视频合成</CardTitle>
                      </CardHeader>
                      <CardContent className="space-y-2">
                        <div className="flex justify-between">
                          <span className="text-sm text-muted-foreground">状态</span>
                          <Badge variant={taskData.video_assembly_data.success ? "default" : "destructive"}>
                            {taskData.video_assembly_data.success ? "成功" : "失败"}
                          </Badge>
                        </div>
                        <div className="flex justify-between">
                          <span className="text-sm text-muted-foreground">总时长</span>
                          <span className="text-sm">{taskData.video_assembly_data.total_duration}s</span>
                        </div>
                      </CardContent>
                    </Card>
                  )}

                </TabsContent>
              </Tabs>
            </div>
          </ScrollArea>
        ) : (
          <div className="flex items-center justify-center py-12 text-muted-foreground">
            无数据
          </div>
        )}
      </DialogContent>
      <Dialog open={addToConsistencyOpen} onOpenChange={(open) => { setAddToConsistencyOpen(open); if (!open) setAddToConsistencyVersionUuid(null); }}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{addToConsistencyType === 'image' ? '加入图片一致性测试集' : '加入视频一致性测试集'}</DialogTitle>
          </DialogHeader>
          <div className="space-y-4 py-2">
            <div className="space-y-2">
              <Label>选择测试集</Label>
              <Select value={addToConsistencyDatasetId} onValueChange={setAddToConsistencyDatasetId}>
                <SelectTrigger>
                  <SelectValue placeholder="请选择测试集" />
                </SelectTrigger>
                <SelectContent>
                  {addToConsistencyDatasets.map((d) => (
                    <SelectItem key={d.dataset_id} value={d.dataset_id}>{d.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label>Prompt 来源</Label>
              <Select value={addToConsistencyPromptSource} onValueChange={setAddToConsistencyPromptSource}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="attempt_1">第 1 次的 prompt</SelectItem>
                  <SelectItem value="attempt_2">第 2 次的 prompt</SelectItem>
                  <SelectItem value="attempt_3">第 3 次的 prompt</SelectItem>
                  <SelectItem value="version">version 上的 prompt</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAddToConsistencyOpen(false)}>取消</Button>
            <Button
              disabled={!addToConsistencyDatasetId || !addToConsistencyVersionUuid || addToConsistencySubmitting}
              onClick={async () => {
                if (!addToConsistencyDatasetId || !addToConsistencyVersionUuid) return;
                setAddToConsistencySubmitting(true);
                try {
                  const payload = { shot_number: addToConsistencyShotNumber, prompt_source: addToConsistencyPromptSource };
                  if (taskId) (payload as any).run_id = taskId;
                  if (taskData?.thread_id) (payload as any).thread_id = taskData.thread_id;
                  if (addToConsistencyType === 'image') {
                    await api.admin.addConsistencyImageItem(addToConsistencyDatasetId, { keyframe_version_uuid: addToConsistencyVersionUuid, ...payload });
                  } else {
                    await api.admin.addConsistencyVideoItem(addToConsistencyDatasetId, { video_generation_version_uuid: addToConsistencyVersionUuid, ...payload });
                  }
                  toast({ title: '已加入测试集' });
                  setAddToConsistencyOpen(false);
                } catch (e: any) {
                  toast({ title: e?.message || '加入失败', variant: 'destructive' });
                } finally {
                  setAddToConsistencySubmitting(false);
                }
              }}
            >
              {addToConsistencySubmitting ? '提交中...' : '确定'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Dialog>
  );
}

