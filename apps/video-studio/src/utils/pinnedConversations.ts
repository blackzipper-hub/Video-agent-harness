
const PINNED_CONVERSATIONS_KEY = 'pinnedConversations';

export interface PinnedConversationData {
  id: string;
  title: string;
  thread_id: string;
  conversation_id: number;
  pinnedAt: number; // timestamp
}

export function getPinnedConversations(): string[] {
  try {
    const stored = localStorage.getItem(PINNED_CONVERSATIONS_KEY);
    if (!stored) return [];

    const parsed = JSON.parse(stored);

    // Support both old format (string[]) and new format (PinnedConversationData[])
    if (Array.isArray(parsed) && parsed.length > 0) {
      if (typeof parsed[0] === 'string') {
        // Old format - just return the IDs
        return parsed;
      } else {
        // New format - extract IDs
        return parsed.map((item: PinnedConversationData) => item.id);
      }
    }

    return [];
  } catch (error) {
    console.error('Error reading pinned conversations from localStorage:', error);
    return [];
  }
}

export function getPinnedConversationsData(): PinnedConversationData[] {
  try {
    const stored = localStorage.getItem(PINNED_CONVERSATIONS_KEY);
    if (!stored) return [];

    const parsed = JSON.parse(stored);

    // Support both old format (string[]) and new format (PinnedConversationData[])
    if (Array.isArray(parsed) && parsed.length > 0) {
      if (typeof parsed[0] === 'string') {
        // Old format - return empty array (no metadata available)
        return [];
      } else {
        // New format - return the full data
        return parsed;
      }
    }

    return [];
  } catch (error) {
    console.error('Error reading pinned conversations data from localStorage:', error);
    return [];
  }
}

export function isConversationPinned(conversationId: string): boolean {
  const pinnedIds = getPinnedConversations();
  return pinnedIds.includes(conversationId);
}

export function pinConversation(conversationId: string, metadata?: { title: string; thread_id: string; conversation_id: number }): void {
  try {
    const pinnedData = getPinnedConversationsData();

    // Check if already pinned
    if (pinnedData.some(item => item.id === conversationId)) {
      return;
    }

    // If metadata provided, use new format
    if (metadata) {
      const newPinnedItem: PinnedConversationData = {
        id: conversationId,
        title: metadata.title,
        thread_id: metadata.thread_id,
        conversation_id: metadata.conversation_id,
        pinnedAt: Date.now(),
      };

      pinnedData.push(newPinnedItem);
      localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(pinnedData));
    } else {
      // Fallback: if no metadata, just add the ID (old format compatibility)
      const pinnedIds = getPinnedConversations();
      if (!pinnedIds.includes(conversationId)) {
        // Try to preserve existing data format
        if (pinnedData.length > 0) {
          // Already using new format, add a minimal entry
          pinnedData.push({
            id: conversationId,
            title: 'Untitled',
            thread_id: '',
            conversation_id: 0,
            pinnedAt: Date.now(),
          });
          localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(pinnedData));
        } else {
          // Old format
          pinnedIds.push(conversationId);
          localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(pinnedIds));
        }
      }
    }
  } catch (error) {
    console.error('Error pinning conversation:', error);
  }
}

export function unpinConversation(conversationId: string): void {
  try {
    const pinnedData = getPinnedConversationsData();

    if (pinnedData.length > 0) {
      // New format
      const filtered = pinnedData.filter(item => item.id !== conversationId);
      localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(filtered));
    } else {
      // Old format
      const pinnedIds = getPinnedConversations();
      const filtered = pinnedIds.filter(id => id !== conversationId);
      localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(filtered));
    }
  } catch (error) {
    console.error('Error unpinning conversation:', error);
  }
}

export function togglePinConversation(conversationId: string, metadata?: { title: string; thread_id: string; conversation_id: number }): boolean {
  const isPinned = isConversationPinned(conversationId);
  if (isPinned) {
    unpinConversation(conversationId);
    return false;
  } else {
    pinConversation(conversationId, metadata);
    return true;
  }
}

export function removePinnedConversation(conversationId: string): void {
  unpinConversation(conversationId);
}

export function updatePinnedConversationMetadata(conversationId: string, metadata: { title: string; thread_id: string; conversation_id: number }): void {
  try {
    const pinnedData = getPinnedConversationsData();
    const index = pinnedData.findIndex(item => item.id === conversationId);

    if (index !== -1) {
      // Update existing entry
      pinnedData[index] = {
        ...pinnedData[index],
        title: metadata.title,
        thread_id: metadata.thread_id,
        conversation_id: metadata.conversation_id,
      };
      localStorage.setItem(PINNED_CONVERSATIONS_KEY, JSON.stringify(pinnedData));
    }
  } catch (error) {
    console.error('Error updating pinned conversation metadata:', error);
  }
}
