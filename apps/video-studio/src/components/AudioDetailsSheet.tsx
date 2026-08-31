import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import type { AudioHistory } from "@/types/api";
import { Music, Clock, Download, Play, Hash, Globe, FileAudio } from "lucide-react";

interface AudioDetailsSheetProps {
  isOpen: boolean;
  onClose: () => void;
  audio: AudioHistory | null;
}

const AudioDetailsSheet = ({ isOpen, onClose, audio }: AudioDetailsSheetProps) => {
  const { language, t } = useLanguage();

  if (!audio) return null;

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
            <Music className="h-5 w-5" />
            {t('audioDetails')}
          </SheetTitle>
        </SheetHeader>

        <ScrollArea className="flex-1 -mx-6 px-6">
          <div className="space-y-6 py-4">

            {/* Audio Player */}
            {audio.audio_url && (
              <div className="space-y-2">
                <audio controls className="w-full">
                  <source src={audio.audio_url} type="audio/mpeg" />
                  {t('audioNotSupported')}
                </audio>
              </div>
            )}

            {/* Status Badge */}
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className="bg-purple-500/10 text-purple-500 border-purple-500/20">
                <Music className="h-3 w-3 mr-1" />
                {t('audioType')}
              </Badge>
              {audio.language && (
                <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20">
                  <Globe className="h-3 w-3 mr-1" />
                  {audio.language}
                </Badge>
              )}
              {audio.duration > 0 && (
                <Badge variant="outline" className="text-muted-foreground border-border">
                  <Clock className="h-3 w-3 mr-1" />
                  {Math.round(audio.duration)}s
                </Badge>
              )}
            </div>

            <Separator />

            {/* Filename */}
            {audio.filename && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <FileAudio className="h-4 w-4" />
                  {t('audioFilename')}
                </h3>
                <p className="text-sm text-foreground font-semibold">
                  {audio.filename}
                </p>
              </div>
            )}


            {/* Text Content */}
            {audio.text && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <FileAudio className="h-4 w-4" />
                  {t('audioText')}
                </h3>
                <p className="text-sm text-foreground leading-relaxed bg-secondary p-3 rounded">
                  {audio.text}
                </p>
              </div>
            )}

            <Separator />

            {/* Technical Details */}
            <div className="space-y-3">
              <h3 className="text-sm font-semibold text-muted-foreground">
                {t('technicalDetails')}
              </h3>

              {/* Duration */}
              {audio.duration > 0 && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('duration')}:
                  </span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {Math.round(audio.duration)}s
                  </Badge>
                </div>
              )}

              {/* Language */}
              {audio.language && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground flex items-center gap-1">
                    <Globe className="h-3 w-3" />
                    {t('language')}:
                  </span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {audio.language}
                  </Badge>
                </div>
              )}

              {/* UUID */}
              <div className="flex justify-between items-center">
                <span className="text-sm text-muted-foreground flex items-center gap-1">
                  <Hash className="h-3 w-3" />
                  UUID:
                </span>
                <code className="text-xs text-muted-foreground font-mono bg-secondary px-2 py-1 rounded truncate max-w-[200px]">
                  {audio.uuid}
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
                  <span className="text-foreground">{formatDate(audio.created_at)}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('updatedAt')}:
                  </span>
                  <span className="text-foreground">{formatDate(audio.updated_at)}</span>
                </div>
              </div>
            </div>

            {/* Additional Data */}
            {audio.additional_data && Object.keys(audio.additional_data).length > 0 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <h3 className="text-sm font-semibold text-muted-foreground">
                    {t('additionalData')}
                  </h3>
                  <pre className="text-xs text-foreground bg-secondary p-3 rounded overflow-x-auto">
                    {JSON.stringify(audio.additional_data, null, 2)}
                  </pre>
                </div>
              </>
            )}

            {/* Action Buttons */}
            {audio.audio_url && (
              <>
                <Separator />
                <div className="flex gap-2">
                  <Button
                    variant="outline"
                    className="flex-1"
                    onClick={() => window.open(audio.audio_url, '_blank')}
                  >
                    <Play className="w-4 h-4 mr-2" />
                    {t('openInNewTab')}
                  </Button>
                  <Button
                    variant="default"
                    className="flex-1"
                    onClick={() => {
                      const link = document.createElement('a');
                      link.href = audio.audio_url;
                      link.download = audio.filename || `audio-${audio.uuid}.mp3`;
                      link.target = '_blank';
                      link.rel = 'noopener noreferrer';
                      document.body.appendChild(link);
                      link.click();
                      setTimeout(() => {
                        document.body.removeChild(link);
                      }, 100);
                    }}
                  >
                    <Download className="w-4 h-4 mr-2" />
                    {t('download')}
                  </Button>
                </div>
              </>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
};

export default AudioDetailsSheet;
