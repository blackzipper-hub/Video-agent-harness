import { Card } from "@/components/ui/card";
import { Mic } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

interface NarrationSectionProps {
  narrationData: any;
}

export const NarrationSection = ({ narrationData }: NarrationSectionProps) => {
  const { t } = useLanguage();
  // API返回格式: { narrations: [], total: 123 }
  if (!narrationData || !narrationData.narrations || narrationData.narrations.length === 0) return null;

  const narrations = narrationData.narrations;

  return (
    <Card className="glass p-6">
      <h3 className="text-lg font-semibold mb-4 flex items-center">
        <Mic className="w-5 h-5 mr-2 text-accent-green" />
        {t('narrationSection')}
      </h3>
      <div className="space-y-3">
        {narrations.map((narration: any, idx: number) => {
          const currentVersion = narration.versions && narration.versions[narration.current_version_index || 0];
          const audioUrl = currentVersion?.audio_url;
          
          return (
            <div key={idx} className="p-3 bg-white/5 rounded border border-white/10">
              <div className="flex items-start justify-between mb-2">
                <div className="text-sm font-medium">
                  {t('shot')} {narration.shot_number || idx + 1}
                </div>
                {narration.versions && narration.versions.length > 1 && (
                  <div className="text-xs text-muted-foreground">
                    {t('version')} {(narration.current_version_index || 0) + 1}/{narration.versions.length}
                  </div>
                )}
              </div>
              {currentVersion?.narration_text && (
                <div className="text-sm text-muted-foreground mb-2">
                  {String(currentVersion.narration_text)}
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
