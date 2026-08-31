import { Navigate, useLocation } from 'react-router-dom';

/**
 * 根路径重定向组件
 * 将根路径重定向到默认语言版本
 */
export const LanguageRedirect = () => {
  const location = useLocation();
  const defaultLang = localStorage.getItem('language') || 'en';
  const hash = location.hash || '';
  const search = location.search || '';
  
  // 如果 hash 中已经有路径，保留它
  let hashPath = hash.replace('#', '') || '/';
  
  // 如果 hashPath 已经包含重复的语言前缀（如 /zh/zh/create），移除重复的部分
  hashPath = hashPath.replace(/^\/(en|zh)\/(en|zh)\//, '/$1/');
  hashPath = hashPath.replace(/^\/(en|zh)\/(en|zh)$/, '/$1');
  
  // 如果 hashPath 已经包含语言前缀，直接使用
  if (hashPath.startsWith('/en/') || hashPath.startsWith('/zh/') || hashPath === '/en' || hashPath === '/zh') {
    return <Navigate to={hashPath + search} replace />;
  }
  
  // 否则添加语言前缀
  const newPath = `/${defaultLang}${hashPath === '/' ? '/' : hashPath}${search}`;
  return <Navigate to={newPath} replace />;
};
