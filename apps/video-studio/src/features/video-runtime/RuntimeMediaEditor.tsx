import { useState } from "react";
import { Download, Film, Loader2, Mic, Music, Subtitles } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type {
  RuntimeExport,
  RuntimeWorkspace,
  VideoEdit,
} from "@/features/video-runtime/client";

interface EditableShot {
  id: string;
  order: number;
  duration_seconds: number;
  narration: string;
  transition: string;
}

const editableShots = (workspace: RuntimeWorkspace): EditableShot[] => {
  const shots = workspace.videoSpec?.shots;
  if (!Array.isArray(shots)) return [];
  return shots.flatMap((value) => {
    if (!value || typeof value !== "object") return [];
    const shot = value as Record<string, unknown>;
    const id = String(shot.id || "");
    if (!id) return [];
    return [{
      id,
      order: Number(shot.order || 0),
      duration_seconds: Number(shot.duration_seconds || 0),
      narration: String(shot.narration || ""),
      transition: String(shot.transition || "cut"),
    }];
  });
};

export function RuntimeMediaEditor({
  workspace,
  busy,
  onPreview,
  onExport,
}: {
  workspace: RuntimeWorkspace;
  busy: boolean;
  onPreview: (edit: VideoEdit, description: string) => Promise<void>;
  onExport: () => Promise<RuntimeExport>;
}) {
  const audio = workspace.videoSpec?.audio;
  const audioSpec = audio && typeof audio === "object"
    ? audio as Record<string, unknown>
    : {};
  const [musicPrompt, setMusicPrompt] = useState(() => String(audioSpec.bgm_prompt || ""));
  const [shots, setShots] = useState(() => editableShots(workspace));
  const [exporting, setExporting] = useState(false);
  const [exportResult, setExportResult] = useState<RuntimeExport | null>(null);
  const narration = workspace.artifacts.find(
    (item) => item.isSelected && item.logicalId === "audio:narration" && Boolean(item.uri),
  );
  const clipShotIds = new Set(
    workspace.artifacts.flatMap((item) => {
      if (!item.isSelected || !item.uri) return [];
      const match = item.logicalId?.match(/^shot:(.+):clip$/);
      return match ? [match[1]] : [];
    }),
  );

  const updateShot = (shotId: string, patch: Partial<EditableShot>) => {
    setShots((current) => current.map((shot) => (
      shot.id === shotId ? { ...shot, ...patch } : shot
    )));
  };

  const previewTimeline = () => {
    const orders = shots.map((shot) => shot.order).sort((left, right) => left - right);
    const expected = shots.map((_, index) => index + 1);
    if (orders.some((value, index) => value !== expected[index])) {
      toast.error(`镜头顺序必须是不重复的 1～${shots.length}`);
      return Promise.resolve();
    }
    if (shots.some((shot) => !Number.isFinite(shot.duration_seconds) || shot.duration_seconds <= 0)) {
      toast.error("每个镜头的时长必须大于 0 秒");
      return Promise.resolve();
    }
    return onPreview({
      type: "patch_timeline",
      patch: {
        shots: shots.map((shot) => ({
          id: shot.id,
          order: shot.order,
          duration_seconds: shot.duration_seconds,
          narration: shot.narration,
          transition: shot.transition,
        })),
      },
    }, "更新时间线顺序、时长和旁白");
  };

  const exportVideo = async () => {
    setExporting(true);
    try {
      setExportResult(await onExport());
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="grid gap-3 xl:grid-cols-2" data-testid="runtime-media-editor">
      <Card className="border-border/60 bg-card/60">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Music className="h-4 w-4" />音乐、口型与导出
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <label className="text-xs font-medium" htmlFor="runtime-music-prompt">BGM 描述</label>
            <textarea
              id="runtime-music-prompt"
              value={musicPrompt}
              onChange={(event) => setMusicPrompt(event.target.value)}
              placeholder="例如：温暖、克制的电影感钢琴"
              className="min-h-20 w-full rounded-md border border-border bg-background p-2 text-xs outline-none focus:ring-2 focus:ring-accent-purple/40"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={busy || !musicPrompt.trim()}
              onClick={() => void onPreview(
                { type: "replace_music", prompt: musicPrompt.trim() },
                "替换背景音乐",
              )}
            >
              <Music className="mr-1 h-3.5 w-3.5" />预览更换音乐
            </Button>
          </div>

          <div className="space-y-2 border-t border-border/50 pt-3">
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1 text-xs font-medium">
                <Mic className="h-3.5 w-3.5" />镜头口型同步
              </span>
              <Badge variant={narration ? "secondary" : "outline"}>
                {narration ? "旁白可用" : "缺少旁白音轨"}
              </Badge>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {shots.map((shot) => (
                <Button
                  key={shot.id}
                  size="sm"
                  variant="ghost"
                  disabled={busy || !narration || !clipShotIds.has(shot.id)}
                  onClick={() => void onPreview(
                    { type: "generate_lipsync", id: shot.id },
                    `为镜头 ${shot.order} 生成口型同步`,
                  )}
                >
                  镜头 {shot.order}
                </Button>
              ))}
            </div>
          </div>

          <div className="space-y-2 border-t border-border/50 pt-3">
            <Button size="sm" disabled={exporting} onClick={() => void exportVideo()}>
              {exporting
                ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
                : <Download className="mr-1 h-3.5 w-3.5" />}
              导出当前 MP4
            </Button>
            {exportResult?.uri ? (
              <a
                href={exportResult.uri}
                download
                target="_blank"
                rel="noreferrer"
                className="block truncate text-xs text-accent-purple underline"
              >
                下载 {exportResult.uri}
              </a>
            ) : exportResult ? (
              <p className="text-xs text-muted-foreground">导出状态：{exportResult.status}</p>
            ) : null}
          </div>
        </CardContent>
      </Card>

      <Card className="border-border/60 bg-card/60">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center justify-between gap-2 text-sm">
            <span className="flex items-center gap-2"><Film className="h-4 w-4" />时间线与旁白</span>
            <Badge variant="outline" className="gap-1">
              <Subtitles className="h-3 w-3" />
              {audioSpec.subtitles ? "字幕开启" : "字幕关闭"}
            </Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {shots.map((shot) => (
            <div key={shot.id} className="grid gap-2 rounded-md border border-border/50 p-2 sm:grid-cols-[4.5rem_5.5rem_1fr]">
              <label className="space-y-1 text-[11px] text-muted-foreground">
                顺序
                <input
                  type="number"
                  min={1}
                  max={shots.length}
                  value={shot.order}
                  onChange={(event) => updateShot(shot.id, { order: Number(event.target.value) })}
                  className="w-full rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
                />
              </label>
              <label className="space-y-1 text-[11px] text-muted-foreground">
                时长（秒）
                <input
                  type="number"
                  min={0.1}
                  max={30}
                  step={0.1}
                  value={shot.duration_seconds}
                  onChange={(event) => updateShot(shot.id, { duration_seconds: Number(event.target.value) })}
                  className="w-full rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
                />
              </label>
              <label className="space-y-1 text-[11px] text-muted-foreground">
                镜头 {shot.id} 旁白
                <input
                  value={shot.narration}
                  onChange={(event) => updateShot(shot.id, { narration: event.target.value })}
                  className="w-full rounded border border-border bg-background px-2 py-1 text-xs text-foreground"
                  placeholder="留空表示没有旁白"
                />
              </label>
            </div>
          ))}
          <Button
            size="sm"
            variant="outline"
            disabled={busy || shots.length === 0}
            onClick={() => void previewTimeline()}
          >
            预览时间线修改
          </Button>
          <p className="text-[11px] text-muted-foreground">
            修改时长会重新生成对应镜头；仅调整顺序会复用镜头并重新拼接。所有付费步骤都先进入影响与费用确认。
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
