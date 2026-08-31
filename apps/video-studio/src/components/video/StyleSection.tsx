import { Card } from "@/components/ui/card";
import { Zap } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

interface StyleSectionProps {
  /** 优先使用 analysis 的 style_preferences（短标签）；无则用 outline 的 style_guide */
  analysisData?: { style_preferences?: string[] } | null;
  storyOutlineData?: any;
}

function normalizeStyles(analysisData: StyleSectionProps['analysisData'], storyOutlineData: any): string[] {
  const raw = analysisData?.style_preferences;
  if (raw != null) {
    if (Array.isArray(raw) && raw.length > 0) return raw.map(String);
    if (typeof raw === 'string' && raw.trim()) return raw.split(',').map((s) => s.trim()).filter(Boolean);
  }
  if (storyOutlineData?.style_guide) return [String(storyOutlineData.style_guide)];
  return [];
}

export const StyleSection = ({ analysisData, storyOutlineData }: StyleSectionProps) => {
  const { t } = useLanguage();
  const styles = normalizeStyles(analysisData, storyOutlineData);
  if (!styles.length) return null;

  return (
    <Card className="paper-card p-6">
      <h3 className="text-lg font-semibold mb-4 flex items-center font-inter">
        <Zap className="w-5 h-5 mr-2 text-primary" />
        {t('styleSection')}
      </h3>
      <div className="text-sm text-muted-foreground leading-relaxed flex flex-wrap gap-2">
        {styles.map((s, i) => (
          <span key={i} className="inline-flex items-center rounded-md bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary">
            {String(s)}
          </span>
        ))}
      </div>
    </Card>
  );
};

