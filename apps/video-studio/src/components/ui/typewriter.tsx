import { useEffect, useRef, useState } from "react";

export function useTypewriterText(
  text: string,
  enabled: boolean,
  charsPerSecond: number = 80
) {
  const [displayText, setDisplayText] = useState<string>(enabled ? "" : text);
  const targetRef = useRef<string>(text);

  useEffect(() => {
    targetRef.current = text;

    if (!enabled) {
      setDisplayText(text);
      return;
    }

    setDisplayText((prev) => (text.length < prev.length ? "" : prev));
  }, [text, enabled]);

  useEffect(() => {
    if (!enabled) return;

    let rafId = 0;
    let last = performance.now();

    const tick = (now: number) => {
      const dt = now - last;
      last = now;

      setDisplayText((prev) => {
        const target = targetRef.current || "";
        if (prev.length >= target.length) return prev;
        const add = Math.max(1, Math.floor((dt / 1000) * charsPerSecond));
        return target.slice(0, Math.min(target.length, prev.length + add));
      });

      rafId = requestAnimationFrame(tick);
    };

    rafId = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafId);
  }, [enabled, charsPerSecond]);

  return displayText;
}

export function TypewriterText({
  text,
  enabled,
  charsPerSecond = 80,
  className,
}: {
  text: string;
  enabled: boolean;
  charsPerSecond?: number;
  className?: string;
}) {
  const typed = useTypewriterText(text, enabled, charsPerSecond);
  return <span className={className}>{typed}</span>;
}


