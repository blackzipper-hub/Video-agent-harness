import { useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Button } from "@/components/ui/button";
import { useLanguage } from "@/i18n/LanguageContext";
import type { VideoHistory } from "@/types/api";
import { getDisplayPromptForUserMessage } from "@/utils/promptMapping";
import { Film, Clock, CheckCircle, XCircle, Download, Play, Hash, Tag, FileText, Loader2 } from "lucide-react";
import { api } from "@/services/api";
import TaskDetailModal from "@/pages/admin/TaskDetailModal";
import { useToast } from "@/hooks/use-toast";

interface VideoDetailsSheetProps {
  isOpen: boolean;
  onClose: () => void;
  video: VideoHistory | null;
}

const VideoDetailsSheet = ({ isOpen, onClose, video }: VideoDetailsSheetProps) => {
  const { language, t } = useLanguage();
  const { toast } = useToast();
  const [taskDetailOpen, setTaskDetailOpen] = useState(false);
  const [taskDetailData, setTaskDetailData] = useState<any>(null);
  const [loadingTaskDetail, setLoadingTaskDetail] = useState(false);

  if (!video) return null;

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
  <>
    <Sheet open={isOpen} onOpenChange={onClose}>
      <SheetContent className="w-full sm:max-w-lg overflow-hidden flex flex-col">
        <SheetHeader>
          <SheetTitle className="flex items-center gap-2">
            <Film className="h-5 w-5" />
            {t('videoDetails')}
          </SheetTitle>
        </SheetHeader>

        <ScrollArea className="flex-1 -mx-6 px-6">
          <div className="space-y-6 py-4">
            {/* Video Preview */}
            {video.final_video_url ? (
              <div className="relative rounded-lg overflow-hidden bg-black/5 flex items-center justify-center" style={{ height: '300px' }}>
                <video
                  src={video.final_video_url}
                  className="max-w-full max-h-full object-contain"
                  controls
                  controlsList="nodownload"
                />
              </div>
            ) : (
              <div className="w-full aspect-video rounded-lg bg-gradient-to-br from-blue-500/20 to-purple-500/20 dark:from-blue-900/40 dark:to-purple-900/40 flex items-center justify-center">
                <Film className="h-24 w-24 text-blue-400" />
              </div>
            )}

            {/* Status Badge */}
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className="bg-blue-500/10 text-blue-500 border-blue-500/20">
                <Film className="h-3 w-3 mr-1" />
                {t('videoType')}
              </Badge>
              <Badge
                variant="outline"
                className={`${
                  video.success
                    ? 'bg-green-500/10 text-green-500 border-green-500/20'
                    : 'bg-red-500/10 text-red-500 border-red-500/20'
                }`}
              >
                {video.success ? (
                  <CheckCircle className="h-3 w-3 mr-1" />
                ) : (
                  <XCircle className="h-3 w-3 mr-1" />
                )}
                {video.success ? t('videoSuccess') : t('videoFailed')}
              </Badge>
            </div>

            <Separator />

            {/* Title */}
            {video.title && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <Tag className="h-4 w-4" />
                  {t('videoTitle')}
                </h3>
                <p className="text-sm text-foreground font-semibold">
                  {video.title}
                </p>
              </div>
            )}

            {/* Prompt - fixed tag, not editable */}
            {video.prompt && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-muted-foreground flex items-center gap-2">
                  <Film className="h-4 w-4" />
                  {t('videoPrompt')}
                </h3>
                <Badge
                  variant="outline"
                  className="text-xs font-normal px-3 py-1.5 cursor-default select-none"
                >
                  {getDisplayPromptForUserMessage(video.prompt)}
                </Badge>
              </div>
            )}

            {/* Error Message */}
            {video.error_msg && (
              <div className="space-y-2">
                <h3 className="text-sm font-semibold text-red-500 flex items-center gap-2">
                  <XCircle className="h-4 w-4" />
                  {t('errorMessage')}
                </h3>
                <p className="text-sm text-red-400 leading-relaxed bg-red-500/5 p-3 rounded">
                  {video.error_msg}
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
              {video.total_duration > 0 && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('duration')}:
                  </span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {Math.round(video.total_duration)}s
                  </Badge>
                </div>
              )}

              {/* Assembly Mode */}
              {video.assembly_mode && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">{t('assemblyMode')}:</span>
                  <Badge variant="outline" className="font-mono text-xs">
                    {video.assembly_mode}
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
                  {video.uuid}
                </code>
              </div>

              {/* Story Outline ID */}
              {video.story_outline_id && (
                <div className="flex justify-between items-center">
                  <span className="text-sm text-muted-foreground">{t('storyOutlineId')}:</span>
                  <code className="text-xs text-muted-foreground font-mono bg-secondary px-2 py-1 rounded truncate max-w-[200px]">
                    {video.story_outline_id}
                  </code>
                </div>
              )}

              <Separator />

              {/* Timestamps */}
              <div className="space-y-2">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('createdAt')}:
                  </span>
                  <span className="text-foreground">{formatDate(video.created_at)}</span>
                </div>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground flex items-center gap-1">
                    <Clock className="h-3 w-3" />
                    {t('updatedAt')}:
                  </span>
                  <span className="text-foreground">{formatDate(video.updated_at)}</span>
                </div>
              </div>
            </div>


            {/* Action Buttons */}
            <Separator />
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                className="flex-1 min-w-[120px]"
                disabled={loadingTaskDetail}
                onClick={async () => {
                  if (!video.run_id) return;
                  setLoadingTaskDetail(true);
                  try {
                    const res = await api.conversation.getConversationTaskDetail(video.run_id);
                    if (res.code === 0 && res.data) {
                      setTaskDetailData(res.data);
                      setTaskDetailOpen(true);
                    } else {
                      toast({ title: language === 'zh' ? '加载任务详情失败' : 'Failed to load task detail', variant: 'destructive' });
                    }
                  } catch {
                    toast({ title: language === 'zh' ? '加载任务详情失败' : 'Failed to load task detail', variant: 'destructive' });
                  } finally {
                    setLoadingTaskDetail(false);
                  }
                }}
              >
                {loadingTaskDetail ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <FileText className="w-4 h-4 mr-2" />}
                {language === 'zh' ? '任务详情' : 'Task Detail'}
              </Button>
              {video.final_video_url && (
                <>
                  <Button
                    variant="outline"
                    className="flex-1 min-w-[120px]"
                    onClick={() => window.open(video.final_video_url!, '_blank')}
                  >
                    <Play className="w-4 h-4 mr-2" />
                    {t('openInNewTab')}
                  </Button>
                  <Button
                    variant="default"
                    className="flex-1 min-w-[120px]"
                    onClick={() => {
                      const link = document.createElement('a');
                      link.href = video.final_video_url!;
                      link.download = video.title || `video-${video.uuid}.mp4`;
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
                </>
              )}
            </div>
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
    {taskDetailOpen && (
      <TaskDetailModal
        isOpen={taskDetailOpen}
        onClose={() => { setTaskDetailOpen(false); setTaskDetailData(null); }}
        taskId={video.run_id}
        initialTaskData={taskDetailData}
      />
    )}
  </>
  );
};

export default VideoDetailsSheet;
