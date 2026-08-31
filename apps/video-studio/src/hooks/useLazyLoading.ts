import { useState, useEffect, useRef, useCallback } from 'react';

interface UseLazyLoadingOptions {
  /** 每次加载的项目数量 */
  itemsPerPage: number;
  /** 触发加载的滚动阈值（距离底部/右侧的像素） */
  threshold?: number;
  /** 预加载的页数 */
  preloadPages?: number;
  /** 初始加载的页数 */
  initialPages?: number;
  /** 滚动方向：垂直（默认）用 scrollTop 判断接近底部；水平用 scrollLeft 判断接近右侧 */
  direction?: 'vertical' | 'horizontal';
  /** 自动加载节流间隔（毫秒） */
  autoLoadThrottleMs?: number;
  /** 连续自动加载上限；离开阈值区域后重置计数 */
  maxConsecutiveAutoLoads?: number;
  /** 触达连续自动加载上限后的冷却时间（毫秒） */
  autoLoadCooldownMs?: number;
}

interface UseLazyLoadingReturn<T> {
  /** 当前可见的项目 */
  visibleItems: T[];
  /** 是否正在加载 */
  isLoading: boolean;
  /** 是否还有更多项目 */
  hasMore: boolean;
  /** 手动触发加载更多 */
  loadMore: () => void;
  /** 重置到初始状态 */
  reset: () => void;
  /** 滚动容器的 ref */
  scrollRef: React.RefObject<HTMLDivElement>;
  /** 当前页数 */
  currentPage: number;
}

export function useLazyLoading<T>(
  allItems: T[],
  options: UseLazyLoadingOptions
): UseLazyLoadingReturn<T> {
  const {
    itemsPerPage,
    threshold = 200,
    preloadPages = 1,
    initialPages = 1,
    direction = 'vertical',
    autoLoadThrottleMs = 400,
    maxConsecutiveAutoLoads = 3,
    autoLoadCooldownMs = 1200
  } = options;

  const [currentPage, setCurrentPage] = useState(initialPages);
  const [isLoading, setIsLoading] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);
  const prevLengthRef = useRef(allItems.length);
  const lastAutoLoadAtRef = useRef(0);
  const consecutiveAutoLoadsRef = useRef(0);
  const lastAutoBlockAtRef = useRef(0);

  // 计算可见项目
  const visibleItems = allItems.slice(0, currentPage * itemsPerPage);
  const hasMore = visibleItems.length < allItems.length;

  // 加载更多项目
  const loadMore = useCallback((source: 'manual' | 'auto' = 'manual') => {
    if (loadingRef.current || !hasMore) return;
    if (source === 'auto') {
      const now = Date.now();
      if (consecutiveAutoLoadsRef.current >= maxConsecutiveAutoLoads) {
        if (now - lastAutoBlockAtRef.current < autoLoadCooldownMs) return;
        consecutiveAutoLoadsRef.current = 0;
      }
      if (now - lastAutoLoadAtRef.current < autoLoadThrottleMs) return;
      lastAutoLoadAtRef.current = now;
      consecutiveAutoLoadsRef.current += 1;
      if (consecutiveAutoLoadsRef.current >= maxConsecutiveAutoLoads) {
        lastAutoBlockAtRef.current = now;
      }
    } else {
      consecutiveAutoLoadsRef.current = 0;
    }
    
    loadingRef.current = true;
    setIsLoading(true);
    
    // 模拟异步加载延迟
    setTimeout(() => {
      setCurrentPage(prev => prev + 1);
      setIsLoading(false);
      loadingRef.current = false;
    }, 100);
  }, [hasMore, autoLoadThrottleMs, maxConsecutiveAutoLoads, autoLoadCooldownMs]);

  // 滚动监听：垂直用 scrollTop 判断接近底部，水平用 scrollLeft 判断接近右侧（避免横向滚动时误触发）
  useEffect(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement) return;

    const handleScroll = () => {
      if (!hasMore || loadingRef.current) return;
      const isHorizontal = direction === 'horizontal';
      const distance = isHorizontal
        ? scrollElement.scrollWidth - scrollElement.scrollLeft - scrollElement.clientWidth
        : scrollElement.scrollHeight - scrollElement.scrollTop - scrollElement.clientHeight;
      if (distance > threshold * 2) {
        consecutiveAutoLoadsRef.current = 0;
      }
      if (distance <= threshold) {
        loadMore('auto');
      }
    };

    scrollElement.addEventListener('scroll', handleScroll, { passive: true });
    return () => scrollElement.removeEventListener('scroll', handleScroll);
  }, [hasMore, loadMore, threshold, direction]);

  // 首屏补载：当内容还未撑满可视区时，自动继续加载（不依赖滚动事件）
  const tryLoadIfNearEnd = useCallback(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement || !hasMore || loadingRef.current) return;

    const isHorizontal = direction === 'horizontal';
    const viewportSize = isHorizontal ? scrollElement.clientWidth : scrollElement.clientHeight;
    if (viewportSize <= 0) return;

    const distance = isHorizontal
      ? scrollElement.scrollWidth - scrollElement.scrollLeft - scrollElement.clientWidth
      : scrollElement.scrollHeight - scrollElement.scrollTop - scrollElement.clientHeight;

    if (distance <= threshold) {
      // 补载走 manual 通道，避免被 auto 连续上限卡死。
      loadMore('manual');
    }
  }, [direction, hasMore, loadMore, threshold]);

  useEffect(() => {
    tryLoadIfNearEnd();
  }, [allItems.length, currentPage, tryLoadIfNearEnd]);

  // 容器尺寸变化（例如 Tab 切换从隐藏到显示）时重试补载
  useEffect(() => {
    const scrollElement = scrollRef.current;
    if (!scrollElement || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(() => {
      tryLoadIfNearEnd();
    });
    observer.observe(scrollElement);
    return () => observer.disconnect();
  }, [tryLoadIfNearEnd]);

  // 重置函数
  const reset = useCallback(() => {
    setCurrentPage(initialPages);
    setIsLoading(false);
    loadingRef.current = false;
    lastAutoLoadAtRef.current = 0;
    consecutiveAutoLoadsRef.current = 0;
    lastAutoBlockAtRef.current = 0;
  }, [initialPages]);

  // 当 allItems 变化时更新分页
  useEffect(() => {
    const prevLength = prevLengthRef.current;
    const nextLength = allItems.length;
    
    if (nextLength < prevLength) {
      reset();
    } else if (nextLength > prevLength) {
      const nextPage = Math.ceil(nextLength / itemsPerPage);
      if (nextPage !== currentPage) {
        setCurrentPage(nextPage);
      }
    }
    
    prevLengthRef.current = nextLength;
  }, [allItems.length, currentPage, itemsPerPage, reset]);

  return {
    visibleItems,
    isLoading,
    hasMore,
    loadMore,
    reset,
    scrollRef,
    currentPage
  };
}
