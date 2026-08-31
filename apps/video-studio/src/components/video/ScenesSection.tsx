import { useState, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { MapPin, Check, X } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { videoAnalysisApi } from "@/services/api";

interface ScenesSectionProps {
  scenesData: any;
  onScenePatch?: (sceneId: string, patch: Record<string, string>) => Promise<boolean | void>;
  contentCategory?: string;
  onFullView?: () => void;
  /** 场景编辑保存后回调，用于父组件合并更新后的场景到 state */
  onSceneUpdated?: (updatedScene: any) => void;
  /** 保存成功后刷新整块场景数据（如按 thread 重新拉取） */
  onAfterSceneUpdate?: () => void | Promise<void>;
}

export const ScenesSection = ({ scenesData, contentCategory, onFullView, onScenePatch, onSceneUpdated, onAfterSceneUpdate }: ScenesSectionProps) => {
  const { t } = useLanguage();
  const [editing, setEditing] = useState<{ uuid: string; field: "title" | "description"; value: string } | null>(null);
  const [saving, setSaving] = useState(false);

  // API返回格式: { scenes: [], total: 123 }
  if (!scenesData || !scenesData.scenes || scenesData.scenes.length === 0) return null;

  const scenes = scenesData.scenes;
  const showSceneNarrations = contentCategory === "Product Launch";
  const baseCardWidth = 220; // 与 Storyboard 图片模块宽度一致
  const editDescriptionCardWidth = 360; // 描述编辑时加宽，减少换行不挤

  const handleSave = useCallback(async () => {
    if (!editing) return;
    const payload = editing.field === "title" ? { title: editing.value.trim() } : { description: editing.value.trim() };
    setSaving(true);
    try {
      if (onScenePatch) {
        await onScenePatch(editing.uuid, payload);
      } else {
        const res = await videoAnalysisApi.patchScene({ uuid: editing.uuid, ...payload });
        if (res?.data && onSceneUpdated) onSceneUpdated(res.data);
      }
      setEditing(null);
      if (!onScenePatch) await onAfterSceneUpdate?.();
    } finally {
      setSaving(false);
    }
  }, [editing, onScenePatch, onSceneUpdated, onAfterSceneUpdate]);

  const handleCancel = useCallback(() => setEditing(null), []);

  return (
    <Card className="glass p-6">
      <div className="flex items-center mb-4">
        <h3 className="text-lg font-semibold flex items-center">
        <MapPin className="w-5 h-5 mr-2 text-accent-white" />
        {t('sceneSection')}
      </h3>
      </div>
      {/* 横向滚动容器 - 与 Storyboards 一致，左侧留白避免第一个被裁切 */}
      <div className="overflow-x-auto scrollbar-subtle pl-10">
        <div
          className="flex gap-4 pb-4 pr-4"
          style={{
            width: `${scenes.reduce((sum, s) => sum + (editing?.uuid === (s.uuid || s.runtime_shot_id) && editing?.field === 'description' ? editDescriptionCardWidth + 16 : baseCardWidth + 16), 0)}px`,
            minWidth: '100%'
          }}
        >
          {scenes.map((scene: any, idx: number) => {
            const sceneId = scene.uuid || scene.runtime_shot_id;
            const isEditingDescription = editing?.uuid === sceneId && editing?.field === "description";
            const cardWidth = isEditingDescription ? editDescriptionCardWidth : baseCardWidth;
            return (
            <div
              key={sceneId ?? idx}
              className="p-3 bg-white/5 rounded border border-white/10 flex-shrink-0"
              style={{ width: `${cardWidth}px` }}
            >
              <div className="font-medium text-sm mb-1">
                {t('scene')} {scene.scene_number || idx + 1}:{" "}
                {editing?.uuid === sceneId && editing?.field === "title" ? (
                  <div className="relative">
                    <Input
                      value={editing.value}
                      onChange={(e) => setEditing((p) => (p ? { ...p, value: e.target.value } : null))}
                      className="pr-20 text-sm h-8"
                    />
                    <div className="absolute right-2 top-1/2 -translate-y-1/2 flex gap-1 z-10">
                      <Button type="button" size="sm" variant="ghost" className="h-8 w-8 p-0 hover:bg-green-100 hover:text-green-600" onClick={handleSave} disabled={saving}>
                        <Check className="w-4 h-4" />
                      </Button>
                      <Button type="button" size="sm" variant="ghost" className="h-8 w-8 p-0 hover:bg-red-100 hover:text-red-600" onClick={handleCancel} disabled={saving}>
                        <X className="w-4 h-4" />
                      </Button>
                    </div>
                  </div>
                ) : (
                  <span
                    className="cursor-pointer hover:bg-white/10 rounded px-0.5 -mx-0.5"
                    onDoubleClick={() => sceneId && setEditing({ uuid: sceneId, field: "title", value: String(scene.title || "") })}
                  >
                    {String(scene.title || t('untitled'))}
                  </span>
                )}
              </div>
              {editing?.uuid === sceneId && editing?.field === "description" ? (
                <div className="relative mt-1">
                  <Textarea
                    value={editing.value}
                    onChange={(e) => setEditing((p) => (p ? { ...p, value: e.target.value } : null))}
                    className="min-h-[200px] pr-20 text-xs leading-tight resize-y"
                  />
                  <div className="absolute bottom-2 right-2 flex gap-1 z-10">
                    <Button type="button" size="sm" variant="ghost" className="h-8 w-8 p-0 hover:bg-green-100 hover:text-green-600" onClick={handleSave} disabled={saving}>
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button type="button" size="sm" variant="ghost" className="h-8 w-8 p-0 hover:bg-red-100 hover:text-red-600" onClick={handleCancel} disabled={saving}>
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              ) : (
                <p
                  className="text-xs text-muted-foreground mb-2 break-words cursor-pointer hover:bg-white/10 rounded px-0.5 -mx-0.5 min-h-[1.5rem]"
                  onDoubleClick={() => sceneId && setEditing({ uuid: sceneId, field: "description", value: String(scene.description || "") })}
                >
                  {scene.description ? String(scene.description) : "\u00A0"}
                </p>
              )}
              {scene.duration != null && (
                <div className="text-xs text-muted-foreground">
                  <span className="font-medium">{t('duration')}:</span>
                  <span> {Number(scene.duration).toFixed(2)}s</span>
                </div>
              )}
              {showSceneNarrations && (Array.isArray(scene.narrations) && scene.narrations.length > 0) && (
                <div className="mt-2 space-y-2 border-t border-white/10 pt-2">
                  <div className="text-[10px] font-medium text-muted-foreground uppercase tracking-wide">
                    {t('narrationSection')}
                  </div>
                  {scene.narrations.map((item: any, nIdx: number) => {
                    const hasNarration = item.has_narration !== false;
                    const displayText = hasNarration ? (item.enhanced_prompt || item.narration_text || "") : "";
                    if (!hasNarration || (!displayText && !item.audio_url)) {
                      return (
                        <div key={`${scene.uuid}-n-${item.shot_number ?? nIdx}`} className="rounded bg-white/5 p-2 space-y-1">
                          <div className="text-[10px] text-muted-foreground">
                            {t('shot')} {item.shot_number ?? nIdx + 1}
                          </div>
                          <p className="text-xs text-muted-foreground/80 italic">{t('noNarration')}</p>
                        </div>
                      );
                    }
                    return (
                      <div key={`${scene.uuid}-n-${item.shot_number ?? nIdx}`} className="rounded bg-white/5 p-2 space-y-1">
                        <div className="text-[10px] text-muted-foreground">
                          {t('shot')} {item.shot_number ?? nIdx + 1}
                        </div>
                        {displayText ? (
                          <p className="text-xs text-foreground/90 break-words line-clamp-4">{String(displayText)}</p>
                        ) : null}
                        {item.audio_url ? (
                          <audio src={item.audio_url} controls className="w-full h-7" />
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          );
          })}
        </div>
      </div>
    </Card>
  );
};

