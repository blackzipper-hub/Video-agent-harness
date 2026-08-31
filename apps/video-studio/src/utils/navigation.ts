import { useNavigate, useLocation, useParams } from 'react-router-dom';
import { Language } from '../i18n/translations';

/**
 * 获取当前语言（从URL路径中）
 */
export const useCurrentLanguage = (): Language => {
  const { lang } = useParams<{ lang: string }>();
  if (lang === 'en' || lang === 'zh') {
    return lang as Language;
  }
  const saved = localStorage.getItem('language');
  return (saved as Language) || 'en';
};

/**
 * 创建带语言前缀的路径
 */
export const createLocalizedPath = (path: string, lang?: Language): string => {
  const currentLang = lang || (localStorage.getItem('language') as Language) || 'en';
  
  // 如果路径已经包含语言前缀，先移除它
  const cleanPath = path.replace(/^\/(en|zh)\//, '/');
  
  // 确保路径以 / 开头
  const normalizedPath = cleanPath.startsWith('/') ? cleanPath : `/${cleanPath}`;
  
  return `/${currentLang}${normalizedPath}`;
};

/**
 * Hook: 使用带语言前缀的导航
 */
export const useLocalizedNavigate = () => {
  const navigate = useNavigate();
  const currentLang = useCurrentLanguage();
  
  return (path: string, options?: { replace?: boolean; state?: any }) => {
    const localizedPath = createLocalizedPath(path, currentLang);
    navigate(localizedPath, options);
  };
};
