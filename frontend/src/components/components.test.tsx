// @vitest-environment jsdom
import { afterEach, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { PipelineTimeline } from './PipelineTimeline'
import { DiffViewer } from './DiffViewer'
import type { FileDiff, StepState } from '../types/orchestrator'

afterEach(cleanup)

const step: StepState = { step_index: 0, agent_name: 'Coder', status: 'completed', output_summary: 'Generated auth middleware.', created_at: '2026-10-05T12:00:00Z' }
const diffs: FileDiff[] = [
  { path: 'app/auth.py', status: 'modified', patch: '--- a/app/auth.py\n+++ b/app/auth.py\n@@ -1 +1 @@\n-old\n+new\n' },
  { path: 'docs/auth.md', status: 'added', patch: '--- /dev/null\n+++ b/docs/auth.md\n@@ -0,0 +1 @@\n+# Authentication\n' },
  { path: 'old.txt', status: 'deleted', patch: '-obsolete\n' },
]

it('renders agent summaries and a checkpoint marker', () => {
  render(<PipelineTimeline stateHistory={[step]} status="awaiting_approval" />)
  expect(screen.getByText('Coder')).toBeTruthy()
  expect(screen.getByText(step.output_summary)).toBeTruthy()
  expect(screen.getByLabelText('Completed')).toBeTruthy()
  expect(screen.getByLabelText('Checkpoint')).toBeTruthy()
})

it('shows running execution even before history is recorded', () => {
  render(<PipelineTimeline stateHistory={[]} status="running" />)
  expect(screen.getByLabelText('Running')).toBeTruthy()
})

it('renders failed steps without a duplicate failure marker', () => {
  render(<PipelineTimeline stateHistory={[{ ...step, status: 'failed' }]} status="failed" />)
  expect(screen.getAllByLabelText('Failed')).toHaveLength(1)
})

it('selects files and highlights additions and deletions', () => {
  render(<DiffViewer diffs={diffs} />)
  const lines = screen.getByLabelText('Unified patch').querySelectorAll('code > span')
  expect([...lines].find((line) => line.textContent?.trim() === '+new')?.className).toContain('bg-emerald-950/40')
  expect([...lines].find((line) => line.textContent?.trim() === '-old')?.className).toContain('bg-rose-950/40')
  expect([...lines].find((line) => line.textContent?.startsWith('@@'))?.className).toContain('bg-indigo-950')
  expect(screen.getByLabelText('Unified patch').textContent).toBe(diffs[0].patch)
  fireEvent.click(screen.getByRole('button', { name: 'Added: docs/auth.md' }))
  expect(screen.getByLabelText('Unified patch').textContent).toContain('# Authentication')
  expect(screen.getByRole('button', { name: 'Added: docs/auth.md' }).getAttribute('aria-pressed')).toBe('true')
  expect(screen.getByRole('button', { name: 'Deleted: old.txt' })).toBeTruthy()
})

it('falls back when the selected file disappears and handles empty diffs', () => {
  const { rerender } = render(<DiffViewer diffs={diffs} />)
  fireEvent.click(screen.getByRole('button', { name: 'Added: docs/auth.md' }))
  rerender(<DiffViewer diffs={[diffs[0]]} />)
  expect(screen.getByLabelText('Unified patch').textContent).toContain('+new')
  rerender(<DiffViewer diffs={[]} />)
  expect(screen.getByText('No file changes.')).toBeTruthy()
})

it('escapes HTML in patch content', () => {
  render(<DiffViewer diffs={[{ ...diffs[0], patch: '+<script>alert(1)</script>\n' }]} />)
  expect(screen.getByLabelText('Unified patch').querySelector('script')).toBeNull()
  expect(screen.getByLabelText('Unified patch').textContent).toContain('<script>')
})