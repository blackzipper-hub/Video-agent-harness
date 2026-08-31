import { useState, useEffect } from "react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FolderOpen, Trash2 } from "lucide-react";
import { useLanguage } from "@/i18n/LanguageContext";

interface Asset {
  id: string;
  name: string;
  type: string;
  file_url: string;
  thumbnail_url: string | null;
  created_at: string;
  folder_name: string | null;
}

interface FolderCardProps {
  folderName: string;
  assets: Asset[];
  onDeleteAsset: (assetId: string, fileUrl: string) => void;
  onFolderClick: () => void;
  onFolderRename: (oldName: string, newName: string) => Promise<void>;
}

export const FolderCard = ({ folderName, assets, onDeleteAsset, onFolderClick, onFolderRename }: FolderCardProps) => {
  const { language, t } = useLanguage();
  const [isEditing, setIsEditing] = useState(false);
  const [editedName, setEditedName] = useState(folderName);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    setEditedName(folderName);
  }, [folderName]);

  const previewAssets = assets.slice(0, 3);
  const itemCountLabel = language === 'zh'
    ? `${assets.length} 项`
    : `${assets.length} item${assets.length !== 1 ? 's' : ''}`;

  const handleNameClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    setIsEditing(true);
    setEditedName(folderName);
  };

  const handleSave = async () => {
    if (!editedName.trim() || editedName === folderName) {
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

  return (
    <Card
      className="glass p-6 cursor-pointer hover:bg-accent/5 transition-colors"
      onClick={onFolderClick}
    >
      <div className="flex items-start justify-between mb-4">
        <div className="flex items-center gap-3 flex-1 min-w-0">
          <FolderOpen className="w-6 h-6 text-primary shrink-0" />
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
              <h3
                className="text-lg font-medium cursor-text hover:text-primary transition-colors truncate"
                onClick={handleNameClick}
                title={t('clickToEdit')}
              >
                {folderName}
              </h3>
            )}
            <p className="text-sm text-muted-foreground">
              {itemCountLabel}
            </p>
          </div>
        </div>
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
        {previewAssets.map((asset, index) => {
          const totalCards = Math.min(previewAssets.length, 3);
          const baseRotation = ((index - (totalCards - 1) / 2) * 25);
          const hoverRotation = ((index - (totalCards - 1) / 2) * 35);

          return (
            <div
              key={asset.id}
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
                <img
                  src={asset.thumbnail_url || asset.file_url}
                  alt={asset.name}
                  className="max-w-full max-h-full object-contain"
                />
                <Button
                  variant="ghost"
                  size="icon"
                  className="absolute top-1 right-1 h-6 w-6 opacity-0 group-hover/card:opacity-100 transition-opacity bg-background/90 hover:bg-destructive hover:text-destructive-foreground"
                  onClick={(e) => {
                    e.stopPropagation();
                    onDeleteAsset(asset.id, asset.file_url);
                  }}
                >
                  <Trash2 className="w-3 h-3" />
                </Button>
              </Card>
            </div>
          );
        })}
      </div>
    </Card>
  );
};
