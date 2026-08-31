export interface ImmerseShareMockVideo {
  id: string;
  videoSrc: string;
  prompt: string;
  likes?: number;
  comments?: number;
  promptImage?: string;
  title?: string;
}

// NOTE:
// - 这里使用远程公开视频 URL 作为 mock，避免在仓库内引入二进制 mp4/webp 资源。
// - 后续对接后端接口时，把该列表替换为真实 API 返回即可。
export const IMMERSE_SHARE_MOCK_VIDEOS: ImmerseShareMockVideo[] = [
  {
    id: "1",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "做视频。歌词：我今天不想上班。风格：R&B",
    likes: 3800,
    comments: 166,
  },
  {
    id: "2",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "A dreamy sunset scene with floating particles and gentle motion",
  },
  {
    id: "3",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "Abstract colorful waves flowing in slow motion",
  },
  {
    id: "4",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "Cinematic nature scene with birds flying across the sky",
  },
  {
    id: "5",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "Futuristic city with neon lights and flying cars",
  },
  {
    id: "6",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "Ocean waves crashing on a tropical beach at golden hour",
  },
  {
    id: "7",
    videoSrc: "https://interactive-examples.mdn.mozilla.net/media/cc0-videos/flower.mp4",
    prompt: "Magical forest with glowing fireflies and mystical creatures",
  },
];


