import { QueryClient } from '@tanstack/react-query'
import { expect, it } from 'vitest'
import { approvalMutationOptions, orchestratorKeys } from './useOrchestrator'
import type { Run } from '../types/orchestrator'

it('optimistically updates run/list caches and restores them on failure', async () => {
  const client = new QueryClient()
  const run = { run_id: 'run_test', status: 'awaiting_approval', pending_checkpoint: { checkpoint_id: 'chk_test' } } as Run
  client.setQueryData(orchestratorKeys.run(run.run_id), run)
  client.setQueryData(orchestratorKeys.runs, [run])
  const options = approvalMutationOptions(client)
  const variables = { runId: run.run_id, checkpointId: 'chk_test', payload: { decision: 'approved' as const } }
  const snapshot = await options.onMutate(variables)
  expect(client.getQueryData<Run>(orchestratorKeys.run(run.run_id))?.status).toBe('completed')
  expect(client.getQueryData<Run[]>(orchestratorKeys.runs)?.[0].pending_checkpoint).toBeNull()
  options.onError(new Error('Request failed'), variables, snapshot)
  expect(client.getQueryData(orchestratorKeys.run(run.run_id))).toEqual(run)
  expect(client.getQueryData(orchestratorKeys.runs)).toEqual([run])
  client.clear()
})