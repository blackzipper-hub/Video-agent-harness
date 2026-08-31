import { useState, useEffect, useCallback } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { MessageSquare, Check, X } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { videoAnalysisApi } from "@/services/api";

interface StorySectionProps {
  storyOutlineData: any;
  onChapterPatch?: (chapterId: string, patch: Record<string, string>) => Promise<boolean | void>;
  /** 章节编辑保存后回调，用于父组件合并更新后的章节到 state */
  onChapterUpdated?: (updatedChapter: any) => void;
}

export const StorySection = ({ storyOutlineData, onChapterPatch, onChapterUpdated }: StorySectionProps) => {
  const { t } = useLanguage();
  const [chaptersWithUuid, setChaptersWithUuid] = useState<any[]>([]);
  const [editing, setEditing] = useState<{ uuid: string; field: "title" | "description"; value: string } | null>(null);
  const [saving, setSaving] = useState(false);

  const outlineUuid = storyOutlineData?.uuid;

  useEffect(() => {
    if (!outlineUuid) {
      setChaptersWithUuid([]);
      return;
    }
    let cancelled = false;
    videoAnalysisApi.getChaptersByOutlineId(outlineUuid).then((res) => {
      if (!cancelled && res?.data?.chapters) {
        setChaptersWithUuid(res.data.chapters);
      }
    }).catch(() => {});
    return () => { cancelled = true; };
  }, [outlineUuid]);

  const handleSave = useCallback(async () => {
    if (!editing) return;
    const payload = editing.field === "title" ? { title: editing.value.trim() } : { description: editing.value.trim() };
    setSaving(true);
    try {
      if (onChapterPatch) {
        await onChapterPatch(editing.uuid, payload);
      } else {
        const res = await videoAnalysisApi.patchChapter({ uuid: editing.uuid, ...payload });
        if (res?.data) {
          if (onChapterUpdated) onChapterUpdated(res.data);
          setChaptersWithUuid((prev) => prev.map((ch) => (ch.uuid === editing.uuid ? { ...ch, ...res.data } : ch)));
        }
      }
      setEditing(null);
      if (!onChapterPatch && outlineUuid) {
        const fresh = await videoAnalysisApi.getChaptersByOutlineId(outlineUuid);
        if (fresh?.data?.chapters) setChaptersWithUuid(fresh.data.chapters);
      }
    } finally {
      setSaving(false);
    }
  }, [editing, onChapterPatch, onChapterUpdated, outlineUuid]);

  const handleCancel = useCallback(() => setEditing(null), []);

  if (!storyOutlineData) return null;

  const structureChapters = storyOutlineData.structure || [];
  const chapters = chaptersWithUuid.length > 0 ? chaptersWithUuid : structureChapters;
  const canEdit = Boolean(onChapterPatch) || chaptersWithUuid.length > 0;

  return (
    <Card className="glass p-6 h-full flex flex-col">
      <h3 className="text-lg font-semibold mb-4 flex items-center font-inter flex-shrink-0">
        <MessageSquare className="w-5 h-5 mr-2 text-accent-cyan" />
        {t('storySection')}
      </h3>
      <div className="flex-1 flex flex-col">
      {storyOutlineData.title && (
        <h4 className="font-medium mb-3 text-base">
          {String(storyOutlineData.title)}
        </h4>
      )}
      {storyOutlineData.theme && (
        <div className="mb-3 text-sm">
          <strong>{t('theme')}:</strong>{" "}
          <span className="text-muted-foreground">
            {String(storyOutlineData.theme)}
          </span>
        </div>
      )}
      {storyOutlineData.description && (
        <div className="mb-3 text-sm">
          <strong>{t('description')}:</strong>{" "}
          <span className="text-muted-foreground">
            {String(storyOutlineData.description)}
          </span>
        </div>
      )}
      {chapters.length > 0 && (
        <div className="space-y-3 mt-4">
          <div className="text-sm font-medium">{t('chapters')}:</div>
          {chapters.map((chapter: any, idx: number) => (
            <div key={chapter.uuid ?? chapter.runtime_shot_id ?? idx} className="p-4 bg-white/5 rounded border border-white/10">
              <div className="font-medium text-sm mb-1">
                {t('chapter')} {(chapter.order ?? idx) + 1}:{" "}
                {canEdit && (chapter.uuid || chapter.runtime_shot_id) && editing?.uuid === (chapter.uuid || chapter.runtime_shot_id) && editing?.field === "title" ? (
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
                    className={canEdit && (chapter.uuid || chapter.runtime_shot_id) ? "cursor-pointer hover:bg-white/10 rounded px-0.5 -mx-0.5" : ""}
                    onDoubleClick={canEdit && (chapter.uuid || chapter.runtime_shot_id) ? () => setEditing({ uuid: chapter.uuid || chapter.runtime_shot_id, field: "title", value: String(chapter.title || "") }) : undefined}
                  >
                    {String(chapter.title || "")}
                  </span>
                )}
              </div>
              {canEdit && (chapter.uuid || chapter.runtime_shot_id) && editing?.uuid === (chapter.uuid || chapter.runtime_shot_id) && editing?.field === "description" ? (
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
                <div
                  className={`text-xs text-muted-foreground mt-1 min-h-[1.25rem] ${canEdit && (chapter.uuid || chapter.runtime_shot_id) ? "cursor-pointer hover:bg-white/10 rounded px-0.5 -mx-0.5" : ""}`}
                  onDoubleClick={canEdit && (chapter.uuid || chapter.runtime_shot_id) ? () => setEditing({ uuid: chapter.uuid || chapter.runtime_shot_id, field: "description", value: String(chapter.description || "") }) : undefined}
                >
                  {chapter.description ? String(chapter.description) : "\u00A0"}
                </div>
              )}
              {chapter.duration != null && (
                <div className="text-xs text-muted-foreground mt-1">
                  {t('duration')}: {Number(chapter.duration).toFixed(2)}s
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      </div>
    </Card>
  );
};

