import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Music, Edit, Check, X } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";
import { useState } from "react";

interface MusicSectionProps {
  musicData: any;
  onMusicPromptUpdate?: (musicUuid: string, versionUuid: string, newPrompt: string) => Promise<void>;
}

/** 与 VideoCheckCanvasPage / 后端 music_generation version 字段对齐 */
export function getMusicVersionAudioUrl(version: any, music: any): string | null {
  const v = version || {};
  const m = music || {};
  const raw =
    v.audio_url ||
    v.music_url ||
    v.cdn_url ||
    v.url ||
    m.audio_url ||
    m.music_url ||
    m.cdn_url ||
    "";
  if (typeof raw !== "string") return null;
  const u = raw.trim();
  return u.length > 0 ? u : null;
}

/** 串联预演 / 时间线：仅用 API 顶层整曲 music_url，不用 music_generations 分片 */
export function getPreviewBgmUrlFromMusicData(musicData: any): string | null {
  if (!musicData) return null;
  const top = musicData.music_url || musicData.audio_url || musicData.cdn_url || musicData.url;
  if (typeof top === "string" && top.trim()) return top.trim();
  return null;
}

export const MusicSection = ({ musicData, onMusicPromptUpdate }: MusicSectionProps) => {
  const { t } = useLanguage();
  const [editingPrompt, setEditingPrompt] = useState<string | null>(null);
  const [editingText, setEditingText] = useState<string>('');
  const [saving, setSaving] = useState<boolean>(false);
  
  // API返回格式: { music_generations: [], total: 123 }
  if (!musicData || !musicData.music_generations || musicData.music_generations.length === 0) return null;

  const musicGenerations = musicData.music_generations;

  const handleEditPrompt = (musicUuid: string, versionUuid: string, currentPrompt: string) => {
    const editKey = `${musicUuid}-${versionUuid}`;
    setEditingPrompt(editKey);
    setEditingText(currentPrompt || '');
  };

  const handleSavePrompt = async (musicUuid: string, versionUuid: string) => {
    if (!onMusicPromptUpdate) return;
    
    setSaving(true);
    try {
      await onMusicPromptUpdate(musicUuid, versionUuid, editingText);
      setEditingPrompt(null);
      setEditingText('');
    } catch (error) {
      console.error('Failed to update music prompt:', error);
      // TODO: Show error toast
    } finally {
      setSaving(false);
    }
  };

  const handleCancelEdit = () => {
    setEditingPrompt(null);
    setEditingText('');
  };

  return (
    <Card className="glass p-6 h-[400px] flex flex-col">
      <h3 className="text-lg font-semibold mb-4 flex items-center flex-shrink-0">
        <Music className="w-5 h-5 mr-2 text-accent-purple" />
        {t('backgroundMusicSection')}
      </h3>
      {typeof musicData.music_url === "string" && musicData.music_url.trim() ? (
        <div className="mb-4 flex-shrink-0 pb-4 border-b border-white/10">
          <audio
            controls
            className="w-full h-9"
            preload="metadata"
            src={musicData.music_url.trim()}
          >
            {t("audioNotSupported")}
          </audio>
        </div>
      ) : null}
      <div className="space-y-0 flex-1 overflow-y-auto scrollbar-subtle">
        {musicGenerations.map((music: any, idx: number) => {
          const currentVersion = music.versions && music.versions[music.current_version_index || 0];
          if (!currentVersion) return null;

          const audioUrl = getMusicVersionAudioUrl(currentVersion, music);

          return (
            <div key={idx} className="p-4 bg-white/5 rounded border border-white/10">
              {/* 版本信息 - 仅在有多个版本时显示 */}
              {music.versions && music.versions.length > 1 && (
                <div className="text-xs text-muted-foreground mb-3 text-right">
                  {t('version')} {(music.current_version_index || 0) + 1}/{music.versions.length}
                </div>
              )}

              {audioUrl ? (
                <div className="mb-4">
                  <audio controls className="w-full h-9" preload="metadata" src={audioUrl}>
                    {t("audioNotSupported")}
                  </audio>
                </div>
              ) : null}
              
              {/* 歌词 / 提示词显示和编辑 */}
              {editingPrompt === `${music.uuid}-${currentVersion.uuid}` ? (
                <div className="relative">
                  <Textarea
                    value={editingText}
                    onChange={(e) => setEditingText(e.target.value)}
                    placeholder={t('enterMusicPrompt') || 'Enter lyrics...'}
                    className="min-h-[120px] pr-20 text-sm leading-tight"
                  />
                  <div className="absolute bottom-2 right-2 flex gap-1">
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 hover:bg-green-100 hover:text-green-600"
                      onClick={() => handleSavePrompt(music.uuid, currentVersion.uuid)}
                      disabled={saving}
                    >
                      <Check className="w-4 h-4" />
                    </Button>
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-8 w-8 p-0 hover:bg-red-100 hover:text-red-600"
                      onClick={handleCancelEdit}
                      disabled={saving}
                    >
                      <X className="w-4 h-4" />
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-2 group">
                  <div
                    className="flex-1 text-sm text-muted-foreground rounded-md cursor-text"
                    onDoubleClick={() => {
                      if (!onMusicPromptUpdate) return;
                      handleEditPrompt(music.uuid, currentVersion.uuid, currentVersion?.music_prompt || '');
                    }}
                    title={onMusicPromptUpdate ? (t('doubleClickToEdit') || 'Double-click to edit') : undefined}
                  >
                    {currentVersion?.music_prompt ? (
                      <div className="whitespace-pre-line leading-tight">
                        {String(currentVersion.music_prompt)}
                      </div>
                    ) : (
                      <span className="italic text-gray-400">
                        {t('noMusicPrompt') || 'No lyrics - click to add'}
                      </span>
                    )}
                  </div>
                  {onMusicPromptUpdate && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0"
                      onClick={() => handleEditPrompt(music.uuid, currentVersion.uuid, currentVersion?.music_prompt || '')}
                    >
                      <Edit className="w-4 h-4" />
                    </Button>
                  )}
                </div>
              )}
              
              {/* 乐器标记 */}
              {music.is_instrumental && (
                <div className="text-xs text-purple-400 mt-2">
                  {t('instrumental')}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
};
