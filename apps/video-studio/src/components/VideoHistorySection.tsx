import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Separator } from "@/components/ui/separator";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useLanguage } from "@/i18n/LanguageContext";
import { videoHistoryApi, audioHistoryApi, mediaHistoryApi, characterHistoryApi, api, videoAnalysisApi } from "@/services/api";
import type { VideoHistory, AudioHistory, CharacterHistory } from "@/types/api";
import { getDisplayPromptForUserMessage } from "@/utils/promptMapping";
import VideoPlayerDialog from "@/components/VideoPlayerDialog";
import CharacterDetailsSheet from "@/components/CharacterDetailsSheet";
import VideoDetailsSheet from "@/components/VideoDetailsSheet";
import AudioDetailsSheet from "@/components/AudioDetailsSheet";
import { MediaFolderCard } from "@/components/MediaFolderCard";
import {
  RefreshCw,
  Play,
  CheckCircle,
  XCircle,
  Film,
  Clock,
  Filter,
  Music,
  User,
  Home,
  FolderOpen,
  History,
  X,
  ZoomIn,
  ZoomOut,
  Trash2,
} from "lucide-react";

interface VideoHistorySectionProps {
  isActive: boolean;
  onFolderOpenChange?: (isOpen: boolean) => void;
}

type MediaType = 'all' | 'video' | 'audio' | 'character';

const VideoHistorySection = ({ isActive, onFolderOpenChange }: VideoHistorySectionProps) => {
  const { language, t } = useLanguage();
  const navigate = useNavigate();

  const [mediaType, setMediaType] = useState<MediaType>('all');

  const [videoHistory, setVideoHistory] = useState<VideoHistory[]>([]);
  const [audioHistory, setAudioHistory] = useState<AudioHistory[]>([]);
  const [characterHistory, setCharacterHistory] = useState<CharacterHistory[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [currentPage, setCurrentPage] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [totalVideos, setTotalVideos] = useState(0);
  const [totalAudios, setTotalAudios] = useState(0);
  const [totalCharacters, setTotalCharacters] = useState(0);
  const [showSuccessOnly, setShowSuccessOnly] = useState(false);
  const [refreshTrigger, setRefreshTrigger] = useState(0);
  const [selectedVideoUrl, setSelectedVideoUrl] = useState<string | null>(null);
  const [isPlayerOpen, setIsPlayerOpen] = useState(false);
  const [selectedCharacter, setSelectedCharacter] = useState<CharacterHistory | null>(null);
  const [isCharacterSheetOpen, setIsCharacterSheetOpen] = useState(false);
  const [selectedVideo, setSelectedVideo] = useState<VideoHistory | null>(null);
  const [isVideoSheetOpen, setIsVideoSheetOpen] = useState(false);
  const [selectedAudio, setSelectedAudio] = useState<AudioHistory | null>(null);
  const [isAudioSheetOpen, setIsAudioSheetOpen] = useState(false);
  const [selectedFolder, setSelectedFolder] = useState<{ name: string; items: Array<VideoHistory | AudioHistory | CharacterHistory> } | null>(null);
  const [zoomLevel, setZoomLevel] = useState(100);
  const [videoPreviewImageByThreadId, setVideoPreviewImageByThreadId] = useState<Record<string, string>>({});
  const itemsPerPage = 20;

  useEffect(() => {
    const fetchData = async () => {
      if (!isActive) return;

      try {
        setIsLoading(true);

        if (mediaType === 'all') {
          const response = await mediaHistoryApi.getMedia({
            video_limit: itemsPerPage,
            video_offset: currentPage * itemsPerPage,
            audio_limit: itemsPerPage,
            audio_offset: currentPage * itemsPerPage,
            character_limit: itemsPerPage,
            character_offset: currentPage * itemsPerPage,
            success_only: showSuccessOnly,
          });

          if (response.code === 0 && response.data) {
            setVideoHistory(response.data.videos);
            setAudioHistory(response.data.audios);
            setCharacterHistory(response.data.characters || []);
            setTotalVideos(response.data.total_videos);
            setTotalAudios(response.data.total_audios);
            setTotalCharacters(response.data.total_characters || 0);
            setHasMore(
              response.data.videos.length === itemsPerPage ||
              response.data.audios.length === itemsPerPage ||
              (response.data.characters && response.data.characters.length === itemsPerPage)
            );
          } else {
            setVideoHistory([]);
            setAudioHistory([]);
            setCharacterHistory([]);
            setTotalVideos(0);
            setTotalAudios(0);
            setTotalCharacters(0);
          }
        } else if (mediaType === 'video') {
          // Use video-specific API
          const response = await videoHistoryApi.getVideos({
            limit: itemsPerPage,
            offset: currentPage * itemsPerPage,
            success_only: showSuccessOnly,
          });

          if (response.code === 0 && response.data) {
            setVideoHistory(response.data.videos);
            setTotalVideos(response.data.total);
            setAudioHistory([]);
            setCharacterHistory([]);
            setTotalAudios(0);
            setTotalCharacters(0);
            setHasMore(response.data.videos.length === itemsPerPage);
          } else {
            setVideoHistory([]);
            setTotalVideos(0);
          }
        } else if (mediaType === 'audio') {
          // Use audio-specific API
          const response = await audioHistoryApi.getAudios({
            limit: itemsPerPage,
            offset: currentPage * itemsPerPage,
          });

          if (response.code === 0 && response.data) {
            setAudioHistory(response.data.audios);
            setTotalAudios(response.data.total);
            setVideoHistory([]);
            setCharacterHistory([]);
            setTotalVideos(0);
            setTotalCharacters(0);
            setHasMore(response.data.audios.length === itemsPerPage);
          } else {
            setAudioHistory([]);
            setTotalAudios(0);
          }
        } else if (mediaType === 'character') {
          // Use character-specific API
          const response = await characterHistoryApi.getCharacters({
            limit: itemsPerPage,
            offset: currentPage * itemsPerPage,
          });

          if (response.code === 0 && response.data) {
            setCharacterHistory(response.data.characters);
            setTotalCharacters(response.data.total);
            setVideoHistory([]);
            setAudioHistory([]);
            setTotalVideos(0);
            setTotalAudios(0);
            setHasMore(response.data.characters.length === itemsPerPage);
          } else {
            setCharacterHistory([]);
            setTotalCharacters(0);
          }
        }
      } catch (error) {
        setVideoHistory([]);
        setAudioHistory([]);
        setCharacterHistory([]);
        setTotalVideos(0);
        setTotalAudios(0);
        setTotalCharacters(0);
      } finally {
        setIsLoading(false);
      }
    };

    if (isActive) {
      fetchData();
    }
  }, [currentPage, isActive, showSuccessOnly, mediaType, refreshTrigger]);

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

  const handleRefresh = () => {
    setCurrentPage(0);
    setRefreshTrigger(prev => prev + 1);
  };

  const groupItemsByFolder = <T extends VideoHistory | AudioHistory | CharacterHistory>(
    items: T[]
  ): Record<string, T[]> => {
    return items.reduce((groups: Record<string, T[]>, item) => {
      const folderKey = item.folder_name || item.conversation_id || t('ungrouped');
      if (!groups[folderKey]) {
        groups[folderKey] = [];
      }
      groups[folderKey].push(item);
      return groups;
    }, {});
  };

  const handleFolderClick = (folderName: string, items: Array<VideoHistory | AudioHistory | CharacterHistory>) => {
    setSelectedFolder({ name: folderName, items });
    onFolderOpenChange?.(true);
  };

  const handleCloseSidebar = () => {
    setSelectedFolder(null);
    setZoomLevel(100);
    onFolderOpenChange?.(false);
  };

  const handleZoomIn = () => {
    setZoomLevel(prev => Math.min(prev + 25, 200));
  };

  const handleZoomOut = () => {
    setZoomLevel(prev => Math.max(prev - 25, 50));
  };

  const handleFolderRename = async (oldName: string, newName: string) => {
    // Find the first item in the folder to get the folder_id
    const allItems = [...videoHistory, ...audioHistory, ...characterHistory];
    const folderItem = allItems.find(item => item.folder_name === oldName);

    if (!folderItem || !folderItem.folder_id) {
      console.error('Cannot rename folder: folder_id not found');
      throw new Error('Folder ID not found');
    }

    try {
      await api.folder.updateFolder(folderItem.folder_id, { name: newName });

      setRefreshTrigger(prev => prev + 1);

      if (selectedFolder && selectedFolder.name === oldName) {
        setSelectedFolder({ ...selectedFolder, name: newName });
      }
    } catch (error) {
      console.error('Error renaming folder:', error);
      throw error;
    }
  };

  const handleFolderDelete = async (folderName: string, folderId: number) => {
    try {
      await api.folder.deleteFolder(folderId);

      setRefreshTrigger(prev => prev + 1);

      if (selectedFolder && selectedFolder.name === folderName) {
        handleCloseSidebar();
      }
    } catch (error) {
      console.error('Error deleting folder:', error);
      throw error;
    }
  };

  const totalCount = mediaType === 'all'
    ? totalVideos + totalAudios + totalCharacters
    : mediaType === 'video'
      ? totalVideos
      : mediaType === 'audio'
        ? totalAudios
        : totalCharacters;

  const isEmpty = videoHistory.length === 0 && audioHistory.length === 0 && characterHistory.length === 0;

  useEffect(() => {
    const videoThreadIds = Array.from(new Set(
      (videoHistory || [])
        .map((item) => item.thread_id)
        .filter((threadId): threadId is string => Boolean(threadId) && !videoPreviewImageByThreadId[threadId])
    ));

    if (videoThreadIds.length === 0) return;

    let cancelled = false;

    const getFirstStoryboardImageUrl = async (threadId: string): Promise<string | null> => {
      const response = await videoAnalysisApi.getKeyframesDataByThreadId(threadId);
      const keyframes = response.code === 0 ? response.data?.keyframes : null;
      if (!Array.isArray(keyframes) || keyframes.length === 0) return null;

      const sortedKeyframes = [...keyframes].sort((a: any, b: any) => {
        const shotA = Number(a?.shot_number ?? Number.MAX_SAFE_INTEGER);
        const shotB = Number(b?.shot_number ?? Number.MAX_SAFE_INTEGER);
        if (shotA !== shotB) return shotA - shotB;
        const frameA = Number(a?.frame_index ?? Number.MAX_SAFE_INTEGER);
        const frameB = Number(b?.frame_index ?? Number.MAX_SAFE_INTEGER);
        return frameA - frameB;
      });

      for (const keyframe of sortedKeyframes) {
        const versions = Array.isArray(keyframe?.versions) ? keyframe.versions : [];
        const currentVersion = versions[keyframe?.current_version_index || 0];
        const imageUrl = currentVersion?.keyframe_url || versions[0]?.keyframe_url || keyframe?.keyframe_url || keyframe?.image_url;
        if (imageUrl) return imageUrl;
      }

      return null;
    };

    const run = async () => {
      const updates: Record<string, string> = {};
      await Promise.all(
        videoThreadIds.map(async (threadId) => {
          try {
            const imageUrl = await getFirstStoryboardImageUrl(threadId);
            if (imageUrl) updates[threadId] = imageUrl;
          } catch {
            // ignore preview fetch failures
          }
        })
      );

      if (!cancelled && Object.keys(updates).length > 0) {
        setVideoPreviewImageByThreadId((prev) => ({ ...prev, ...updates }));
      }
    };

    run();

    return () => {
      cancelled = true;
    };
  }, [videoHistory, videoPreviewImageByThreadId]);

  // Combine and sort all media items by creation date
  const allMediaItems = [
    ...videoHistory.map(item => ({ ...item, mediaType: 'video' as const })),
    ...audioHistory.map(item => ({ ...item, mediaType: 'audio' as const })),
    ...characterHistory.map(item => ({ ...item, mediaType: 'character' as const })),
  ].sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime());

  return (
    <div className="min-h-screen flex w-full relative">

      <div className={`flex-1 transition-all duration-300 ease-in-out ${selectedFolder ? 'mr-[40%]' : ''}`}>
        <div className="container mx-auto px-6">
          <div className="flex items-center justify-between mb-8 animate-in fade-in slide-in-from-top-2 duration-500">
            <Tabs
              value={mediaType}
              onValueChange={(value) => {
                setMediaType(value as MediaType);
                setCurrentPage(0);
              }}
            >
              <TabsList className="transition-all duration-200">
                <TabsTrigger value="all" className="flex items-center gap-2 transition-all duration-200">
                  <Film className="h-4 w-4" />
                  {t('allTypes')}
                </TabsTrigger>
                <TabsTrigger value="video" className="flex items-center gap-2 transition-all duration-200">
                  <Film className="h-4 w-4" />
                  {t('videoType')}
                </TabsTrigger>
                <TabsTrigger value="audio" className="flex items-center gap-2 transition-all duration-200">
                  <Music className="h-4 w-4" />
                  {t('audioType')}
              </TabsTrigger>
              <TabsTrigger value="character" className="flex items-center gap-2 transition-all duration-200">
                <User className="h-4 w-4" />
                {t('characterType')}
              </TabsTrigger>
            </TabsList>
            </Tabs>

            <div className="flex gap-2">
              {(mediaType === 'all' || mediaType === 'video') && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setShowSuccessOnly(!showSuccessOnly)}
                  className={`${
                    showSuccessOnly
                      ? 'bg-purple-600 border-purple-600 hover:bg-purple-700 text-white'
                      : ''
                  }`}
                >
                  <Filter className="h-4 w-4 mr-2" />
                  {showSuccessOnly ? t('showSuccessOnly') : t('showAllVideos')}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                onClick={handleRefresh}
                disabled={isLoading}
              >
                <RefreshCw className={`h-4 w-4 mr-2 ${isLoading ? 'animate-spin' : ''}`} />
                {t('refresh')}
              </Button>
            </div>
          </div>

          <div className="space-y-4">
              {isLoading ? (
                <Card className="glass aspect-square flex items-center justify-center p-6 text-center">
                  <div className="text-muted-foreground">
                    <RefreshCw className="h-12 w-12 animate-spin mx-auto mb-2 opacity-50" />
                    <p className="text-sm">{t('loading')}</p>
                  </div>
                </Card>
              ) : isEmpty ? (
                <Card className="glass aspect-square flex items-center justify-center p-6 text-center">
                  <div className="text-muted-foreground">
                    {mediaType === 'character' ? (
                      <User className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    ) : mediaType === 'audio' ? (
                      <Music className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    ) : (
                      <Film className="h-12 w-12 mx-auto mb-2 opacity-50" />
                    )}
                    <p className="text-sm">
                      {mediaType === 'audio'
                        ? t('noAudioHistory')
                        : mediaType === 'character'
                          ? t('noCharacterHistory')
                          : t('noVideoHistory')}
                    </p>
                  </div>
                </Card>
              ) : (
                <>
                  {/* Responsive Masonry Layout */}
                  {mediaType === 'all' && (
                    <div className="grid gap-6 transition-all duration-300 ease-in-out" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 320px))' }}>
                      {Object.entries(groupItemsByFolder(allMediaItems)).map(([folderName, items], index) => (
                        <div
                          key={folderName}
                          className="animate-in fade-in slide-in-from-bottom-4 transition-all duration-300 ease-in-out"
                          style={{ animationDelay: `${index * 50}ms`, animationFillMode: 'both' }}
                        >
                          <MediaFolderCard
                            folderName={folderName}
                            items={items}
                            videoPreviewImageByThreadId={videoPreviewImageByThreadId}
                            onFolderClick={() => handleFolderClick(folderName, items)}
                            onFolderRename={handleFolderRename}
                            onFolderDelete={handleFolderDelete}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {mediaType === 'video' && (
                    <div className="grid gap-6 transition-all duration-300 ease-in-out" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 320px))' }}>
                      {Object.entries(groupItemsByFolder(videoHistory.map(v => ({...v, mediaType: 'video' as const})))).map(([folderName, items], index) => (
                        <div
                          key={folderName}
                          className="animate-in fade-in slide-in-from-bottom-4 transition-all duration-300 ease-in-out"
                          style={{ animationDelay: `${index * 50}ms`, animationFillMode: 'both' }}
                        >
                          <MediaFolderCard
                            folderName={folderName}
                            items={items}
                            videoPreviewImageByThreadId={videoPreviewImageByThreadId}
                            onFolderClick={() => handleFolderClick(folderName, items)}
                            onFolderRename={handleFolderRename}
                            onFolderDelete={handleFolderDelete}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {mediaType === 'audio' && (
                    <div className="grid gap-6 transition-all duration-300 ease-in-out" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 320px))' }}>
                      {Object.entries(groupItemsByFolder(audioHistory.map(a => ({...a, mediaType: 'audio' as const})))).map(([folderName, items], index) => (
                        <div
                          key={folderName}
                          className="animate-in fade-in slide-in-from-bottom-4 transition-all duration-300 ease-in-out"
                          style={{ animationDelay: `${index * 50}ms`, animationFillMode: 'both' }}
                        >
                          <MediaFolderCard
                            folderName={folderName}
                            items={items}
                            videoPreviewImageByThreadId={videoPreviewImageByThreadId}
                            onFolderClick={() => handleFolderClick(folderName, items)}
                            onFolderRename={handleFolderRename}
                            onFolderDelete={handleFolderDelete}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {mediaType === 'character' && (
                    <div className="grid gap-6 transition-all duration-300 ease-in-out" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 320px))' }}>
                      {Object.entries(groupItemsByFolder(characterHistory.map(c => ({...c, mediaType: 'character' as const})))).map(([folderName, items], index) => (
                        <div
                          key={folderName}
                          className="animate-in fade-in slide-in-from-bottom-4 transition-all duration-300 ease-in-out"
                          style={{ animationDelay: `${index * 50}ms`, animationFillMode: 'both' }}
                        >
                          <MediaFolderCard
                            folderName={folderName}
                            items={items}
                            videoPreviewImageByThreadId={videoPreviewImageByThreadId}
                            onFolderClick={() => handleFolderClick(folderName, items)}
                            onFolderRename={handleFolderRename}
                            onFolderDelete={handleFolderDelete}
                          />
                        </div>
                      ))}
                    </div>
                  )}

                  {/* Pagination */}
                  {!isLoading && !isEmpty && (
                    <>
                      <Separator className="my-6 bg-border" />
                      <div className="flex items-center justify-center gap-4">
                        <Button
                          variant="outline"
                          onClick={() => setCurrentPage((prev) => Math.max(0, prev - 1))}
                          disabled={currentPage === 0}
                        >
                          {t('previous')}
                        </Button>
                        <span className="text-sm text-muted-foreground">
                          {language === 'zh' ? `${t('page')} ${currentPage + 1} 页` : `${t('page')} ${currentPage + 1}`}
                        </span>
                        <Button
                          variant="outline"
                          onClick={() => setCurrentPage((prev) => prev + 1)}
                          disabled={!hasMore}
                        >
                          {t('next')}
                        </Button>
                      </div>
                    </>
                  )}
                </>
              )}
          </div>

        </div>
      </div>

      {selectedFolder && (
        <div className="fixed right-0 top-0 w-[40%] h-screen bg-background border-l border-border/50 z-40 flex flex-col animate-in slide-in-from-right fade-in duration-300 ease-out">
          <div className="flex items-center justify-between p-6 border-b border-border/50">
            <div className="flex items-center gap-3">
              <FolderOpen className="w-6 h-6 text-primary" />
              <div>
                <h2 className="text-2xl font-medium">{selectedFolder.name}</h2>
                <p className="text-sm text-muted-foreground">
                  {language === 'zh'
                    ? `${selectedFolder.items.length} 项`
                    : `${selectedFolder.items.length} item${selectedFolder.items.length !== 1 ? 's' : ''}`}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 px-3 py-1 rounded-lg bg-accent/50">
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleZoomOut}
                  disabled={zoomLevel <= 50}
                  className="h-8 w-8 hover:bg-accent"
                >
                  <ZoomOut className="w-4 h-4" />
                </Button>
                <span className="text-sm font-medium min-w-[3rem] text-center">
                  {zoomLevel}%
                </span>
                <Button
                  variant="ghost"
                  size="icon"
                  onClick={handleZoomIn}
                  disabled={zoomLevel >= 200}
                  className="h-8 w-8 hover:bg-accent"
                >
                  <ZoomIn className="w-4 h-4" />
                </Button>
              </div>
              <Button
                variant="ghost"
                size="icon"
                onClick={handleCloseSidebar}
                className="hover:bg-accent"
              >
                <X className="w-5 h-5" />
              </Button>
            </div>
          </div>

          <ScrollArea className="flex-1 p-6">
            <div className="grid gap-4" style={{
              gridTemplateColumns: `repeat(auto-fill, minmax(${zoomLevel * 1.5}px, 1fr))`
            }}>
              {selectedFolder.items.map((item, index) => {
                const isVideo = 'final_video_url' in item;
                const isAudio = 'audio_url' in item && !('image_url' in item);
                const isCharacter = 'image_url' in item;

                return (
                  <Card
                    key={`${item.id}`}
                    className="glass group relative overflow-hidden transition-all duration-200 flex flex-col cursor-pointer animate-in fade-in zoom-in-95"
                    style={{ animationDelay: `${index * 30}ms`, animationDuration: '300ms', animationFillMode: 'both' }}
                    onClick={() => {
                    if (isVideo) {
                      setSelectedVideo(item as VideoHistory);
                      setIsVideoSheetOpen(true);
                    } else if (isAudio) {
                      setSelectedAudio(item as AudioHistory);
                      setIsAudioSheetOpen(true);
                    } else if (isCharacter) {
                      setSelectedCharacter(item as CharacterHistory);
                      setIsCharacterSheetOpen(true);
                    }
                  }}>
                    <div className="aspect-square bg-muted/20 flex items-center justify-center p-2 overflow-hidden">
                      {isVideo && (
                        <>
                          {videoPreviewImageByThreadId[(item as VideoHistory).thread_id] ? (
                            <div className="relative w-full h-full">
                              <img
                                src={videoPreviewImageByThreadId[(item as VideoHistory).thread_id]}
                                alt={(item as VideoHistory).title || (item as VideoHistory).prompt || ""}
                                className="w-full h-full object-cover"
                              />
                              <div className="absolute inset-0 bg-black/20 flex items-center justify-center">
                                <div className="bg-blue-600/80 rounded-full p-2">
                                  <Play className="w-4 h-4 text-white" />
                                </div>
                              </div>
                            </div>
                          ) : (
                            <Film className="w-12 h-12 text-blue-400" />
                          )}
                        </>
                      )}
                      {isAudio && (
                        <div className="w-full h-full bg-gradient-to-br from-purple-500/20 to-purple-600/20 flex items-center justify-center relative overflow-hidden">
                          <div className="absolute inset-0 flex items-center justify-center gap-0.5 px-2">
                            {[...Array(12)].map((_, i) => (
                              <div
                                key={i}
                                className="flex-1 bg-purple-400/40 rounded-full"
                                style={{
                                  height: `${Math.random() * 60 + 20}%`,
                                }}
                              />
                            ))}
                          </div>
                          <Music className="w-12 h-12 text-purple-400 relative z-10" />
                        </div>
                      )}
                      {isCharacter && (
                        <>
                          {(item as CharacterHistory).image_url ? (
                            <img
                              src={(item as CharacterHistory).image_url}
                              alt={(item as CharacterHistory).name}
                              className="w-full h-full object-cover"
                            />
                          ) : (
                            <User className="w-12 h-12 text-green-400" />
                          )}
                        </>
                      )}
                    </div>
                    <div className="p-3 bg-background/80 backdrop-blur-sm flex-shrink-0">
                      {isVideo && (
                        <>
                          <Badge
                            variant="outline"
                            className="text-xs font-normal px-2 py-0.5 cursor-default select-none truncate max-w-full"
                            title={(item as VideoHistory).prompt}
                          >
                            {getDisplayPromptForUserMessage((item as VideoHistory).prompt)}
                          </Badge>
                          <div className="flex items-center gap-1 mt-1">
                            {(item as VideoHistory).success ? (
                              <CheckCircle className="w-3 h-3 text-green-500" />
                            ) : (
                              <XCircle className="w-3 h-3 text-red-500" />
                            )}
                            <p className="text-xs text-muted-foreground">
                              {formatDate(item.created_at)}
                            </p>
                          </div>
                        </>
                      )}
                      {isAudio && (
                        <>
                          <p className="text-sm font-medium truncate" title={(item as AudioHistory).filename}>
                            {(item as AudioHistory).filename}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(item.created_at)}
                          </p>
                        </>
                      )}
                      {isCharacter && (
                        <>
                          <p className="text-sm font-medium truncate" title={(item as CharacterHistory).name}>
                            {(item as CharacterHistory).name}
                          </p>
                          <p className="text-xs text-muted-foreground">
                            {formatDate(item.created_at)}
                          </p>
                        </>
                      )}
                    </div>
                  </Card>
                );
              })}
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Video Player Dialog */}
      {selectedVideoUrl && (
        <VideoPlayerDialog
          isOpen={isPlayerOpen}
          onClose={() => {
            setIsPlayerOpen(false);
            setSelectedVideoUrl(null);
          }}
          videoUrl={selectedVideoUrl}
        />
      )}

      {/* Character Details Sheet */}
      <CharacterDetailsSheet
        isOpen={isCharacterSheetOpen}
        onClose={() => {
          setIsCharacterSheetOpen(false);
          setSelectedCharacter(null);
        }}
        character={selectedCharacter}
      />

      <VideoDetailsSheet
        isOpen={isVideoSheetOpen}
        onClose={() => {
          setIsVideoSheetOpen(false);
          setSelectedVideo(null);
        }}
        video={selectedVideo}
      />

      <AudioDetailsSheet
        isOpen={isAudioSheetOpen}
        onClose={() => {
          setIsAudioSheetOpen(false);
          setSelectedAudio(null);
        }}
        audio={selectedAudio}
      />
    </div>
  );
};

export default VideoHistorySection;
