import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useLanguage } from '@/i18n/LanguageContext';
import { Loader2, Music } from 'lucide-react';

interface Message {
  id: number;
  conversation_id: number;
  role: string;
  content: string;
  created_at: string;
  sequence: number;
  metadata: any;
  event_type?: string;
  event_data?: any;
}

interface MusicPlayerSectionProps {
  messages: Message[];
}

interface MusicTrack {
  title: string;
  url: string;
  description?: string;
}

const MusicPlayerSection: React.FC<MusicPlayerSectionProps> = ({ messages }) => {
  const { t } = useLanguage();

  // Extract music tracks from messages
  const extractMusicTracks = (): MusicTrack[] => {
    const tracks: MusicTrack[] = [];

    for (const msg of messages) {
      if (msg.event_type !== 'music_agent_generated') continue;
      // 防御性解析：API 返回的 event_data 可能是 JSON 字符串，父组件可能未解析
      let eventData = msg.event_data;
      if (typeof eventData === 'string') {
        try {
          eventData = JSON.parse(eventData);
        } catch {
          continue;
        }
      }
      const musicContent = eventData?.music_content;
      if (!musicContent || typeof musicContent !== 'string') continue;

      const regex = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g;
      let match;
      while ((match = regex.exec(musicContent)) !== null) {
        const [, title, url] = match;
        if (title && url) {
          tracks.push({
            title: title.trim(),
            url: url.trim()
          });
        }
      }
    }

    return tracks;
  };

  const musicTracks = extractMusicTracks();

  const latestTodoMessage = [...messages].reverse().find((msg) => msg.event_type === 'generation_todo');
  const latestUserInput = [...messages].reverse().find((msg) => msg.event_type === 'user_input');
  const latestUserInputHasConfirmed = latestUserInput?.event_data?.has_confirmed === true;
  const musicProgressPercent = Number(latestTodoMessage?.event_data?.music_progress_percent);
  const isGenerating = messages.some(
    msg => latestUserInputHasConfirmed &&
           msg.event_type === 'generation_todo' && 
           msg.event_data?.status !== 'cancelled' && 
           msg.event_data?.status !== 'failed'
  ) && !messages.some(msg => msg.event_type === 'music_agent_generated');

  if (musicTracks.length === 0) {
    if (!isGenerating) return null;
    return (
      <div className="p-6 space-y-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Music className="w-5 h-5" />
              {t('generatedMusic')}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            <div className="flex items-center gap-3">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              <span className="text-sm text-muted-foreground">
                {(Number.isFinite(musicProgressPercent) && musicProgressPercent > 0)
                  ? `${t('musicGenerating')} ${Math.round(musicProgressPercent)}%`
                  : t('musicGenerating')}
              </span>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Music className="w-5 h-5" />
            {t('generatedMusic')}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          {musicTracks.map((track, index) => (
            <div key={index} className="space-y-2">
              <div className="flex items-center justify-between">
                <h3 className="text-sm font-medium text-foreground">
                  {track.title}
                </h3>
              </div>
              <audio
                controls
                className="w-full"
                preload="metadata"
                src={track.url}
              >
                {t('audioNotSupported')}
              </audio>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
};

export default MusicPlayerSection;
