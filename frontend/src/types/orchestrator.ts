export type TaskType = 'code' | 'review_docs' | 'code_and_review'

export type RunStatus =
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'completed'
  | 'failed'
  | 'rejected'

export interface FileDiff {
  path: string
  status: 'modified' | 'added' | 'deleted'
  patch: string
}

export interface StepState {
  step_index: number
  agent_name: string
  status: string
  output_summary: string
  created_at: string
}

export interface Checkpoint {
  checkpoint_id: string
  description: string
  requires_action: 'approve_reject' | 'edit_prompt'
}

export interface Run {
  run_id: string
  status: RunStatus
  current_step: string | null
  state_history: StepState[]
  pending_checkpoint: Checkpoint | null
  diffs: FileDiff[]
  context_diffs: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ApprovalRequest {
  decision: 'approved' | 'rejected' | 'request_changes'
  feedback?: string | null
}

export interface CreateRunRequest {
  prompt: string
  pipeline_mode?: TaskType
  repository_ref?: string | null
  target_files?: string[]
}

export interface RunDiffsResponse {
  run_id: string
  diffs: FileDiff[]
  context_diffs: Record<string, unknown>
}