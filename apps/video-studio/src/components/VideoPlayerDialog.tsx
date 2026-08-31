import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useLanguage } from "@/i18n/LanguageContext";

interface VideoPlayerDialogProps {
  isOpen: boolean;
  onClose: () => void;
  videoUrl: string;
  title?: string;
}

const VideoPlayerDialog = ({ isOpen, onClose, videoUrl, title }: VideoPlayerDialogProps) => {
  const { t } = useLanguage();
  return (
    <Dialog open={isOpen} onOpenChange={onClose}>
      <DialogContent className="max-w-4xl w-full p-0 overflow-hidden bg-black/95 border-border">
        <DialogHeader className="p-4 pb-2 bg-background/80 backdrop-blur-sm">
          <DialogTitle className="text-foreground">
            {title || "Video Player"}
          </DialogTitle>
        </DialogHeader>

        <div className="relative w-full aspect-video bg-black">
          <video
            src={videoUrl}
            controls
            autoPlay
            className="w-full h-full object-contain"
            onError={(e) => {
              console.error("Video playback error:", e);
            }}
          >
            {t('videoNotSupported')}
          </video>
        </div>
      </DialogContent>
    </Dialog>
  );
};

export default VideoPlayerDialog;
