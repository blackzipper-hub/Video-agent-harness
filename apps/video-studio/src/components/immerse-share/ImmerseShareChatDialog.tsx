import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { createPortal } from "react-dom";
import { useLanguage } from "@/i18n/LanguageContext";
import { useAuth } from "@/hooks/useAuth";
import { DEFAULT_VIDEO_OPTIONS, DEFAULT_IMAGE_GENERATION_TOOL } from "@/constants/defaults";
import { toast } from "sonner";

type PromptData = {
  text: string;
  image?: string;
};

type ChatMessage = {
  id: string;
  text: string;
  isUser: boolean;
  image?: string;
  isLink?: boolean;
  linkUrl?: string;
  linkText?: string;
};

export interface ImmerseShareChatDialogProps {
  pendingPrompt?: PromptData | null;
  onClearPendingPrompt?: () => void;
  avatarSrc?: string;
  collapsedBreathBgClassName?: string;
  collapsedBreathOuterBgClassName?: string;
}

export const ImmerseShareChatDialog = ({ pendingPrompt, onClearPendingPrompt, avatarSrc, collapsedBreathBgClassName, collapsedBreathOuterBgClassName }: ImmerseShareChatDialogProps) => {
  const navigate = useNavigate();
  const { lang: routeLang } = useParams<{ lang?: string }>();
  const { t, language } = useLanguage();
  const { isLoggedIn } = useAuth();
  const [inputValue, setInputValue] = useState("");
  const [isExpanded, setIsExpanded] = useState(false);
  const [attachedImage, setAttachedImage] = useState<string | null>(null);
  const [isTyping, setIsTyping] = useState(false);
  const breathBgClassName = collapsedBreathBgClassName ?? "bg-pink-500/30";
  const breathOuterBgClassName = collapsedBreathOuterBgClassName ?? "bg-pink-500/20";
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "1",
      text: "Hi! I'm Cuti. Tell me what music video you want to create!",
      isUser: false,
    },
  ]);

  const dialogRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isExpanded]);

  useEffect(() => {
    if (pendingPrompt) {
      setInputValue(pendingPrompt.text);
      if (pendingPrompt.image) {
        setAttachedImage(pendingPrompt.image);
      }
      setIsExpanded(true);
    }
  }, [pendingPrompt]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dialogRef.current && !dialogRef.current.contains(event.target as Node)) {
        setIsExpanded(false);
      }
    };

    if (isExpanded) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isExpanded]);

  const sendMessage = () => {
    if (!inputValue.trim() && !attachedImage) return;

    const newMessages: ChatMessage[] = [];
    const userText = inputValue.trim();

    if (attachedImage) {
      newMessages.push({
        id: `${Date.now()}`,
        text: "",
        isUser: true,
        image: attachedImage,
      });
    }

    if (userText) {
      newMessages.push({
        id: `${Date.now() + 1}`,
        text: userText,
        isUser: true,
      });
    }

    setMessages((prev) => [...prev, ...newMessages]);
    setInputValue("");
    setAttachedImage(null);
    onClearPendingPrompt?.();
    setIsExpanded(true);
    setIsTyping(false);

    if (userText) {
      if (!isLoggedIn) {
        toast(t("pleaseLoginFeature"));
      }
      const lang = routeLang || language || "en";
      const userOption = {
        aspect_ratio: DEFAULT_VIDEO_OPTIONS.aspectRatio,
        resolution: DEFAULT_VIDEO_OPTIONS.resolution,
        duration: DEFAULT_VIDEO_OPTIONS.duration,
        video_generation_tool: "auto",
        image_generation_tool: DEFAULT_IMAGE_GENERATION_TOOL,
        content_category: "Default",
        lipsync_coverage: DEFAULT_VIDEO_OPTIONS.lipsyncCoverage,
        enable_continuity_mode: DEFAULT_VIDEO_OPTIONS.enableContinuityMode,
        full_auto: DEFAULT_VIDEO_OPTIONS.autoContinueOnInterrupt,
      };
      navigate(`/${lang}/create`, {
        state: {
          initialPrompt: userText,
          shouldAutoSend: true,
          agentType: "auto",
          userOption,
        },
      });
    }
  };

  if (typeof document === "undefined") return null;

  return createPortal((
    <div
      ref={dialogRef}
      className="flex items-end gap-3"
      style={{ position: "fixed", left: 96, bottom: 16, zIndex: 2147483647 }}
    >
      <button
        onClick={() => setIsExpanded((v) => !v)}
        className="relative w-12 h-12 rounded-full bg-background/95 backdrop-blur-xl shadow-2xl flex items-center justify-center hover:scale-105 transition-transform flex-shrink-0 border border-border"
        aria-label="Toggle chat"
      >
        {!isExpanded && (
          <>
            <div className={`pointer-events-none absolute inset-0 rounded-full ${breathBgClassName} animate-breath`} />
            <div className={`pointer-events-none absolute inset-[-4px] rounded-full ${breathOuterBgClassName} animate-breath [animation-delay:0.5s]`} />
          </>
        )}
        {avatarSrc ? (
          <img
            src={avatarSrc}
            alt="Chat avatar"
            className="relative z-10 w-full h-full rounded-full object-cover"
          />
        ) : (
          <span className="relative z-10 text-sm font-semibold text-white">C</span>
        )}
      </button>

      <div
        className={`bg-background/95 backdrop-blur-xl rounded-3xl overflow-hidden shadow-2xl flex flex-col transition-all duration-300 ease-out border border-border ${
          isExpanded ? "opacity-100 translate-x-0 w-[930px] max-w-[calc(100vw-10rem)]" : "opacity-0 -translate-x-4 w-0 pointer-events-none"
        }`}
      >
        <div className="overflow-y-auto p-4 space-y-3 max-h-[280px] scrollbar-hide text-foreground">
          {messages.map((m) => (
            <div key={m.id} className={`flex items-start gap-2 ${m.isUser ? "flex-row-reverse" : ""}`}>
              <div className={`rounded-2xl max-w-[80%] overflow-hidden ${m.isUser ? "bg-primary text-primary-foreground" : "bg-muted/60 text-foreground"}`}>
                {m.image && (
                  <div className="w-full">
                    <img src={m.image} alt="Attached" className="w-full max-w-[200px] object-cover" />
                  </div>
                )}
                {m.text && <p className="text-sm px-3 py-2">{m.text}</p>}
                {m.isLink && m.linkUrl && (
                  <button
                    onClick={() => navigate(m.linkUrl!)}
                    className="w-full px-3 py-2 text-sm text-primary hover:bg-background/20 transition-colors text-left font-medium"
                  >
                    {m.linkText || "Open"}
                  </button>
                )}
              </div>
            </div>
          ))}

          {isTyping && (
            <div className="flex items-start gap-2">
              <div className="rounded-2xl bg-muted/60 px-3 py-2">
                <div className="flex gap-1">
                  <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: "0ms" }} />
                  <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: "150ms" }} />
                  <span className="w-2 h-2 bg-muted-foreground/60 rounded-full animate-bounce" style={{ animationDelay: "300ms" }} />
                </div>
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        <div className="h-px bg-border/50 mx-4" />

        <div className="p-4">
          <input
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            placeholder={t('describeYourVideo')}
            className="w-full bg-transparent text-foreground placeholder:text-muted-foreground text-base outline-none"
            onKeyDown={(e) => e.key === "Enter" && sendMessage()}
            onPaste={(e) => {
              const clipboardData = e.clipboardData;
              if (!clipboardData) return;
              for (let i = 0; i < clipboardData.items.length; i++) {
                const item = clipboardData.items[i];
                if (item.kind !== "file" || !item.type.startsWith("image/")) continue;
                const file = item.getAsFile();
                if (!file) continue;
                e.preventDefault();
                const reader = new FileReader();
                reader.onload = () => {
                  const r = reader.result;
                  if (typeof r === "string") setAttachedImage(r);
                };
                reader.readAsDataURL(file);
                toast.success(t("pastedImage"));
                break;
              }
            }}
          />

          <div className="flex items-center justify-between mt-3">
            <button
              onClick={() => navigate(`/${routeLang || language || "en"}/create`)}
              className="w-9 h-9 rounded-full bg-muted/50 flex items-center justify-center hover:bg-muted/60 transition-colors"
              aria-label="Open create"
            >
              <span className="text-foreground text-sm">+</span>
            </button>

            <button
              onClick={sendMessage}
              className="w-10 h-10 rounded-full bg-primary flex items-center justify-center hover:bg-primary/90 transition-colors shadow-lg shadow-primary/30"
              aria-label="Send"
            >
              <span className="text-primary-foreground text-sm">↑</span>
            </button>
          </div>
        </div>
      </div>
    </div>
  ), document.body);
};


