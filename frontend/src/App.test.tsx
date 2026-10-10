// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import App from './App'
import type { Run } from './types/orchestrator'

const mocks = vi.hoisted(() => ({ mutate: vi.fn(), reset: vi.fn(), create: vi.fn(), refetch: vi.fn() }))
vi.mock('./api/client', () => ({ createRun: mocks.create }))
vi.mock('./hooks/useOrchestrator', async (original) => ({
  ...await original<typeof import('./hooks/useOrchestrator')>(),
  useRuns: () => ({ data: [run], isLoading: false, error: null, refetch: mocks.refetch }),
  useRun: () => ({ data: run, dataUpdatedAt: 2, error: null }),
  useRunDiffs: () => ({ data: { diffs: [] }, dataUpdatedAt: 1, error: null, refetch: mocks.refetch }),
  useSubmitApproval: () => ({ mutate: mocks.mutate, reset: mocks.reset, isPending: false, error: null }),
}))

const run: Run = {
  run_id: 'run_test', status: 'awaiting_approval', current_step: null,
  state_history: [{ step_index: 0, agent_name: 'Coder', status: 'completed', output_summary: 'Added auth.', created_at: '2026-10-05T12:00:00Z' }],
  pending_checkpoint: { checkpoint_id: 'chk_test', description: 'Review auth changes.', requires_action: 'approve_reject' },
  diffs: [{ path: 'auth.py', status: 'added', patch: '+auth\n' }], context_diffs: {},
  created_at: '2026-10-05T12:00:00Z', updated_at: '2026-10-05T12:00:00Z',
}

beforeEach(() => {
  vi.clearAllMocks()
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open') }
})
afterEach(cleanup)

function dashboard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<StrictMode><QueryClientProvider client={client}><App /></QueryClientProvider></StrictMode>)
  return client
}

it('shows recent runs, checkpoint actions, and navigable diff tabs', () => {
  const client = dashboard()
  expect(screen.getByRole('navigation', { name: 'Recent runs' })).toBeTruthy()
  expect(screen.getByRole('region', { name: 'Approval checkpoint' })).toBeTruthy()
  fireEvent.click(screen.getByRole('tab', { name: 'Diff Viewer' }))
  expect(screen.getByLabelText('Unified patch').textContent).toContain('+auth')
  expect(mocks.refetch).toHaveBeenCalled()
  fireEvent.keyDown(screen.getByRole('tab', { name: 'Diff Viewer' }), { key: 'ArrowLeft' })
  expect(screen.getByRole('tab', { name: 'Pipeline Steps' }).getAttribute('aria-selected')).toBe('true')
  client.clear()
})

it.each([['Approve & Commit', 'approved'], ['Reject', 'rejected']])('submits %s for the active checkpoint', (label, decision) => {
  const client = dashboard()
  fireEvent.click(screen.getByRole('button', { name: label }))
  expect(mocks.mutate).toHaveBeenCalledWith({ runId: 'run_test', checkpointId: 'chk_test', payload: { decision, feedback: null } }, expect.any(Object))
  client.clear()
})

it('requires nonblank feedback before requesting changes', () => {
  const client = dashboard()
  const button = screen.getByRole('button', { name: 'Request Changes' }) as HTMLButtonElement
  expect(button.disabled).toBe(true)
  fireEvent.change(screen.getByLabelText('Feedback for requested changes'), { target: { value: '  Add tests.  ' } })
  fireEvent.click(button)
  expect(mocks.mutate).toHaveBeenCalledWith({ runId: 'run_test', checkpointId: 'chk_test', payload: { decision: 'request_changes', feedback: 'Add tests.' } }, expect.any(Object))
  client.clear()
})

it('opens the new-run modal and posts prompt and mode', async () => {
  mocks.create.mockResolvedValue({ ...run, run_id: 'run_new', status: 'queued', pending_checkpoint: null })
  const client = dashboard()
  fireEvent.click(screen.getByRole('button', { name: 'New Run' }))
  expect(screen.getByRole('dialog', { name: 'New Run' })).toBeTruthy()
  expect(document.activeElement).toBe(screen.getByLabelText('Prompt'))
  fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Document auth.' } })
  fireEvent.change(screen.getByLabelText('Pipeline Mode'), { target: { value: 'review_docs' } })
  fireEvent.click(screen.getByRole('button', { name: 'Create Run' }))
  await waitFor(() => expect(mocks.create).toHaveBeenCalled())
  expect(mocks.create.mock.calls[0][0]).toEqual({ prompt: 'Document auth.', pipeline_mode: 'review_docs', target_files: [] })
  await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  client.clear()
})