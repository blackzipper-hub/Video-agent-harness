/**
 * 流式响应解析器
 * 处理 Server-Sent Events (SSE) 格式的流式数据
 */

import type { StreamEvent } from '../types/api';

/**
 * 解析流式响应
 * @param stream ReadableStream from fetch response
 * @param onEvent 事件回调函数
 * @param onError 错误回调函数
 * @param onComplete 完成回调函数
 */
export async function parseStream(
  stream: ReadableStream<Uint8Array>,
  onEvent: (event: StreamEvent) => void | Promise<void>,
  onError?: (error: Error) => void | Promise<void>,
  onComplete?: () => void | Promise<void>
) {
  console.log('🔵 [streamParser] ===== parseStream STARTED =====');
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let chunkCount = 0;
  let eventCount = 0;
  
  try {
    while (true) {
      const { done, value } = await reader.read();
      
      if (done) {
        console.log('🔵 [streamParser] Stream done. Total chunks:', chunkCount, 'Total events:', eventCount);
        // 处理缓冲区中剩余的数据
        if (buffer.trim()) {
          console.log('🔵 [streamParser] Processing remaining buffer:', buffer.substring(0, 100));
          await processBuffer(buffer, onEvent);
        }
        await onComplete?.();
        break;
      }
      
      chunkCount++;
      const chunk = decoder.decode(value, { stream: true });
      console.log(`🔵 [streamParser] Chunk ${chunkCount} received (${chunk.length} bytes):`, chunk.substring(0, 200));
      
      // 解码数据并添加到缓冲区
      buffer += chunk;
      
      // 按行处理数据
      const lines = buffer.split('\n');
      buffer = lines.pop() || ''; // 保留最后一行（可能不完整）
      
      console.log(`🔵 [streamParser] Processing ${lines.length} lines`);
      for (const line of lines) {
        if (line.trim()) {
          eventCount++;
          await processLine(line, onEvent);
        }
      }
    }
  } catch (error) {
    console.error('🔵 [streamParser] ❌ Stream parsing error:', error);
    await onError?.(error as Error);
  } finally {
    console.log('🔵 [streamParser] ===== parseStream ENDED =====');
    reader.releaseLock();
  }
}

/**
 * 处理单行数据
 */
async function processLine(line: string, onEvent: (event: StreamEvent) => void | Promise<void>) {
  try {
    console.log('🟡 [processLine] Raw line:', line.substring(0, 200));
    
    // SSE 格式: data: {...}
    if (line.startsWith('data: ')) {
      const jsonStr = line.substring(6); // 移除 'data: ' 前缀
      console.log('🟡 [processLine] JSON string:', jsonStr.substring(0, 200));
      
      // 跳过心跳消息
      if (jsonStr === '[DONE]' || jsonStr.trim() === '') {
        console.log('🟡 [processLine] Skipping heartbeat/empty message');
        return;
      }
      
      const event: StreamEvent = JSON.parse(jsonStr);
      console.log('📨 [streamParser] ✅ Parsed event:', {
        type: event.type,
        message: event.message,
        conversation_id: event.conversation_id,
        thread_id: event.thread_id,
        run_id: event.run_id,
        hidden: event.hidden,
        completed: event.completed,
        total: event.total
      });
      
      if (event.type === 'video_generation_progress') {
        console.log('🎬 [streamParser] This is a PROGRESS event!', {
          completed: event.completed,
          total: event.total,
          progress_percent: event.progress_percent
        });
      }
      
      await onEvent(event);
    } else {
      console.log('🟡 [processLine] ⚠️ Line does not start with "data: "');
    }
  } catch (error) {
    console.error('🟡 [processLine] ❌ Failed to parse line:', line.substring(0, 100), error);
  }
}

/**
 * 处理缓冲区数据
 */
async function processBuffer(buffer: string, onEvent: (event: StreamEvent) => void | Promise<void>) {
  const lines = buffer.split('\n');
  for (const line of lines) {
    if (line.trim()) {
      await processLine(line, onEvent);
    }
  }
}

/**
 * 流式事件类型枚举
 */
export const StreamEventType = {
  USER_INPUT: 'user_input',
  CONVERSATION_START: 'conversation_start',
  ANALYSIS_START: 'video_analysis_start',
  ANALYSIS_COMPLETE: 'video_analysis_complete',
  STORY_OUTLINE_START: 'story_outline_start',
  STORY_OUTLINE_COMPLETE: 'story_outline_complete',
  SCENE_GENERATION_START: 'scene_generation_start',
  SCENE_GENERATION_COMPLETE: 'scene_generation_complete',
  SHOT_GENERATION_START: 'shot_generation_start',
  SHOT_GENERATION_COMPLETE: 'shot_generation_complete',
  KEYFRAME_START: 'keyframe_generation_start',
  KEYFRAME_GENERATED: 'keyframe_generated',
  KEYFRAME_COMPLETE: 'keyframe_generation_complete',
  VIDEO_START: 'video_generation_start',
  VIDEO_GENERATED: 'video_generated',
  VIDEO_COMPLETE: 'video_generation_complete',
  VIDEO_PROGRESS: 'video_generation_progress',
  NARRATION_START: 'narration_generation_start',
  NARRATION_COMPLETE: 'narration_generation_complete',
  MUSIC_START: 'music_generation_start',
  MUSIC_COMPLETE: 'music_generation_complete',
  ASSEMBLY_START: 'video_assembly_start',
  ASSEMBLY_COMPLETE: 'video_assembly_complete',
  FINAL_VIDEO: 'final_video',
  ERROR: 'error',
  COMPLETE: 'complete',
} as const;

/**
 * 判断是否为错误事件
 */
export function isErrorEvent(event: StreamEvent): boolean {
  return event.type === StreamEventType.ERROR;
}

/**
 * 判断是否为完成事件
 */
export function isCompleteEvent(event: StreamEvent): boolean {
  return event.type === StreamEventType.COMPLETE;
}

/**
 * 提取事件中的消息文本
 */
export function extractEventMessage(event: StreamEvent): string {
  return event.message || event.type;
}

