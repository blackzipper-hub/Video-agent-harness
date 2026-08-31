import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { useLanguage } from "@/i18n/LanguageContext";
import type { CharacterHistory } from "@/types/api";
import { User, Clock, Palette, Users, Briefcase } from "lucide-react";

interface CharacterDetailsSheetProps {
  isOpen: boolean;
  onClose: () => void;
  character: CharacterHistory | null;
}

const CharacterDetailsSheet = ({ isOpen, onClose, character }: CharacterDetailsSheetProps) => {
  const { language, t } = useLanguage();

  if (!character) return null;

  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return new Intl.DateTimeFormat(language === 'zh' ? 'zh-CN' : 'en-US', {
      year: 'numeric',
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    }).format(date);
  };

  return (
    <Sheet open={isOpen} onOpenChange={onClose}>
      <SheetContent className="w-full sm:max-w-lg overflow-hidden flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <User className="h-5 w-5" />
            {t('characterDetails')}
          </SheetTitle>
        </SheetHeader>

        <ScrollArea className="flex-1 -mx-6 px-6">
          <div className="space-y-6 py-4">
            {/* Character Image */}
            {character.image_url ? (
              <div className="relative w-full aspect-square rounded-lg overflow-hidden bg-secondary">
                <img
                  src={character.image_url}
                  alt={character.name}
                  className="w-full h-full object-cover"
                />
              </div>
            ) : (
              <div className="w-full aspect-square rounded-lg bg-gradient-to-br from-green-500/20 to-emerald-500/20 dark:from-green-900/40 dark:to-emerald-900/40 flex items-center justify-center">
                <User className="h-24 w-24 text-green-400" />
              </div>
            )}

            {/* Character Name */}
            <div>
              <h2 className="text-2xl font-bold text-foreground mb-2">
                {character.name}
              </h2>
              <Badge variant="outline" className="bg-green-500/10 text-green-500 border-green-500/20">
                <User className="h-3 w-3 mr-1" />
                {t('characterType')}
              </Badge>
            </div>

            <Separator />

            {/* Description */}
            {character.description && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <User className="h-4 w-4" />
                  {t('characterDescription')}
                </h3>
                <p className="text-sm text-foreground leading-relaxed">
                  {character.description}
                </p>
              </div>
            )}

            {/* Personality */}
            {character.personality && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <Users className="h-4 w-4" />
                  {t('characterPersonality')}
                </h3>
                <p className="text-sm text-foreground leading-relaxed">
                  {character.personality}
                </p>
              </div>
            )}

            {/* Appearance */}
            {character.appearance && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <Palette className="h-4 w-4" />
                  {t('characterAppearance')}
                </h3>
                <p className="text-sm text-foreground leading-relaxed">
                  {character.appearance}
                </p>
              </div>
            )}

            {/* Role */}
            {character.role && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <Briefcase className="h-4 w-4" />
                  {t('characterRole')}
                </h3>
                <p className="text-sm text-foreground leading-relaxed">
                  {character.role}
                </p>
              </div>
            )}

            <Separator />

            {/* Technical Details */}
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-muted-foreground">
                {t('technicalDetails')}
              </h3>

              {/* Style */}
              {character.style && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">{t('characterStyle')}:</span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {character.style}
                  </Badge>
                </div>
              )}

              {/* Body Type */}
              {character.body_type && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">{t('characterBodyType')}:</span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {character.body_type}
                  </Badge>
                </div>
              )}

              {/* UUID */}
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground">UUID:</span>
                <code className="text-xs text-muted-foreground font-mono bg-secondary px-2 py-1 rounded">
                  {character.uuid}
                </code>
              </div>


              <Separator />

              {/* Timestamps */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('createdAt')}:
                  </span>
                  <span className="text-foreground">{formatDate(character.created_at)}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('updatedAt')}:
                  </span>
                  <span className="text-foreground">{formatDate(character.updated_at)}</span>
                </div>
              </div>
            </div>

            {/* Additional Data */}
            {character.additional_data && Object.keys(character.additional_data).length > 0 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-muted-foreground">
                    {t('additionalData')}
                  </h3>
                  <pre className="text-xs text-foreground bg-secondary p-3 rounded overflow-x-auto">
                    {JSON.stringify(character.additional_data, null, 2)}
                  </pre>
                </div>
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
};

export default CharacterDetailsSheet;
