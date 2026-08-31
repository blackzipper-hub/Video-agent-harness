import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

export function ImageWithLoader({
  src,
  alt,
  className,
  containerClassName,
  onClick,
  title,
}: {
  src?: string;
  alt?: string;
  className?: string;
  containerClassName?: string;
  onClick?: () => void;
  title?: string;
}) {
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [src]);

  if (!src) {
    return (
      <div className={containerClassName}>
        <div className="w-full h-full flex items-center justify-center">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  return (
    <div className={`relative ${containerClassName || ""}`}>
      {!loaded && !failed && (
        <div className="absolute inset-0 flex items-center justify-center bg-black/5">
          <Loader2 className="w-5 h-5 animate-spin text-muted-foreground" />
        </div>
      )}
      <img
        src={src}
        alt={alt}
        title={title}
        className={className}
        onClick={onClick}
        onLoad={() => setLoaded(true)}
        onError={() => setFailed(true)}
      />
    </div>
  );
}


