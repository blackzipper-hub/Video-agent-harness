import { Card } from "@/components/ui/card";
import { Volume2 } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

interface AudioEffectsSectionProps {
  audioEffectsData: any;
}

export const AudioEffectsSection = ({ audioEffectsData }: AudioEffectsSectionProps) => {
  const { t } = useLanguage();
  // API返回格式: { audio_effects: [], total: 123 }
  if (!audioEffectsData || !audioEffectsData.audio_effects || audioEffectsData.audio_effects.length === 0) return null;

  const audioEffects = audioEffectsData.audio_effects;

  return (
    <Card className="glass p-6">
      <h3 className="text-lg font-semibold mb-4 flex items-center">
        <Volume2 className="w-5 h-5 mr-2 text-accent-orange" />
        {t('audioEffectsSection')}
      </h3>
      <div className="space-y-3">
        {audioEffects.map((effect: any, idx: number) => {
          const currentVersion = effect.versions && effect.versions[effect.current_version_index || 0];
          const audioUrl = currentVersion?.audio_url;
          
          return (
            <div key={idx} className="p-3 bg-white/5 rounded border border-white/10">
              <div className="flex items-start justify-between mb-2">
                <div className="text-sm font-medium">
                  {t('shot')} {effect.shot_number || idx + 1}
                </div>
                {effect.versions && effect.versions.length > 1 && (
                  <div className="text-xs text-muted-foreground">
                    {t('version')} {(effect.current_version_index || 0) + 1}/{effect.versions.length}
                  </div>
                )}
              </div>
              {currentVersion?.prompt && (
                <div className="text-sm text-muted-foreground mb-2">
                  {String(currentVersion.prompt)}
                </div>
              )}
              {audioUrl && (
                <audio controls className="w-full mt-2">
                  <source src={audioUrl} type="audio/mpeg" />
                </audio>
              )}
            </div>
          );
        })}
      </div>
    </Card>
  );
};
