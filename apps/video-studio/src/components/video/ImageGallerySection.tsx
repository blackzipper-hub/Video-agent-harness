import React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useLanguage } from '@/i18n/LanguageContext';
import { Image } from 'lucide-react';

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

interface ImageGallerySectionProps {
  messages: Message[];
}

interface ImageItem {
  title: string;
  url: string;
  description?: string;
}

const ImageGallerySection: React.FC<ImageGallerySectionProps> = ({ messages }) => {
  const { t } = useLanguage();

  // Extract images from messages
  const extractImages = (): ImageItem[] => {
    const images: ImageItem[] = [];

    // Find all image_agent_generated messages
    const imageMessages = messages.filter(
      msg => msg.event_type === 'image_agent_generated' && msg.event_data?.image_content
    );

    imageMessages.forEach(msg => {
      const imageContent = msg.event_data.image_content;

      // Parse markdown-style image links: ![title](url) or [title](url)
      const regex = /!?\[([^\]]*)\]\((https:\/\/[^)]+\.(jpg|jpeg|png|gif|webp)[^)]*)\)/gi;
      let match;

      while ((match = regex.exec(imageContent)) !== null) {
        const [, title, url] = match;
        images.push({
          title: title.trim() || 'Generated Image',
          url: url.trim()
        });
      }

      // Also try to extract direct URLs if no markdown format found
      if (images.length === 0) {
        const urlRegex = /(https:\/\/[^\s]+\.(jpg|jpeg|png|gif|webp))/gi;
        let urlMatch;
        let imageIndex = 1;

        while ((urlMatch = urlRegex.exec(imageContent)) !== null) {
          images.push({
            title: `Generated Image ${imageIndex}`,
            url: urlMatch[1].trim()
          });
          imageIndex++;
        }
      }
    });

    return images;
  };

  const imageItems = extractImages();

  if (imageItems.length === 0) {
    return null;
  }

  return (
    <div className="p-6 space-y-4">
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Image className="w-5 h-5" />
            {t('generatedImages') || 'Generated Images'}
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-6">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {imageItems.map((image, index) => (
              <div key={index} className="space-y-2">
                <div className="aspect-square overflow-hidden rounded-lg border border-white/20 bg-black/5 flex items-center justify-center">
                  <img
                    src={image.url}
                    alt={image.title}
                    className="w-full h-full object-cover cursor-pointer hover:opacity-90 transition-opacity"
                    onClick={() => window.open(image.url, '_blank')}
                    loading="lazy"
                  />
                </div>
                <h3 className="text-sm font-medium text-foreground text-center">
                  {image.title}
                </h3>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default ImageGallerySection;
