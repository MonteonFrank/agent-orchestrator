import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AlertCircle, Check, FileDiff as DiffIcon, GitBranch, GitCommitHorizontal, LoaderCircle, MessageSquare, Plus, RefreshCw, Workflow, X } from 'lucide-react'
import { createRun } from './api/client'
import { DiffViewer } from './components/DiffViewer'
import { PipelineTimeline } from './components/PipelineTimeline'
import { orchestratorKeys, useRun, useRunDiffs, useRuns, useSubmitApproval } from './hooks/useOrchestrator'
import { cn } from './lib/utils'
import type { ApprovalRequest, Run, RunStatus, TaskType } from './types/orchestrator'

const statusColors: Record<RunStatus, string> = {
  completed: 'bg-emerald-100 text-emerald-800',
  awaiting_approval: 'bg-amber-100 text-amber-800',
  running: 'bg-sky-100 text-sky-800',
  queued: 'bg-zinc-100 text-zinc-600',
  failed: 'bg-red-100 text-red-800',
  rejected: 'bg-red-100 text-red-800',
}

function StatusBadge({ status }: { status: RunStatus }) {
  return <span className={cn('inline-flex items-center gap-1.5 rounded px-2 py-1 text-xs font-medium', statusColors[status])}>
    <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full bg-current" />
    {status.replaceAll('_', ' ')}
  </span>
}

function NewRunDialog({ onClose, onCreated }: { onClose: () => void; onCreated: (run: Run) => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [prompt, setPrompt] = useState('')
  const [mode, setMode] = useState<TaskType>('code_and_review')
  const [files, setFiles] = useState('')
  const mutation = useMutation({ mutationFn: createRun, onSuccess: onCreated })

  useEffect(() => {
    const dialog = dialogRef.current
    dialog?.showModal()
    dialog?.querySelector<HTMLTextAreaElement>('textarea')?.focus()
    return () => dialog?.close()
  }, [])

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!prompt.trim() || mutation.isPending) return
    mutation.mutate({ prompt, pipeline_mode: mode, target_files: files.split('\n').map((file) => file.trim()).filter(Boolean) })
  }

  return <dialog ref={dialogRef} aria-labelledby="new-run-title" onCancel={onClose} className="m-auto w-[calc(100%-2rem)] max-w-lg rounded-md border border-zinc-200 bg-white p-0 text-zinc-800 shadow-xl backdrop:bg-zinc-950/50">
    <div className="flex items-center justify-between border-b border-zinc-200 px-6 py-4">
      <h2 id="new-run-title" className="text-lg font-semibold">New Run</h2>
      <button type="button" onClick={onClose} aria-label="Close new run" title="Close" className="rounded p-2 text-zinc-500 hover:bg-zinc-100"><X size={18} /></button>
    </div>
    <form onSubmit={submit} className="space-y-5 p-6">
      <div><label htmlFor="new-prompt" className="mb-2 block text-sm font-medium">Prompt</label>
        <textarea id="new-prompt" autoFocus required minLength={1} maxLength={4000} rows={5} value={prompt} onChange={(event) => setPrompt(event.target.value)} className="w-full resize-y rounded border border-zinc-300 p-3" /></div>
      <div><label htmlFor="new-mode" className="mb-2 block text-sm font-medium">Pipeline Mode</label>
        <select id="new-mode" value={mode} onChange={(event) => setMode(event.target.value as TaskType)} className="w-full rounded border border-zinc-300 bg-white p-3">
          <option value="code_and_review">Code and documentation review</option><option value="code">Code</option><option value="review_docs">Documentation review</option>
        </select></div>
      <div><label htmlFor="new-files" className="mb-2 block text-sm font-medium">Target Files</label>
        <textarea id="new-files" rows={2} value={files} onChange={(event) => setFiles(event.target.value)} placeholder="app/main.py" className="w-full resize-y rounded border border-zinc-300 p-3 font-mono text-sm" /></div>
      {mutation.error && <p role="alert" className="text-sm text-red-700">{mutation.error.message}</p>}
      <div className="flex justify-end gap-3"><button type="button" onClick={onClose} className="rounded px-4 py-2 text-sm hover:bg-zinc-100">Cancel</button>
        <button type="submit" disabled={!prompt.trim() || mutation.isPending} className="inline-flex items-center gap-2 rounded bg-emerald-700 px-4 py-2 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50">{mutation.isPending ? <LoaderCircle size={16} className="animate-spin" /> : <Plus size={16} />}Create Run</button></div>
    </form>
  </dialog>
}

export default function App() {
  const queryClient = useQueryClient()
  const runs = useRuns()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const runId = selectedId ?? runs.data?.[0]?.run_id ?? null
  const runQuery = useRun(runId)
  const diffs = useRunDiffs(runId)
  const run = runQuery.data ?? runs.data?.find((item) => item.run_id === runId)
  const [tab, setTab] = useState<'pipeline' | 'diffs'>('pipeline')
  const [modalOpen, setModalOpen] = useState(false)
  const [feedback, setFeedback] = useState('')
  const approval = useSubmitApproval()
  const checkpoint = run?.status === 'awaiting_approval' ? run.pending_checkpoint : null
  const error = approval.error ?? runQuery.error

  function selectRun(id: string) {
    setSelectedId(id)
    setFeedback('')
    approval.reset()
  }

  function decide(decision: ApprovalRequest['decision']) {
    if (!run || !checkpoint || approval.isPending) return
    if (decision === 'request_changes' && !feedback.trim()) return
    approval.mutate({ runId: run.run_id, checkpointId: checkpoint.checkpoint_id, payload: { decision, feedback: decision === 'request_changes' ? feedback.trim() : null } }, {
      onSuccess: () => setFeedback(''),
    })
  }

  function tabKeys(event: KeyboardEvent<HTMLButtonElement>) {
    if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      event.preventDefault()
      const next = event.key === 'Home' ? 'pipeline' : event.key === 'End' ? 'diffs' : tab === 'pipeline' ? 'diffs' : 'pipeline'
      setTab(next)
      document.getElementById(`tab-${next}`)?.focus()
    }
  }

  return <div className="min-h-screen bg-zinc-50">
    <header className="border-b border-zinc-200 bg-white px-5 py-4 sm:px-8">
      <div className="flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3"><Workflow className="h-6 w-6 shrink-0 text-emerald-700" aria-hidden="true" /><h1 className="text-base font-semibold sm:text-lg">Agent Orchestrator</h1></div>
        <button type="button" onClick={() => setModalOpen(true)} className="inline-flex shrink-0 items-center gap-2 rounded bg-emerald-700 px-3 py-2 text-sm font-medium text-white hover:bg-emerald-800"><Plus size={16} />New Run</button>
      </div>
    </header>
    <div className="grid min-h-[calc(100vh-73px)] md:grid-cols-[260px_minmax(0,1fr)]">
      <aside className="min-w-0 border-b border-zinc-200 bg-white md:border-b-0 md:border-r">
        <div className="flex items-center justify-between px-5 py-5"><h2 className="text-xs font-semibold uppercase text-zinc-500">Recent Runs</h2><button type="button" onClick={() => void runs.refetch()} disabled={runs.isFetching} aria-label="Refresh runs" title="Refresh runs" className="rounded p-1.5 text-zinc-500 hover:bg-zinc-100 disabled:opacity-50"><RefreshCw size={15} className={cn(runs.isFetching && 'animate-spin')} /></button></div>
        {runs.isLoading && <p className="px-5 pb-5 text-sm text-zinc-500" role="status">Loading runs...</p>}
        {runs.error && <p className="px-5 pb-5 text-sm text-red-700" role="alert">{runs.error.message}</p>}
        {runs.data?.length === 0 && <p className="px-5 pb-5 text-sm text-zinc-500">No runs yet.</p>}
        <nav aria-label="Recent runs" className="flex max-h-60 gap-2 overflow-auto px-3 pb-4 md:block md:max-h-[calc(100vh-150px)] md:space-y-1">
          {runs.data?.map((item) => <button type="button" key={item.run_id} onClick={() => selectRun(item.run_id)} aria-current={item.run_id === runId ? 'true' : undefined} className={cn('min-w-[200px] rounded border px-3 py-3 text-left md:w-full', item.run_id === runId ? 'border-emerald-200 bg-emerald-50' : 'border-transparent hover:bg-zinc-50')}>
            <span className="mb-2 block break-all font-mono text-xs text-zinc-700">{item.run_id}</span><StatusBadge status={item.status} />
            <span className="mt-2 block text-xs text-zinc-400">{new Date(item.created_at).toLocaleString()}</span>
          </button>)}
        </nav>
      </aside>
      <main className="min-w-0 px-5 py-6 sm:px-8">
        {!run ? <div className="flex min-h-64 flex-col items-center justify-center gap-3 text-zinc-500"><Workflow size={32} aria-hidden="true" /><h2 className="text-lg font-medium text-zinc-700">{runQuery.isLoading ? 'Loading run...' : 'No run selected'}</h2>{runQuery.error && <p role="alert" className="text-sm text-red-700">{runQuery.error.message}</p>}</div> : <>
          <div className="mb-6 flex flex-wrap items-start justify-between gap-3"><div className="min-w-0"><h2 className="text-xl font-semibold">Run Overview</h2><p className="mt-1 break-all font-mono text-xs text-zinc-500">{run.run_id}</p></div><div className="flex items-center gap-3"><span className="flex items-center gap-1 text-xs text-zinc-500"><GitBranch size={14} />main</span><StatusBadge status={run.status} /></div></div>
          {checkpoint && <section aria-label="Approval checkpoint" className="mb-6 border-y border-amber-300 bg-amber-50 px-4 py-5">
            <div className="flex items-start gap-3"><AlertCircle size={20} className="mt-0.5 shrink-0 text-amber-700" aria-hidden="true" /><div><h3 className="text-sm font-semibold text-amber-900">Awaiting Approval</h3><p className="mt-1 text-sm text-amber-800">{checkpoint.description}</p></div></div>
            <div className="mt-4 flex flex-wrap items-start gap-3">
              <button type="button" onClick={() => decide('approved')} disabled={approval.isPending} title="Approve proposals; this mock backend does not create a Git commit" className="inline-flex h-10 items-center gap-2 rounded bg-emerald-700 px-3 text-sm font-medium text-white hover:bg-emerald-800 disabled:opacity-50"><GitCommitHorizontal size={16} />Approve &amp; Commit</button>
              <form onSubmit={(event) => { event.preventDefault(); decide('request_changes') }} className="flex min-w-0 flex-1 flex-wrap gap-2 sm:min-w-[300px]">
                <label htmlFor="feedback" className="sr-only">Feedback for requested changes</label><input id="feedback" value={feedback} onChange={(event) => setFeedback(event.target.value)} placeholder="Requested changes" required disabled={approval.isPending} className="h-10 min-w-0 flex-1 rounded border border-amber-300 bg-white px-3 text-sm" />
                <button type="submit" disabled={approval.isPending || !feedback.trim()} className="inline-flex h-10 items-center gap-2 rounded bg-sky-700 px-3 text-sm font-medium text-white hover:bg-sky-800 disabled:opacity-50"><MessageSquare size={16} />Request Changes</button>
              </form>
              <button type="button" onClick={() => decide('rejected')} disabled={approval.isPending} className="inline-flex h-10 items-center gap-2 rounded bg-red-700 px-3 text-sm font-medium text-white hover:bg-red-800 disabled:opacity-50"><X size={16} />Reject</button>
            </div>
          </section>}
          {approval.isPending && <p role="status" className="mb-4 flex items-center gap-2 text-sm text-zinc-600"><LoaderCircle size={16} className="animate-spin" />Submitting decision...</p>}
          {approval.isSuccess && !approval.isPending && <p role="status" className="mb-4 flex items-center gap-2 text-sm text-emerald-700"><Check size={16} />Decision submitted.</p>}
          {error && <p role="alert" className="mb-4 text-sm text-red-700">{error.message}</p>}
          <div role="tablist" aria-label="Run views" className="mb-6 flex gap-6 border-b border-zinc-200">
            {(['pipeline', 'diffs'] as const).map((item) => <button type="button" key={item} id={`tab-${item}`} role="tab" aria-selected={tab === item} aria-controls={`panel-${item}`} tabIndex={tab === item ? 0 : -1} onKeyDown={tabKeys} onClick={() => { setTab(item); if (item === 'diffs') void diffs.refetch() }} className={cn('flex items-center gap-2 border-b-2 py-3 text-sm font-medium', tab === item ? 'border-emerald-700 text-emerald-800' : 'border-transparent text-zinc-500 hover:text-zinc-800')}>{item === 'pipeline' ? <Workflow size={16} /> : <DiffIcon size={16} />}{item === 'pipeline' ? 'Pipeline Steps' : 'Diff Viewer'}</button>)}
          </div>
          <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} tabIndex={0} className="min-w-0">
            {tab === 'pipeline' ? <div className="max-w-3xl"><PipelineTimeline stateHistory={run.state_history} status={run.status} /></div> : <>{diffs.error && <p role="alert" className="mb-4 text-sm text-red-700">{diffs.error.message}</p>}<DiffViewer diffs={diffs.data && diffs.dataUpdatedAt >= runQuery.dataUpdatedAt ? diffs.data.diffs : run.diffs} /></>}
          </section>
        </>}
      </main>
    </div>
    {modalOpen && <NewRunDialog onClose={() => setModalOpen(false)} onCreated={(created) => {
      queryClient.setQueryData(orchestratorKeys.run(created.run_id), created)
      queryClient.setQueryData<Run[]>(orchestratorKeys.runs, (existing) => [created, ...(existing ?? [])])
      selectRun(created.run_id)
      setTab('pipeline')
      setModalOpen(false)
      void queryClient.invalidateQueries({ queryKey: orchestratorKeys.runs })
    }} />}
  </div>
}