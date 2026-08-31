import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Sparkles, Wand2, Loader2, Pencil, Check, RotateCcw, X } from "lucide-react";
import { toast } from "sonner";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";
import { useLanguage } from "@/i18n/LanguageContext";
import type { Language } from "@/i18n/translations";
import { videoEditingApi } from "@/services/api";

type ImgOrientation = "landscape" | "portrait" | "square" | "unknown";

type MagicPreset = { label: string; emoji: string; instruction: string };

/** 与 others/paint-show-magic PromptEditorDialog 同源预设 */
const MAGIC_PRESETS_ZH: MagicPreset[] = [
  { label: "更艺术感", emoji: "🎨", instruction: "增加艺术性、构图与光影张力" },
  { label: "更情绪感", emoji: "💫", instruction: "强化情绪氛围与角色内心戏" },
  { label: "梦幻感", emoji: "🌙", instruction: "加入梦幻、超现实质感与柔光" },
  { label: "电影感", emoji: "🎬", instruction: "电影级调色、镜头语言与景深" },
  { label: "场景换成黄昏", emoji: "🌇", instruction: "场景改为黄昏时段" },
  { label: "场景换成雪夜", emoji: "❄️", instruction: "场景改为下雪的夜晚" },
];

const MAGIC_PRESETS_EN: MagicPreset[] = [
  { label: "More artistic", emoji: "🎨", instruction: "Increase artistry, composition, and lighting contrast" },
  { label: "More emotional", emoji: "💫", instruction: "Strengthen mood and character inner tension" },
  { label: "Dreamlike", emoji: "🌙", instruction: "Add dreamy, surreal texture and soft light" },
  { label: "Cinematic", emoji: "🎬", instruction: "Cinematic color grading, camera language, and depth of field" },
  { label: "Golden hour", emoji: "🌇", instruction: "Change the scene to golden hour" },
  { label: "Snowy night", emoji: "❄️", instruction: "Change the scene to a snowy night" },
];

function getMagicPresets(language: Language): MagicPreset[] {
  return language === "zh" ? MAGIC_PRESETS_ZH : MAGIC_PRESETS_EN;
}

export type VideoArtifactPromptEditorDialogProps = {
  open: boolean;
  title: string;
  image: string;
  initialPrompt: string;
  onClose: () => void;
  /** 用于拉取 AI 建议芯片；不传则仅用本地默认预设 */
  artifactKind?: "character" | "keyframe" | "video";
  /** artifactKind 为 video 时可选：成片片段 URL，与关键帧图一并供多模态 */
  videoUrl?: string | null;
  threadId?: string | null;
  /** 真实 LLM：instruction → 融合后的完整 prompt */
  onRefineInstruction: (instruction: string) => Promise<string>;
  /** 保存并重新生成：使用当前手动编辑区的 prompt */
  onSaveAndRegenerate: (newPrompt: string) => Promise<void>;
};

/**
 * 横图 / 方图：与 paint-show-magic 一致——单栏滚动、顶部原图条（h-[30vh]）、手动编辑、再 AI 编辑。
 * 竖图：原图在左、手动编辑在右，同一行顶对齐（px-6 pt-5）；其下全宽为 AI 编辑。
 */
export function VideoArtifactPromptEditorDialog({
  open,
  title,
  image,
  initialPrompt,
  onClose,
  artifactKind,
  videoUrl,
  threadId,
  onRefineInstruction,
  onSaveAndRegenerate,
}: VideoArtifactPromptEditorDialogProps) {
  const { t, language } = useLanguage();
  const [prompt, setPrompt] = useState(initialPrompt);
  const [instruction, setInstruction] = useState("");
  const [isEnhancing, setIsEnhancing] = useState(false);
  const [activePreset, setActivePreset] = useState<string | null>(null);
  const [generatedPrompt, setGeneratedPrompt] = useState<string | null>(null);
  const [orientation, setOrientation] = useState<ImgOrientation>("unknown");
  const [isSaving, setIsSaving] = useState(false);
  const [chipPresets, setChipPresets] = useState(() => getMagicPresets(language));
  const [presetsLoading, setPresetsLoading] = useState(false);
  const aiGeneratedSectionRef = useRef<HTMLDivElement | null>(null);
  /** 弹窗已卸载或已关闭时，忽略融合/保存异步结束后的 setState，避免警告 */
  const suppressStateAfterUnmountRef = useRef(false);
  useEffect(() => {
    suppressStateAfterUnmountRef.current = false;
    return () => {
      suppressStateAfterUnmountRef.current = true;
    };
  }, []);

  /** 融合开始或结果出现时滚入视口，避免长 Prompt 时用户以为没有响应（character / keyframe / video 共用本弹窗） */
  useEffect(() => {
    if (!open) return;
    if (!isEnhancing && !generatedPrompt) return;
    let cancelled = false;
    const id = window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        if (cancelled) return;
        const el = aiGeneratedSectionRef.current;
        if (!el) return;
        const block: ScrollLogicalPosition =
          generatedPrompt && !isEnhancing ? "start" : "nearest";
        el.scrollIntoView({ behavior: "smooth", block, inline: "nearest" });
      });
    });
    return () => {
      cancelled = true;
      window.cancelAnimationFrame(id);
    };
  }, [open, isEnhancing, generatedPrompt]);

  useEffect(() => {
    if (!open) {
      setIsSaving(false);
      setIsEnhancing(false);
      return;
    }
    setPrompt(initialPrompt);
    setInstruction("");
    setActivePreset(null);
    setGeneratedPrompt(null);
    setOrientation("unknown");
    setIsSaving(false);
    setChipPresets(getMagicPresets(language));
    setPresetsLoading(false);
  }, [open, initialPrompt, language]);

  useEffect(() => {
    if (!open || !artifactKind) {
      setChipPresets(getMagicPresets(language));
      setPresetsLoading(false);
      return;
    }
    const ac = new AbortController();
    setPresetsLoading(true);
    setChipPresets(getMagicPresets(language));
    void videoEditingApi
      .suggestPromptEditPresets(
        {
          artifact_kind: artifactKind,
          prompt: initialPrompt,
          image_url: image,
          video_url: artifactKind === "video" ? (videoUrl ?? undefined) : undefined,
          thread_id: threadId ?? undefined,
        },
        ac.signal,
      )
      .then((res) => {
        const list = res.data?.presets;
        if (Array.isArray(list) && list.length > 0) {
          const cleaned = list
            .map((p) => ({
              label: String(p.label ?? "").trim() || t("presetSuggestionDefault"),
              emoji: String(p.emoji ?? "✨").trim() || "✨",
              instruction: String(p.instruction ?? "").trim(),
            }))
            .filter((p) => p.instruction.length > 0);
          if (cleaned.length > 0) {
            setChipPresets(cleaned);
          }
        }
      })
      .catch(() => {
        if (!ac.signal.aborted) {
          setChipPresets(getMagicPresets(language));
        }
      })
      .finally(() => {
        if (!ac.signal.aborted) {
          setPresetsLoading(false);
        }
      });
    return () => ac.abort();
  }, [open, artifactKind, initialPrompt, image, videoUrl, threadId, language, t]);

  const handleEditPrompt = async () => {
    if (!instruction.trim()) return;
    setIsEnhancing(true);
    setGeneratedPrompt(null);
    try {
      const merged = await onRefineInstruction(instruction.trim());
      if (suppressStateAfterUnmountRef.current) return;
      setGeneratedPrompt(merged);
      setActivePreset(null);
    } catch (e: unknown) {
      if (!suppressStateAfterUnmountRef.current) {
        setGeneratedPrompt(null);
        const msg = e instanceof Error ? e.message : String(e || "");
        toast.error(msg || t("promptMergeFailed"));
      }
    } finally {
      if (!suppressStateAfterUnmountRef.current) {
        setIsEnhancing(false);
      }
    }
  };

  const selectionVars = {
    ["--selection-ring" as string]: "hsl(var(--primary))",
    ["--selection-glow" as string]: "hsl(var(--primary) / 0.22)",
  } as CSSProperties;

  const manualEditLabelRow = (
    <div className="mb-2 flex shrink-0 items-center justify-between">
      <label
        htmlFor="video-artifact-prompt-textarea"
        className="text-xs font-medium uppercase tracking-wide text-muted-foreground"
      >
        {t("manualEdit")}
      </label>
      <span className="text-xs text-muted-foreground">{t("promptCharCount").replace("{count}", String(prompt.length))}</span>
    </div>
  );

  const landscapeManualEdit = (
    <>
      {manualEditLabelRow}
      <textarea
        id="video-artifact-prompt-textarea"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={7}
        className="w-full resize-none rounded-xl border border-input bg-card p-4 text-sm leading-relaxed text-foreground shadow-sm outline-none ring-0 transition-all placeholder:text-muted-foreground focus:border-[var(--selection-ring)] focus:shadow-[0_0_0_3px_var(--selection-glow)]"
        placeholder={t("manualEditPromptPlaceholder")}
      />
    </>
  );

  const portraitManualEdit = (
    <>
      {manualEditLabelRow}
      <textarea
        id="video-artifact-prompt-textarea"
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={2}
        className="min-h-0 w-full flex-1 basis-0 resize-none overflow-y-auto rounded-xl border border-input bg-card p-4 text-sm leading-relaxed text-foreground shadow-sm outline-none ring-0 transition-all placeholder:text-muted-foreground focus:border-[var(--selection-ring)] focus:shadow-[0_0_0_3px_var(--selection-glow)]"
        placeholder={t("manualEditPromptPlaceholder")}
      />
    </>
  );

  const aiSectionClass = cn(
    "border-t border-border bg-muted/40 px-6 py-5",
    orientation === "portrait" ? "mt-0" : "mt-6",
  );

  const renderAiEdit = () => (
    <section className={aiSectionClass}>
      <div className="mb-3 flex items-center gap-2">
        <Sparkles className="h-4 w-4 text-[var(--selection-ring)]" />
        <h3 className="text-sm font-semibold text-foreground">{t("aiEditSection")}</h3>
        <span className="text-xs text-muted-foreground">{t("aiEditHint")}</span>
      </div>

      <div className="mb-4 flex flex-wrap gap-2">
        {presetsLoading ? (
          <span className="inline-flex items-center gap-1.5 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin shrink-0" />
            {t("generatingPresetSuggestions")}
          </span>
        ) : null}
        {chipPresets.map((p, idx) => {
          const isActive = activePreset === p.label;
          return (
            <button
              key={`${p.label}-${idx}`}
              type="button"
              disabled={isEnhancing || presetsLoading}
              onClick={() => {
                setInstruction(p.instruction);
                setActivePreset(p.label);
                requestAnimationFrame(() => {
                  document.getElementById("video-artifact-instruction-input")?.focus();
                });
              }}
              className={[
                "group/chip inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-all",
                "disabled:cursor-not-allowed disabled:opacity-60",
                isActive
                  ? "border-[var(--selection-ring)] bg-[var(--selection-ring)] text-white shadow-md"
                  : "border-border bg-card text-foreground hover:-translate-y-0.5 hover:border-[var(--selection-ring)] hover:text-[var(--selection-ring)] hover:shadow-md",
              ].join(" ")}
            >
              <span className="text-base leading-none">{p.emoji}</span>
              <span className="font-medium">{p.label}</span>
            </button>
          );
        })}
      </div>

      <div className="group/input flex items-center gap-2 rounded-xl border border-input bg-card p-1.5 pl-4 shadow-sm transition-all focus-within:border-[var(--selection-ring)] focus-within:shadow-[0_0_0_3px_var(--selection-glow)]">
        <Wand2 className="h-4 w-4 shrink-0 text-muted-foreground" />
        <input
          id="video-artifact-instruction-input"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void handleEditPrompt();
            }
          }}
          placeholder={t("enterYourIdea")}
          disabled={isEnhancing}
          className="min-w-0 flex-1 bg-transparent py-2 text-sm text-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
        />
        <button
          type="button"
          onClick={() => void handleEditPrompt()}
          disabled={!instruction.trim() || isEnhancing}
          className="inline-flex h-9 shrink-0 items-center gap-1.5 rounded-lg bg-foreground px-3 text-xs font-medium text-background transition-all hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {isEnhancing ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Pencil className="h-3.5 w-3.5" />
          )}
          {t("editPrompt")}
        </button>
      </div>

      {(isEnhancing || generatedPrompt) && (
        <div
          ref={aiGeneratedSectionRef}
          id="video-artifact-ai-generated-prompt"
          className="mt-4 overflow-hidden rounded-xl border border-[var(--selection-ring)]/40 bg-card shadow-sm animate-in fade-in slide-in-from-bottom-2 duration-300"
        >
          <div className="flex items-center justify-between gap-2 border-b border-border bg-[var(--selection-glow)]/10 px-4 py-2.5">
            <div className="flex items-center gap-2">
              <Sparkles className="h-4 w-4 text-[var(--selection-ring)]" />
              <span className="text-xs font-semibold text-foreground">{t("aiGeneratedPromptTitle")}</span>
            </div>
            {generatedPrompt && (
              <div className="flex items-center gap-1">
                <button
                  type="button"
                  disabled={!instruction.trim() || isEnhancing}
                  onClick={() => void handleEditPrompt()}
                  title={t("regenerate")}
                  className="flex h-7 w-7 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {isEnhancing ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--selection-ring)]" aria-hidden />
                  ) : (
                    <RotateCcw className="h-3.5 w-3.5" />
                  )}
                </button>
                <button
                  type="button"
                  disabled={isEnhancing}
                  onClick={() => {
                    setPrompt(generatedPrompt);
                    setGeneratedPrompt(null);
                    setInstruction("");
                  }}
                  className="inline-flex items-center gap-1 rounded-md bg-[var(--selection-ring)] px-2.5 py-1 text-xs font-medium text-white shadow-sm transition-all hover:-translate-y-0.5 hover:shadow-md disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <Check className="h-3.5 w-3.5" />
                  {t("applyToManualEdit")}
                </button>
              </div>
            )}
          </div>
          <div className="max-h-48 overflow-y-auto px-4 py-3">
            {isEnhancing ? (
              <div className="flex items-center gap-2 py-3 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin text-[var(--selection-ring)]" />
                {t("aiGeneratingNewPrompt")}
              </div>
            ) : (
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{generatedPrompt}</p>
            )}
          </div>
        </div>
      )}
    </section>
  );

  return (
    <Dialog open={open} onOpenChange={(nextOpen) => { if (!nextOpen) onClose(); }}>
      <DialogContent
        hideCloseButton
        style={selectionVars}
        className={cn(
          "flex max-h-[min(90dvh,880px)] w-[min(100vw-1.5rem,48rem)] max-w-none flex-col gap-0 overflow-hidden p-0",
          "left-1/2 top-[4vh] translate-x-[-50%] translate-y-0 sm:top-[6vh]",
          "border bg-background shadow-lg",
        )}
      >
        <header className="flex shrink-0 items-center justify-between gap-3 border-b border-border px-6 py-4">
          <div className="flex min-w-0 items-center gap-3">
            <img src={image} alt="" className="h-10 w-10 shrink-0 rounded-lg object-cover ring-1 ring-border" />
            <div className="min-w-0">
              <p className="text-xs text-muted-foreground">{t("artifactPromptDialogLabel")}</p>
              <h2 className="truncate text-base font-semibold text-foreground">{title}</h2>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("close")}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        <img
          src={image}
          alt=""
          aria-hidden
          className="hidden"
          onLoad={(e) => {
            const el = e.currentTarget;
            const w = el.naturalWidth;
            const h = el.naturalHeight;
            if (!w || !h) return;
            if (w > h * 1.05) setOrientation("landscape");
            else if (h > w * 1.05) setOrientation("portrait");
            else setOrientation("square");
          }}
        />

        <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">
            {orientation === "portrait" ? (
              <>
                <div className="flex min-h-0 flex-row items-stretch border-b border-border">
                  <div className="flex w-[33%] min-w-[112px] max-w-[260px] shrink-0 flex-col border-r border-border px-6 pt-5 pb-4 min-h-0">
                    <p className="mb-2 shrink-0 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("originalImageLabel")}</p>
                    <div className="flex min-h-0 flex-1 flex-col items-center justify-center overflow-hidden rounded-xl bg-muted/30 p-1 ring-1 ring-border">
                      <img
                        src={image}
                        alt={title}
                        className="mx-auto block max-h-[min(42dvh,360px)] max-w-full object-contain"
                      />
                    </div>
                  </div>
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col px-6 pt-5 pb-4">{portraitManualEdit}</div>
                </div>
                {renderAiEdit()}
              </>
            ) : (
              <>
                {(orientation === "landscape" || orientation === "square" || orientation === "unknown") && (
                  <div className="border-b border-border bg-muted/30 px-6 py-4">
                    <p className="mb-2 text-xs font-medium uppercase tracking-wide text-muted-foreground">{t("originalImageLabel")}</p>
                    <div className="flex justify-center overflow-hidden rounded-xl bg-muted/30 ring-1 ring-border">
                      <img
                        src={image}
                        alt={title}
                        className="h-[30vh] w-auto max-w-full object-contain"
                      />
                    </div>
                  </div>
                )}
                <section className="px-6 pt-5">{landscapeManualEdit}</section>
                {renderAiEdit()}
              </>
            )}
          </div>

          <footer className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-card/50 px-6 py-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-lg px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
            >
              {t("cancel")}
            </button>
            <button
              type="button"
              disabled={isSaving}
              onClick={() => {
                void (async () => {
                  setIsSaving(true);
                  let didSave = false;
                  try {
                    await onSaveAndRegenerate(prompt);
                    didSave = true;
                  } catch (e: unknown) {
                    const msg = e instanceof Error ? e.message : String(e || "");
                    toast.error(msg || t("saveAndRegenerateFailed"));
                  } finally {
                    if (!suppressStateAfterUnmountRef.current) {
                      setIsSaving(false);
                    }
                  }
                  if (didSave && !suppressStateAfterUnmountRef.current) {
                    onClose();
                  }
                })();
              }}
              className="rounded-lg bg-[var(--selection-ring)] px-5 py-2 text-sm font-medium text-[hsl(var(--primary-foreground))] shadow-md transition-all hover:-translate-y-0.5 hover:shadow-lg disabled:opacity-60"
            >
              {isSaving ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  {t("saving")}
                </span>
              ) : (
                t("saveAndRegenerate")
              )}
            </button>
          </footer>
        </div>
      </DialogContent>
    </Dialog>
  );
}
