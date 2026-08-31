/**
 * Stream 断开后：若任务仍 running/queued 则重连 getMessageStream + parseStream。
 * 供 handleSendMessage / resume / clarify 共用，保证 onError 与 onComplete 行为一致。
 */

import { api } from "@/services/api";
import { parseStream } from "@/utils/streamParser";
import { TaskStatus } from "@/types/api";
import type { StreamEvent } from "@/types/api";

const RECONNECT_DELAY_MS = 800;

export interface TryReconnectStreamParams {
  runId: string;
  getCount: () => number;
  incCount: () => void;
  maxReconnect: number;
  onStreamEvent: (event: StreamEvent) => void | Promise<void>;
  onStreamError: (error: Error) => void | Promise<void>;
  onStreamComplete: () => void | Promise<void>;
  logLabel?: string;
}

/**
 * 查任务状态，若仍 RUNNING/QUEUED 且未超重连次数则 800ms 后重连 stream 并解析。
 * @returns true 表示已重连并开始 parseStream，false 表示未重连（调用方应设 generating false 等）
 */
export async function tryReconnectStream(params: TryReconnectStreamParams): Promise<boolean> {
  const {
    runId,
    getCount,
    incCount,
    maxReconnect,
    onStreamEvent,
    onStreamError,
    onStreamComplete,
    logLabel = "Stream",
  } = params;

  try {
    const statusRes = await api.agent.getTaskStatus(runId);
    if (statusRes.code !== 0) {
      console.warn(`🔄 ${logLabel}: getTaskStatus code !== 0, skip reconnect`, statusRes);
      return false;
    }
    const { status, is_terminal } = statusRes.data;
    const statusLower = typeof status === 'string' ? status.toLowerCase() : status;
    const isRunningOrQueued = statusLower === TaskStatus.RUNNING || statusLower === TaskStatus.QUEUED || statusLower === TaskStatus.RESUME_QUEUED;
    if (is_terminal || !isRunningOrQueued) {
      console.log(`🔄 ${logLabel}: skip reconnect (is_terminal=${is_terminal}, status=${status})`);
      return false;
    }
    if (getCount() >= maxReconnect) {
      console.warn(`🔄 ${logLabel}: max reconnects (${maxReconnect}) reached`);
      return false;
    }

    incCount();
    console.log(`🔄 ${logLabel}: task still running, reconnecting in ${RECONNECT_DELAY_MS}ms #`, getCount());
    await new Promise((r) => setTimeout(r, RECONNECT_DELAY_MS));

    const stream = await api.agent.getMessageStream(runId);
    await parseStream(stream, onStreamEvent, onStreamError, onStreamComplete);
    return true;
  } catch (e) {
    console.error(`${logLabel} getTaskStatus/reconnect error`, e);
    return false;
  }
}
