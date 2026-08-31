import { useNavigate } from "react-router-dom";
import { Card } from "@/components/ui/card";
import { User, Music, Sparkles } from "lucide-react";

interface CreationMode {
  id: string;
  title: string;
  description: string;
  icon: React.ReactNode;
  iconBg: string;
  iconColor: string;
  path: string;
}

const creationModes: CreationMode[] = [
  {
    id: "character-first",
    title: "Character First",
    description: "Already have an avatar? I'll help you find the perfect scene and music to bring them to life.",
    icon: <User className="w-6 h-6" />,
    iconBg: "bg-violet-100",
    iconColor: "text-violet-500",
    path: "/create?mode=character",
  },
  {
    id: "song-first",
    title: "Song First",
    description: "Upload your audio track. I'll generate a character and visuals that match the beat.",
    icon: <Music className="w-6 h-6" />,
    iconBg: "bg-orange-100",
    iconColor: "text-orange-500",
    path: "/create?mode=song",
  },
  {
    id: "full-control",
    title: "Full Control",
    description: "Mix & match. Define both the actor and the audio for precise direction.",
    icon: <Sparkles className="w-6 h-6" />,
    iconBg: "bg-blue-100",
    iconColor: "text-blue-500",
    path: "/create?mode=full",
  },
];

const CreationModeCards = () => {
  const navigate = useNavigate();

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 sm:gap-6">
      {creationModes.map((mode) => (
        <div key={mode.id} className="flex flex-col">
          <h3 className="text-base sm:text-xl font-semibold text-foreground mb-2 sm:mb-3">
            {mode.title}
          </h3>
          <Card
            className="p-4 sm:p-6 bg-card/50 backdrop-blur-sm border border-border/50 rounded-2xl cursor-pointer transition-all duration-300 hover:shadow-lg hover:shadow-primary/5 hover:-translate-y-1 hover:border-border group flex-1 flex flex-col"
            onClick={() => navigate(mode.path)}
          >
            <div
              className={`w-10 h-10 sm:w-14 sm:h-14 rounded-xl ${mode.iconBg} ${mode.iconColor} flex items-center justify-center mb-3 sm:mb-6 transition-transform duration-300 group-hover:scale-110`}
            >
              {mode.icon}
            </div>
            <p className="text-muted-foreground text-xs sm:text-sm leading-relaxed">
              {mode.description}
            </p>
          </Card>
        </div>
      ))}
    </div>
  );
};

export default CreationModeCards;

