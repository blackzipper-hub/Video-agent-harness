import React, { forwardRef } from 'react';
import { useLanguage } from '@/i18n/LanguageContext';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Film, Loader2 } from 'lucide-react';
import { ScrollArea } from "@/components/ui/scroll-area";
import { extractGeneratedVideoItems } from "@/utils/videoGenResults";

interface Message {
  role: string;
  content: string;
  timestamp?: string;
  event_type?: string;
  event_data?: any;
}

interface VideoAgentResultsPanelProps {
  messages: Message[];
  /** 当前对话是否有任务在 running/queued，有则优先显示「生成中」而非「已完成」 */
  isGenerating?: boolean;
}

interface VideoItem {
  url: string;
  coverUrl?: string;
  timestamp?: string;
}

export const VideoAgentResultsPanel = forwardRef<HTMLDivElement, VideoAgentResultsPanelProps>(
  ({ messages, isGenerating: isGeneratingProp }, ref) => {
    const { t } = useLanguage();

    // 从 video_agent_generated 消息提取结构化视频，兼容 videos[] / video_url / final_video_url / video_generations[]。
    const extractVideos = (): VideoItem[] => {
      const videos: VideoItem[] = [];
      const videoMessages = messages.filter(
        (msg) => msg.event_type === 'video_agent_generated' && msg.event_data
      );

      videoMessages.forEach((msg) => {
        const data = msg.event_data;
        const timestamp = msg.timestamp;
        extractGeneratedVideoItems({
          ...data,
          content: msg.content,
        }).forEach((v) => {
          videos.push({
            url: v.video_url,
            coverUrl: v.cover_image_url || undefined,
            timestamp,
          });
        });
      });

      return videos;
    };

    const videoItems = extractVideos();

    const latestUserInput = [...messages].reverse().find((msg) => msg.event_type === 'user_input');
    const latestUserInputHasConfirmed = latestUserInput?.event_data?.has_confirmed === true;
    const derivedGenerating =
      messages.some(
        (msg) =>
          latestUserInputHasConfirmed &&
          msg.event_type === 'generation_todo' &&
          msg.event_data?.status !== 'cancelled' &&
          msg.event_data?.status !== 'failed'
      ) && !messages.some((msg) => msg.event_type === 'video_agent_generated');
    // 一旦有结果就不再显示「生成中」，避免「生成中」与「已完成」同时出现
    const isGenerating = (isGeneratingProp ?? derivedGenerating) && videoItems.length === 0;

    return (
      <ScrollArea ref={ref} className="flex-1 min-h-0 h-full">
        <div className="p-6 space-y-6">
          {isGenerating && (
            <Card className="border-white/20 dark:border-gray-700/50 bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm">
              <CardContent className="flex items-center justify-center gap-3 py-8">
                <Loader2 className="w-5 h-5 animate-spin text-accent-purple" />
                <span className="text-sm text-muted-foreground">
                  {t('generatingVideos')}
                </span>
              </CardContent>
            </Card>
          )}

          {videoItems.length > 0 && (
            <Card className="border-white/20 dark:border-gray-700/50 bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm">
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-lg">
                  <Film className="w-5 h-5 text-accent-purple" />
                  {t('generatedVideos')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-1 gap-6">
                  {videoItems.map((video, index) => (
                    <div
                      key={index}
                      className="group relative animate-fade-in"
                      style={{ animationDelay: `${index * 100}ms` }}
                    >
                      <div className="relative overflow-hidden rounded-lg border border-white/20 dark:border-gray-700/50 bg-black/5 dark:bg-black/20 shadow-lg">
                        <video
                          src={video.url}
                          poster={video.coverUrl}
                          controls
                          playsInline
                          preload="metadata"
                          className="block h-auto max-h-[calc(100dvh-14rem)] max-w-full object-contain bg-black"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {!isGenerating && videoItems.length === 0 && (
            <Card className="border-white/20 dark:border-gray-700/50 bg-white/50 dark:bg-gray-800/50 backdrop-blur-sm">
              <CardContent className="flex flex-col items-center justify-center py-12">
                <Film className="w-16 h-16 text-muted-foreground/30 mb-4" />
                <p className="text-sm text-muted-foreground text-center">
                  {t('noVideosYet')}
                </p>
              </CardContent>
            </Card>
          )}
        </div>

        <style>{`
          @keyframes fade-in {
            from { opacity: 0; transform: translateY(10px); }
            to { opacity: 1; transform: translateY(0); }
          }
          .animate-fade-in { animation: fade-in 0.5s ease-out forwards; }
        `}</style>
      </ScrollArea>
    );
  }
);

VideoAgentResultsPanel.displayName = 'VideoAgentResultsPanel';
