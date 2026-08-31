import React from "react";
import { LucideIcon } from "lucide-react";

interface ReelHeaderProps {
  title: string;
  statusText: string;
  statusIcon?: LucideIcon;
  rightContent?: React.ReactNode;
  className?: string;
}

const ReelHeader: React.FC<ReelHeaderProps> = ({
  title,
  statusText,
  statusIcon: StatusIcon,
  rightContent,
  className = "",
}) => {
  return (
    <div className={`sticky top-0 z-30 bg-gradient-to-b from-[#2a2015] via-[#2a2015]/95 to-transparent backdrop-blur-sm ${className}`}>
      <div className="w-full px-[10%] py-6 flex items-center justify-between">
        <div className="flex items-center gap-6">
          {/* Recording indicator and title */}
          <div className="flex items-center gap-3">
            <div className="w-6 h-6 rounded-full bg-red-500 animate-pulse" />
            <h1 className="text-4xl font-semibold text-amber-100 tracking-wide">{title}</h1>
          </div>

          {/* Divider */}
          <div className="h-8 w-px bg-amber-200/30" />

          {/* Status */}
          <div className="flex items-center gap-2">
            {StatusIcon && <StatusIcon className="w-5 h-5 text-amber-200/60" />}
            <p className="text-2xl text-amber-200/60 font-mono">{statusText}</p>
          </div>
        </div>

        {/* Right content slot */}
        {rightContent && (
          <div className="flex items-center gap-2">
            {rightContent}
          </div>
        )}
      </div>
    </div>
  );
};

export { ReelHeader };
export type { ReelHeaderProps };



