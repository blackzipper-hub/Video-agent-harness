/**
 * LazyVideo
 *
 * admin Modal 这类「同屏渲染多个视频」的场景下，直接用 `<video src controls>` 会导致：
 *  - 浏览器对每个 video 都发起 range request 抓 metadata（甚至 auto preload 直接拉视频体）
 *  - N 个视频同时加载 → 首屏卡顿 / 渲染阻塞
 *
 * 该组件默认行为：
 *  - 渲染一个 `preload="metadata" muted playsInline` 的 `<video>`，浏览器仅请求几 KB header，
 *    多数浏览器（Chrome / Safari / Firefox）会自动展示首帧。
 *  - 不挂 controls，避免误以为已加载完整视频；用一个居中 ▶ 蒙层提示可播放。
 *  - 点击 ▶ → expanded 状态，挂上 `controls autoPlay preload="auto"`，此时才真正下载视频体。
 *
 * 设计原则：仅用浏览器原生 metadata 抓首帧，不走 canvas 抓帧，避免 CORS 污染与跨域限制。
 */
import { useState } from "react";
import { Play } from "lucide-react";

interface LazyVideoProps {
  src: string;
  /** 与原 <video> 一致的尺寸/样式类，会同时应用到 placeholder 与播放器，保证占位高度一致 */
  className?: string;
  /** 默认 false。设为 true 时跳过懒加载（极少数 inline 立即播放场景） */
  autoExpand?: boolean;
}

export function LazyVideo({ src, className, autoExpand = false }: LazyVideoProps) {
  const [expanded, setExpanded] = useState(autoExpand);

  if (expanded) {
    return (
      <video
        src={src}
        className={className}
        controls
        autoPlay
        preload="auto"
      />
    );
  }

  return (
    <div className={`relative ${className ?? ""}`}>
      {/* preload="metadata" + muted + playsInline：浏览器只请求 metadata header，多数情况下自动显示首帧；
          不挂 controls，避免触发完整加载，由覆盖层接管"点击播放"。 */}
      <video
        src={src}
        className={`block w-full h-full ${className ?? ""}`}
        muted
        playsInline
        preload="metadata"
      />
      <button
        type="button"
        onClick={() => setExpanded(true)}
        className="absolute inset-0 flex items-center justify-center bg-black/30 hover:bg-black/40 transition-colors rounded-md group"
        aria-label="播放视频"
      >
        <span className="w-12 h-12 rounded-full bg-white/90 group-hover:bg-white flex items-center justify-center shadow-lg">
          <Play className="w-6 h-6 text-black fill-black ml-0.5" />
        </span>
      </button>
    </div>
  );
}
