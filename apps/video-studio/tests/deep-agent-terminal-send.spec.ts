import { describe, expect, it } from 'vitest'

import { deepAgentReducer, initialDeepAgentState } from '../src/features/deep-agent-v2/reducer'
import { DeepAgentApiError, isStoppedResumeRequired } from '../src/features/deep-agent-v2/client'

const terminalSnapshot = (status: 'completed' | 'failed' | 'cancelled') => ({
  run: {
    id: 'project-1',
    project_id: 'project-1',
    thread_id: 'thread-1',
    status,
    updated_at: '2026-09-08T00:00:00Z',
  },
  tasks: [],
  artifacts: [],
  selections: [],
  messages: [],
  last_event_sequence: 1,
})

describe('Deep Agent terminal send state', () => {
  it.each(['completed', 'failed', 'cancelled'] as const)(
    'releases a stale sending lock when the hydrated run is %s',
    (status) => {
      const state = {
        ...initialDeepAgentState,
        selectedRunId: 'project-1',
        isSending: true,
      }

      const next = deepAgentReducer(state, {
        type: 'HYDRATE',
        runId: 'project-1',
        snapshot: terminalSnapshot(status) as never,
        messages: [],
        events: [],
      })

      expect(next.snapshot?.run.status).toBe(status)
      expect(next.isSending).toBe(false)
    },
  )

  it('recognizes the authoritative stopped-build conflict for resume fallback', () => {
    expect(isStoppedResumeRequired(new DeepAgentApiError(
      409,
      'Task stopped. Confirm resume before sending a continuation.',
      'task_stopped_resume_required',
    ))).toBe(true)
  })

  it('does not treat unrelated conflicts as a stopped-build resume request', () => {
    expect(isStoppedResumeRequired(new DeepAgentApiError(409, 'Project version conflict'))).toBe(false)
    expect(isStoppedResumeRequired(new DeepAgentApiError(422, 'Task stopped. Confirm resume before sending a continuation.'))).toBe(false)
  })
})
