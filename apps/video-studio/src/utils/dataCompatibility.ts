/**
 * 数据格式兼容性处理工具函数
 * 用于处理新旧数据格式的兼容性问题
 */

import type { CharacterHistory, CharacterVersion } from '../types/api';

/**
 * 处理角色数据的兼容性，优先使用版本数据
 * @param character - 角色数据
 * @returns 处理后的角色数据
 */
export const processCharacterData = (character: CharacterHistory): CharacterHistory => {
  if (!character) return character;
  
  // 获取当前版本的图片URL（兼容性逻辑）
  let currentImageUrl = character.image_url || '';
  let processedVersions = character.versions || [];
  let isLegacyData = false;
  
  // 如果有版本数据且当前版本索引有效
  if (character.versions && character.versions.length > 0) {
    const currentVersionIndex = character.current_version_index || 0;
    if (currentVersionIndex < character.versions.length) {
      const currentVersion = character.versions[currentVersionIndex];
      if (currentVersion.character_image_url) {
        currentImageUrl = currentVersion.character_image_url;
      }
    }
  } else {
    // 🔥 老数据标识：versions为空，标记为老数据，不支持regenerate
    isLegacyData = true;
    currentImageUrl = character.image_url || '';
    processedVersions = []; // 保持空数组，前端据此判断是否显示regenerate功能
  }
  
  return {
    ...character,
    image_url: currentImageUrl,
    versions: processedVersions,
    current_version_index: character.current_version_index || 0,
    isLegacyData // 添加标识字段
  };
};

/**
 * 处理角色数据列表的兼容性
 * @param charactersData - 角色数据响应
 * @returns 处理后的角色数据
 */
export const processCharactersData = (charactersData: any): any => {
  if (!charactersData || !charactersData.characters) return charactersData;
  
  const processedCharacters = charactersData.characters.map((character: CharacterHistory) => 
    processCharacterData(character)
  );
  
  return {
    ...charactersData,
    characters: processedCharacters
  };
};

/**
 * 处理关键帧版本的参考图片，优先使用角色版本数据
 * @param keyframeVersion - 关键帧版本数据
 * @param characters - 角色数据列表
 * @returns 处理后的参考图片URL列表
 */
export const processKeyframeReferenceImages = (
  keyframeVersion: any, 
  characters: CharacterHistory[] = []
): string[] => {
  // 如果有角色版本ID，优先使用角色版本的图片
  if (keyframeVersion.character_version_ids && keyframeVersion.character_version_ids.length > 0) {
    const referenceImages: string[] = [];
    
    // 从角色版本中获取图片URL
    characters.forEach(character => {
      if (character.versions) {
        character.versions.forEach(version => {
          if (keyframeVersion.character_version_ids.includes(version.id)) {
            if (version.character_image_url) {
              referenceImages.push(version.character_image_url);
            }
          }
        });
      }
    });
    
    return referenceImages;
  }
  
  // 兼容老数据：使用reference_image_urls
  return keyframeVersion.reference_image_urls || [];
};

/**
 * 处理角色列表数据的兼容性
 * @param characters - 角色数据列表
 * @returns 处理后的角色数据列表
 */
export const processCharactersList = (characters: CharacterHistory[]): CharacterHistory[] => {
  return characters.map(character => processCharacterData(character));
};

/**
 * 获取角色的当前版本图片URL
 * @param character - 角色数据
 * @returns 当前版本的图片URL
 */
export const getCurrentCharacterImageUrl = (character: CharacterHistory): string => {
  const processedCharacter = processCharacterData(character);
  return processedCharacter.image_url || '';
};
