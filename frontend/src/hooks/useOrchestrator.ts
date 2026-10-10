import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchRun, fetchRunDiffs, fetchRuns, submitApproval } from '../api/client'
import type { ApprovalRequest, Run, RunStatus } from '../types/orchestrator'

export const orchestratorKeys = {
  runs: ['runs'] as const,
  run: (runId: string | null | undefined) => ['run', runId] as const,
  diffs: (runId: string | null | undefined) => ['run', runId, 'diffs'] as const,
}

export function runQueryOptions(runId: string | null | undefined) {
  return queryOptions({
    queryKey: orchestratorKeys.run(runId),
    queryFn: () => fetchRun(runId!),
    enabled: Boolean(runId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'queued' || status === 'running' ? 2000 : false
    },
  })
}

export function useRun(runId: string | null | undefined) {
  return useQuery(runQueryOptions(runId))
}

export function useRuns() {
  return useQuery({
    queryKey: orchestratorKeys.runs,
    queryFn: fetchRuns,
    refetchInterval: 2000,
  })
}

export function useRunDiffs(runId: string | null | undefined) {
  return useQuery({
    queryKey: orchestratorKeys.diffs(runId),
    queryFn: () => fetchRunDiffs(runId!),
    enabled: Boolean(runId),
  })
}

export interface SubmitApprovalVariables {
  runId: string
  checkpointId: string
  payload: ApprovalRequest
}

export function approvalMutationOptions(queryClient: ReturnType<typeof useQueryClient>) {
  return {
    mutationFn: ({ runId, checkpointId, payload }: SubmitApprovalVariables) =>
      submitApproval(runId, checkpointId, payload),
    onMutate: async ({ runId, payload }: SubmitApprovalVariables) => {
      await Promise.all([
        queryClient.cancelQueries({ queryKey: orchestratorKeys.run(runId) }),
        queryClient.cancelQueries({ queryKey: orchestratorKeys.runs }),
      ])
      const previousRun = queryClient.getQueryData<Run>(orchestratorKeys.run(runId))
      const previousRuns = queryClient.getQueryData<Run[]>(orchestratorKeys.runs)
      const status: RunStatus = payload.decision === 'approved' ? 'completed'
        : payload.decision === 'rejected' ? 'rejected' : 'running'
      const update = (run: Run): Run => ({
        ...run, status, pending_checkpoint: null, current_step: null,
      })
      if (previousRun) queryClient.setQueryData(orchestratorKeys.run(runId), update(previousRun))
      if (previousRuns) queryClient.setQueryData(orchestratorKeys.runs,
        previousRuns.map((run) => run.run_id === runId ? update(run) : run))
      return { previousRun, previousRuns }
    },
    onError: (_error: Error, { runId }: SubmitApprovalVariables, context: {
      previousRun: Run | undefined; previousRuns: Run[] | undefined
    } | undefined) => {
      if (context?.previousRun) queryClient.setQueryData(orchestratorKeys.run(runId), context.previousRun)
      if (context?.previousRuns) queryClient.setQueryData(orchestratorKeys.runs, context.previousRuns)
    },
    onSuccess: (updated: Run) => {
      queryClient.setQueryData(orchestratorKeys.run(updated.run_id), updated)
      queryClient.setQueryData<Run[]>(orchestratorKeys.runs,
        (runs) => runs?.map((run) => run.run_id === updated.run_id ? updated : run))
    },
    onSettled: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['run'] }),
        queryClient.invalidateQueries({ queryKey: orchestratorKeys.runs }),
      ])
    },
  }
}

export function useSubmitApproval() {
  const queryClient = useQueryClient()
  return useMutation(approvalMutationOptions(queryClient))
}