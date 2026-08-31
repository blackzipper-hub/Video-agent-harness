import { useState, useEffect } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { FolderOpen, Trash2, Film, Music, User, Play, MoreVertical, Pencil } from "lucide-react";
import type { VideoHistory, AudioHistory, CharacterHistory } from "@/types/api";
import { useLanguage } from "@/i18n/LanguageContext";

type MediaItem = (VideoHistory | AudioHistory | CharacterHistory) & { mediaType: 'video' | 'audio' | 'character' };

interface MediaFolderCardProps {
  folderName: string;
  items: MediaItem[];
  videoPreviewImageByThreadId?: Record<string, string>;
  onDeleteItem?: (item: MediaItem) => void;
  onFolderClick: () => void;
  onFolderRename?: (oldName: string, newName: string) => Promise<void>;
  onFolderDelete?: (folderName: string, folderId: number) => Promise<void>;
}

export const MediaFolderCard = ({ folderName, items, videoPreviewImageByThreadId = {}, onDeleteItem, onFolderClick, onFolderRename, onFolderDelete }: MediaFolderCardProps) => {
  const { language, t } = useLanguage();
  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState(folderName);
  const [isSaving, setIsSaving] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [isDeleting, setIsDeleting] = useState(false);

  useEffect(() => {
    setEditedName(folderName);
  }, [folderName]);

  const previewItems = items.slice(0, 3);
  const itemCountLabel = language === 'zh'
    ? `${items.length} 项`
    : `${items.length} item${items.length !== 1 ? 's' : ''}`;

  const handleNameClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    if (onFolderRename) {
      setIsEditing(true);
      setEditedName(folderName);
    }
  };

  const handleSave = async () => {
    if (!editedName.trim() || editedName === folderName || !onFolderRename) {
      setIsEditing(false);
      setEditedName(folderName);
      return;
    }

    setIsSaving(true);
    try {
      await onFolderRename(folderName, editedName.trim());
      setIsEditing(false);
    } catch (error) {
      console.error('Error renaming folder:', error);
      setEditedName(folderName);
      setIsEditing(false);
    } finally {
      setIsSaving(false);
    }
  };

  const handleBlur = () => {
    handleSave();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleSave();
    } else if (e.key === 'Escape') {
      setIsEditing(false);
      setEditedName(folderName);
    }
  };

  const handleDeleteFolder = async () => {
    if (!onFolderDelete) return;

    const folderItem = items.find(item => item.folder_id);
    if (!folderItem || !folderItem.folder_id) {
      console.error('Cannot delete folder: folder_id not found');
      return;
    }

    setIsDeleting(true);
    try {
      await onFolderDelete(folderName, folderItem.folder_id);
      setShowDeleteDialog(false);
    } catch (error) {
      console.error('Error deleting folder:', error);
    } finally {
      setIsDeleting(false);
    }
  };

  const getItemThumbnail = (item: MediaItem) => {
    if (item.mediaType === 'video') {
      const video = item as VideoHistory;
      return videoPreviewImageByThreadId[video.thread_id] || null;
    } else if (item.mediaType === 'character') {
      const character = item as CharacterHistory;
      return character.image_url;
    }
    return null;
  };

  const renderItemPreview = (item: MediaItem) => {
    const thumbnail = getItemThumbnail(item);

    if (item.mediaType === 'video') {
      return (
        <>
          {thumbnail ? (
            <div className="relative w-full h-full overflow-hidden bg-secondary flex items-center justify-center">
              <img
                src={thumbnail}
                alt={(item as VideoHistory).title || (item as VideoHistory).prompt || ""}
                className="max-w-full max-h-full object-contain"
              />
              <div className="absolute inset-0 bg-black/20 flex items-center justify-center">
                <div className="bg-blue-600/80 rounded-full p-2">
                  <Play className="w-4 h-4 text-white" />
                </div>
              </div>
            </div>
          ) : (
            <div className="w-full h-full bg-gradient-to-br from-blue-500/20 to-blue-600/20 flex items-center justify-center">
              <Film className="w-8 h-8 text-blue-400" />
            </div>
          )}
        </>
      );
    } else if (item.mediaType === 'audio') {
      return (
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
          <Music className="w-8 h-8 text-purple-400 relative z-10" />
        </div>
      );
    } else if (item.mediaType === 'character') {
      return (
        <>
          {thumbnail ? (
            <img
              src={thumbnail}
              alt={(item as CharacterHistory).name}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="w-full h-full bg-gradient-to-br from-green-500/20 to-green-600/20 flex items-center justify-center">
              <User className="w-8 h-8 text-green-400" />
            </div>
          )}
        </>
      );
    }
  };

  const getFolderColor = () => {
    const types = items.map(item => item.mediaType);
    if (types.every(t => t === 'video')) return 'text-blue-500';
    if (types.every(t => t === 'audio')) return 'text-purple-500';
    if (types.every(t => t === 'character')) return 'text-green-500';
    return 'text-primary';
  };

  return (
    <Card
      className="glass p-6 cursor-pointer hover:bg-accent/5 transition-all duration-300 ease-in-out"
      onClick={onFolderClick}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <FolderOpen className={`w-6 h-6 ${getFolderColor()} shrink-0`} />
          <div className="flex-1 min-w-0">
            {isEditing ? (
              <div className="flex items-center gap-2" onClick={(e) => e.stopPropagation()}>
                <Input
                  value={editedName}
                  onChange={(e) => setEditedName(e.target.value)}
                  onKeyDown={handleKeyDown}
                  onBlur={handleBlur}
                  className="h-8 text-lg font-medium"
                  maxLength={50}
                  autoFocus
                  disabled={isSaving}
                />
              </div>
            ) : (
              <h3 className="text-lg font-medium transition-colors truncate">
                {folderName}
              </h3>
            )}
            <p className="text-sm text-muted-foreground">
              {itemCountLabel}
            </p>
          </div>
        </div>

        {/* Dropdown Menu */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild onClick={(e) => e.stopPropagation()}>
            <Button variant="ghost" size="icon" className="h-8 w-8 shrink-0">
              <MoreVertical className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                if (onFolderRename) {
                  setIsEditing(true);
                  setEditedName(folderName);
                }
              }}
              disabled={!onFolderRename}
            >
              <Pencil className="h-4 w-4 mr-2" />
              {t('rename')}
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={(e) => {
                e.stopPropagation();
                setShowDeleteDialog(true);
              }}
              className="text-destructive focus:text-destructive"
              disabled={!onFolderDelete}
            >
              <Trash2 className="h-4 w-4 mr-2" />
              {t('delete')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <div
        className="relative h-48 flex items-center justify-center group/folder"
        onMouseEnter={(e) => {
          const cards = e.currentTarget.querySelectorAll('.fan-card');
          cards.forEach((card) => {
            const htmlCard = card as HTMLElement;
            const hoverTransform = htmlCard.getAttribute('data-hover-transform');
            if (hoverTransform) {
              htmlCard.style.transform = hoverTransform;
            }
          });
        }}
        onMouseLeave={(e) => {
          const cards = e.currentTarget.querySelectorAll('.fan-card');
          cards.forEach((card) => {
            const htmlCard = card as HTMLElement;
            const baseTransform = htmlCard.getAttribute('data-base-transform');
            if (baseTransform) {
              htmlCard.style.transform = baseTransform;
            }
          });
        }}
      >
        {previewItems.map((item, index) => {
          const totalCards = Math.min(previewItems.length, 3);
          const baseRotation = ((index - (totalCards - 1) / 2) * 25);
          const hoverRotation = ((index - (totalCards - 1) / 2) * 35);

          return (
            <div
              key={`${item.mediaType}-${item.id}`}
              className="absolute w-36 h-44 fan-card group/card"
              data-base-transform={`rotate(${baseRotation}deg)`}
              data-hover-transform={`rotate(${hoverRotation}deg) translateY(-8px)`}
              style={{
                transform: `rotate(${baseRotation}deg)`,
                transformOrigin: 'bottom center',
                zIndex: totalCards - Math.abs(index - (totalCards - 1) / 2),
                left: `calc(50% - 72px + ${(index - (totalCards - 1) / 2) * 20}px)`,
                bottom: '10px',
                transition: 'all 0.6s cubic-bezier(0.34, 1.56, 0.64, 1)',
              }}
            >
              <Card className="w-full h-full overflow-hidden border-2 border-border shadow-lg group-hover/card:shadow-2xl group-hover/card:shadow-primary/20 group-hover/card:border-primary/50 bg-card flex items-center justify-center p-1 relative">
                {renderItemPreview(item)}
                {onDeleteItem && (
                  <Button
                    variant="ghost"
                    size="icon"
                    className="absolute top-1 right-1 h-6 w-6 opacity-0 group-hover/card:opacity-100 transition-opacity bg-background/90 hover:bg-destructive hover:text-destructive-foreground"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteItem(item);
                    }}
                  >
                    <Trash2 className="w-3 h-3" />
                  </Button>
                )}
              </Card>
            </div>
          );
        })}
      </div>

      {/* Delete Confirmation Dialog */}
      <AlertDialog open={showDeleteDialog} onOpenChange={setShowDeleteDialog}>
        <AlertDialogContent onClick={(e) => e.stopPropagation()}>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('folderDeleteTitle')}</AlertDialogTitle>
            <AlertDialogDescription>
              {t('folderDeleteDescription')
                .replace('{{folderName}}', folderName)
                .replace('{{count}}', String(items.length))}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeleting}>{t('cancel')}</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDeleteFolder}
              disabled={isDeleting}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              {isDeleting ? t('deleting') : t('delete')}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </Card>
  );
};
