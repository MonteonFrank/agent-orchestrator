import { AlertCircle, CheckCircle, Circle, LoaderCircle } from 'lucide-react'
import { cn } from '../lib/utils'
import type { RunStatus, StepState } from '../types/orchestrator'

export interface PipelineTimelineProps {
  stateHistory: StepState[]
  status: RunStatus
}

function StatusIcon({ status }: { status: string }) {
  if (status === 'running') return <LoaderCircle className="h-5 w-5 animate-spin motion-reduce:animate-none text-sky-300" aria-label="Running" />
  if (status === 'completed') return <CheckCircle className="h-5 w-5 text-emerald-400" aria-label="Completed" />
  if (['failed', 'awaiting_approval', 'rejected'].includes(status)) return <AlertCircle className={cn('h-5 w-5', status === 'awaiting_approval' ? 'text-amber-300' : 'text-red-400')} aria-label={status === 'awaiting_approval' ? 'Checkpoint' : status === 'rejected' ? 'Rejected' : 'Failed'} />
  return <Circle className="h-5 w-5 text-zinc-500" aria-label="Pending" />
}

export function PipelineTimeline({ stateHistory, status }: PipelineTimelineProps) {
  const marker = status === 'awaiting_approval' ? 'Approval checkpoint'
    : status === 'running' && !stateHistory.some((step) => step.status === 'running') ? 'Agent execution in progress'
    : status === 'queued' ? 'Queued for execution'
    : status === 'failed' && !stateHistory.some((step) => step.status === 'failed') ? 'Pipeline failed'
    : status === 'rejected' ? 'Run rejected' : null

  return (
    <ol aria-label="Pipeline timeline" className="space-y-4 text-zinc-100">
      {stateHistory.map((step, index) => (
        <li key={`${step.step_index}-${index}`} className="relative pl-9">
          <span className="absolute left-0 top-4"><StatusIcon status={step.status} /></span>
          {(index < stateHistory.length - 1 || marker) && <span aria-hidden="true" className="absolute bottom-[-1rem] left-[9px] top-10 w-px bg-zinc-300" />}
          <article className="min-w-0 rounded-md border border-zinc-700 bg-zinc-900 p-4 shadow-sm">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h3 className="text-sm font-semibold">{step.agent_name}</h3>
              <span className="text-xs text-zinc-400">{step.status.replaceAll('_', ' ')}</span>
            </div>
            <p className="mt-3 whitespace-pre-wrap break-words text-sm leading-6 text-zinc-300">{step.output_summary || 'No output recorded.'}</p>
          </article>
        </li>
      ))}
      {marker && <li className="relative pl-9">
        <span className="absolute left-0 top-1"><StatusIcon status={status} /></span>
        <p className={cn('py-1 text-sm font-medium', status === 'awaiting_approval' ? 'text-amber-700' : status === 'failed' || status === 'rejected' ? 'text-red-700' : 'text-zinc-600')}>{marker}</p>
      </li>}
      {!stateHistory.length && !marker && <li className="text-sm text-zinc-500">No agent steps recorded.</li>}
    </ol>
  )
}

export default PipelineTimeline