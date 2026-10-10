import type {
  ApprovalRequest,
  CreateRunRequest,
  Run,
  RunDiffsResponse,
} from '../types/orchestrator'

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, options)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null
    throw new ApiError(
      response.status,
      typeof detail === 'string' ? detail : `Request failed (${response.status})`,
    )
  }
  return response.json() as Promise<T>
}

function runPath(runId: string): string {
  return `/v1/runs/${encodeURIComponent(runId)}`
}

export function fetchRuns(): Promise<Run[]> {
  return request<Run[]>('/v1/runs')
}

export function fetchRun(runId: string): Promise<Run> {
  return request<Run>(runPath(runId))
}

export function fetchRunDiffs(runId: string): Promise<RunDiffsResponse> {
  return request<RunDiffsResponse>(`${runPath(runId)}/diffs`)
}

export function createRun(payload: CreateRunRequest): Promise<Run> {
  return request<Run>('/v1/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export function submitApproval(
  runId: string,
  checkpointId: string,
  payload: ApprovalRequest,
): Promise<Run> {
  return request<Run>(`${runPath(runId)}/approvals/${encodeURIComponent(checkpointId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}