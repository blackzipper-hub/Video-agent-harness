import { useState } from "react";
import { Button } from "@/components/ui/button";
import { 
  Play, 
  Pause, 
  Volume2, 
  VolumeX, 
  Maximize, 
  ArrowLeft,
  Download,
  Share2
} from "lucide-react";
import { useNavigate, useLocation } from "react-router-dom";

const VideoPlayerPage = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [isPlaying, setIsPlaying] = useState(false);
  const [isMuted, setIsMuted] = useState(false);
  const [progress, setProgress] = useState(0);

  const handleBackToEditor = () => {
    const from = location.state?.from;
    if (from === 'instant-generation') {
      navigate('/instant-generation', { state: { prompt: location.state?.prompt, skipGeneration: true } });
    } else {
      navigate(-1);
    }
  };

  return (
    <div className="h-screen bg-black flex flex-col">
      {/* Header */}
      <div className="absolute top-0 left-0 right-0 z-10 p-6 bg-gradient-to-b from-black/80 to-transparent">
        <div className="flex items-center justify-between">
          <Button
            variant="ghost"
            size="sm"
            onClick={handleBackToEditor}
            className="text-white hover:bg-white/10"
          >
            <ArrowLeft className="w-4 h-4 mr-2" />
            Back
          </Button>
          <div className="flex gap-2">
            <Button variant="ghost" size="sm" className="text-white hover:bg-white/10">
              <Download className="w-4 h-4 mr-2" />
              Download
            </Button>
            <Button variant="ghost" size="sm" className="text-white hover:bg-white/10">
              <Share2 className="w-4 h-4 mr-2" />
              Share
            </Button>
          </div>
        </div>
      </div>

      {/* Video Player */}
      <div className="flex-1 flex items-center justify-center relative">
        <div className="w-full max-w-6xl aspect-video bg-gradient-cosmic rounded-lg overflow-hidden">
          {/* Video Placeholder */}
          <div className="w-full h-full flex items-center justify-center">
            <div className="text-center">
              <div className="w-24 h-24 bg-white/10 rounded-full flex items-center justify-center mb-4 mx-auto backdrop-blur-sm">
                {isPlaying ? (
                  <Pause className="w-12 h-12 text-white" />
                ) : (
                  <Play className="w-12 h-12 text-white ml-1" />
                )}
              </div>
              <p className="text-white/80 text-lg mb-2">Surveillance View Animal Video</p>
              <p className="text-white/60 text-sm">Video Duration: 0:22</p>
            </div>
          </div>
        </div>
      </div>

      {/* Video Controls */}
      <div className="absolute bottom-0 left-0 right-0 z-10 p-6 bg-gradient-to-t from-black/80 to-transparent">
        <div className="max-w-6xl mx-auto space-y-4">
          {/* Progress Bar */}
          <div className="relative h-1 bg-white/20 rounded-full cursor-pointer group">
            <div 
              className="absolute h-full bg-primary rounded-full transition-all"
              style={{ width: `${progress}%` }}
            />
            <div 
              className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
              style={{ left: `${progress}%`, marginLeft: '-6px' }}
            />
          </div>

          {/* Control Buttons */}
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-4">
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsPlaying(!isPlaying)}
                className="text-white hover:bg-white/10 h-10 w-10 p-0"
              >
                {isPlaying ? (
                  <Pause className="w-5 h-5" />
                ) : (
                  <Play className="w-5 h-5 ml-0.5" />
                )}
              </Button>
              
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setIsMuted(!isMuted)}
                className="text-white hover:bg-white/10 h-10 w-10 p-0"
              >
                {isMuted ? (
                  <VolumeX className="w-5 h-5" />
                ) : (
                  <Volume2 className="w-5 h-5" />
                )}
              </Button>

              <span className="text-white text-sm">0:00 / 0:22</span>
            </div>

            <Button
              variant="ghost"
              size="sm"
              className="text-white hover:bg-white/10 h-10 w-10 p-0"
            >
              <Maximize className="w-5 h-5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default VideoPlayerPage;
