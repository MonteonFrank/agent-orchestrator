import { QueryClient } from '@tanstack/react-query'
import { afterEach, expect, it, vi } from 'vitest'
import { approvalMutationOptions, orchestratorKeys, runQueryOptions } from './useOrchestrator'
import type { Run } from '../types/orchestrator'

afterEach(() => vi.unstubAllGlobals())

it('polls active runs every two seconds and stops for every other status', () => {
  const client = new QueryClient()
  const options = runQueryOptions('run_test')
  const query = client.getQueryCache().build<Run, Error, Run, ReturnType<typeof orchestratorKeys.run>>(
    client, { queryKey: orchestratorKeys.run('run_test') },
  )
  const interval = options.refetchInterval
  if (typeof interval !== 'function') throw new Error('Expected conditional polling')
  expect(interval(query)).toBe(false)
  for (const status of ['queued', 'running', 'awaiting_approval', 'completed', 'failed', 'rejected'] as const) {
    query.setData({ status } as Run)
    expect(interval(query)).toBe(['queued', 'running'].includes(status) ? 2000 : false)
  }
  client.clear()
})

it('disables queries without a selected run', () => {
  expect(runQueryOptions(null).enabled).toBe(false)
  expect(runQueryOptions(undefined).enabled).toBe(false)
  expect(runQueryOptions('').enabled).toBe(false)
})

it('invalidates run, diff, and list caches after approval', async () => {
  const client = new QueryClient()
  client.setQueryData(orchestratorKeys.run('run_test'), { status: 'awaiting_approval' })
  client.setQueryData(orchestratorKeys.diffs('run_test'), { diffs: [] })
  client.setQueryData(orchestratorKeys.runs, [])
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{"status":"completed"}')))
  const mutation = client.getMutationCache().build(client, approvalMutationOptions(client))
  await mutation.execute({ runId: 'run_test', checkpointId: 'chk_test', payload: { decision: 'approved' } })
  for (const key of [orchestratorKeys.run('run_test'), orchestratorKeys.diffs('run_test'), orchestratorKeys.runs]) {
    expect(client.getQueryState(key)?.isInvalidated).toBe(true)
  }
  client.clear()
})