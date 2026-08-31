import { Music, BookOpen, Mic, Play } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import { useLanguage } from "@/i18n/LanguageContext";

interface DemoCard {
  id: string;
  title: string;
  icon: React.ElementType;
  description: string;
  action: string;
  gradient: string;
  visualType: "gradient" | "soft" | "illustration";
}

const demoCards: DemoCard[] = [
  {
    id: "song2video",
    title: "Cyberpunk Beats",
    icon: Music,
    description: "Generate visuals from audio rhythm.",
    action: "Try this beat",
    gradient: "from-pink-500 via-purple-500 to-violet-600",
    visualType: "gradient",
  },
  {
    id: "story2video",
    title: "Mars Explorer",
    icon: BookOpen,
    description: "Script + BGM auto-generation.",
    action: "Use Story Template",
    gradient: "from-pink-300 via-purple-300 to-violet-400",
    visualType: "soft",
  },
  {
    id: "text2song",
    title: "Monday Mood",
    icon: Mic,
    description: "Turn text into a singing character.",
    action: "Remix",
    gradient: "from-pink-400 via-purple-400 to-violet-500",
    visualType: "illustration",
  },
];

const VisualPlaceholder = ({ type, gradient }: { type: string; gradient: string }) => {
  if (type === "gradient") {
    return (
      <div className={`w-full h-full bg-gradient-to-br ${gradient} rounded-xl flex items-center justify-center`}>
        <div className="w-16 h-16 bg-white/20 backdrop-blur-sm rounded-full flex items-center justify-center">
          <Play className="w-8 h-8 text-white fill-white" />
        </div>
      </div>
    );
  }
  
  if (type === "soft") {
    return (
      <div className={`w-full h-full bg-gradient-to-br ${gradient} rounded-xl relative overflow-hidden`}>
        <div className="absolute inset-0 flex items-center justify-center">
          <div className="w-20 h-20 bg-white/30 rounded-full blur-xl" />
        </div>
        <div className="absolute bottom-4 left-4 right-4 h-2 bg-white/40 rounded-full" />
        <div className="absolute bottom-8 left-4 right-8 h-2 bg-white/30 rounded-full" />
      </div>
    );
  }
  
  return (
    <div className={`w-full h-full bg-gradient-to-br ${gradient} rounded-xl flex items-center justify-center relative overflow-hidden`}>
      <div className="absolute top-4 left-4 w-8 h-8 bg-white/30 rounded-full" />
      <div className="absolute top-6 right-6 w-6 h-6 bg-white/20 rounded-full" />
      <div className="absolute bottom-8 left-8 w-4 h-4 bg-white/25 rounded-full" />
      <div className="w-14 h-14 bg-white/30 backdrop-blur-sm rounded-2xl flex items-center justify-center rotate-12">
        <Mic className="w-7 h-7 text-white" />
      </div>
    </div>
  );
};

const InspirationSection = () => {
  const [hoveredCard, setHoveredCard] = useState<string | null>(null);
  const { t } = useLanguage();

  return (
    <section className="w-screen py-12 flex flex-col items-center -ml-[calc((100vw-100%)/2)]">
      <div className="text-center mb-10">
        <h2 className="text-2xl font-semibold text-foreground mb-2">
          {t('getInspired')}
        </h2>
        <p className="text-muted-foreground text-sm">
          {t('exploreWhatCutiCanCreate')}
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 w-[80vw]">
        {demoCards.map((card) => {
          const Icon = card.icon;
          const isHovered = hoveredCard === card.id;
          
          return (
            <Card
              key={card.id}
              className={`
                group relative overflow-hidden cursor-pointer
                border border-border/50 bg-card
                transition-all duration-300 ease-out
                hover:shadow-xl hover:shadow-primary/5
                ${isHovered ? "scale-[1.02]" : "scale-100"}
              `}
              onMouseEnter={() => setHoveredCard(card.id)}
              onMouseLeave={() => setHoveredCard(null)}
            >
              <div className="aspect-[4/3] p-3">
                <VisualPlaceholder type={card.visualType} gradient={card.gradient} />
              </div>

              <div className="p-4 pt-2">
                <div className="flex items-center gap-2 mb-2">
                  <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-pink-500 via-purple-500 to-violet-600 flex items-center justify-center">
                    <Icon className="w-4 h-4 text-white" />
                  </div>
                  <h3 className="font-medium text-foreground">{card.title}</h3>
                </div>

                <p className="text-sm text-muted-foreground mb-3">
                  {card.description}
                </p>

                <Button
                  variant="ghost"
                  size="sm"
                  className={`
                    w-full justify-center text-sm font-medium
                    bg-muted/50 hover:bg-muted
                    transition-all duration-200
                    ${isHovered ? "bg-gradient-to-r from-pink-500 via-purple-500 to-violet-600 text-white hover:from-pink-600 hover:via-purple-600 hover:to-violet-700" : ""}
                  `}
                >
                  {isHovered ? "Use Template" : card.action}
                </Button>
              </div>

              <div 
                className={`
                  absolute inset-0 pointer-events-none
                  bg-gradient-to-t from-primary/5 to-transparent
                  transition-opacity duration-300
                  ${isHovered ? "opacity-100" : "opacity-0"}
                `}
              />
            </Card>
          );
        })}
      </div>
    </section>
  );
};

export default InspirationSection;

