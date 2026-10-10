import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, createRun, fetchRun, fetchRunDiffs, fetchRuns, submitApproval } from './client'

afterEach(() => vi.unstubAllGlobals())

describe('API client', () => {
  it('fetches lists, runs, and structured diffs using encoded IDs', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{"ok":true}')))
    vi.stubGlobal('fetch', fetcher)
    await fetchRuns()
    await fetchRun('run/a b')
    await fetchRunDiffs('run/a b')
    expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
      '/v1/runs', '/v1/runs/run%2Fa%20b', '/v1/runs/run%2Fa%20b/diffs',
    ])
  })

  it('posts JSON creation and approval payloads', async () => {
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(new Response('{}')))
    vi.stubGlobal('fetch', fetcher)
    const payload = { prompt: 'Add pagination.', pipeline_mode: 'code_and_review' as const }
    await createRun(payload)
    await submitApproval('run_test', 'chk/a', { decision: 'approved' })
    expect(fetcher).toHaveBeenNthCalledWith(1, '/v1/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
    })
    expect(fetcher).toHaveBeenNthCalledWith(2, '/v1/runs/run_test/approvals/chk%2Fa', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{"decision":"approved"}',
    })
  })

  it('preserves backend errors and status codes', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(
      '{"detail":"Run not found"}', { status: 404 },
    )))
    await expect(fetchRun('missing')).rejects.toMatchObject({
      name: 'ApiError', status: 404, message: 'Run not found',
    })
  })

  it('handles non-JSON errors without masking the HTTP failure', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('Bad gateway', { status: 502 })))
    await expect(fetchRuns()).rejects.toThrow(ApiError)
  })
})