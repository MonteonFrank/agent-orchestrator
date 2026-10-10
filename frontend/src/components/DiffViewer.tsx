import { useId, useState } from 'react'
import { FileMinus, FileText, FilePlus, Files, Folder } from 'lucide-react'
import { cn } from '../lib/utils'
import type { FileDiff } from '../types/orchestrator'

export interface DiffViewerProps {
  diffs: FileDiff[]
}

const fileStyles = {
  added: { Icon: FilePlus, color: 'text-emerald-300', label: 'Added' },
  modified: { Icon: FileText, color: 'text-sky-300', label: 'Modified' },
  deleted: { Icon: FileMinus, color: 'text-red-300', label: 'Deleted' },
}

export function DiffViewer({ diffs }: DiffViewerProps) {
  const [selectedKey, setSelectedKey] = useState<string | null>(null)
  const panelId = useId()
  const entries = diffs.map((diff, index) => ({ diff, key: `${index}:${diff.path}` }))
  const selected = entries.find((entry) => entry.key === selectedKey) ?? entries[0]
  const groups = new Map<string, typeof entries>()
  for (const entry of entries) {
    const { diff } = entry
    const separator = diff.path.lastIndexOf('/')
    const directory = separator < 0 ? '.' : diff.path.slice(0, separator)
    const group = groups.get(directory) ?? []
    group.push(entry)
    groups.set(directory, group)
  }

  if (!selected) return <div className="flex items-center gap-3 border-y border-zinc-200 py-8 text-sm text-zinc-500"><Files size={20} aria-hidden="true" />No file changes.</div>
  const style = fileStyles[selected.diff.status]

  return (
    <div className="grid min-w-0 overflow-hidden rounded-md border border-zinc-700 bg-zinc-950 text-zinc-200 md:grid-cols-12">
      <nav aria-label="Changed files" className="min-w-0 max-h-64 overflow-auto border-b border-zinc-700 bg-zinc-900 py-3 md:col-span-4 md:max-h-[32rem] md:border-b-0 md:border-r">
        <h3 className="px-4 pb-3 text-xs font-semibold uppercase text-zinc-400">Files <span className="ml-2 text-zinc-500">{diffs.length}</span></h3>
        {[...groups].map(([directory, items]) => <div key={directory} className="mb-3">
          <div className="flex items-start gap-2 px-4 py-2 text-xs text-zinc-500"><Folder size={14} className="mt-0.5 shrink-0" aria-hidden="true" /><span className="break-all">{directory === '.' ? 'Repository' : directory}</span></div>
          {items.map(({ diff, key }) => {
            const { Icon, color, label } = fileStyles[diff.status]
            return <button key={key} type="button" aria-pressed={selected.key === key} aria-controls={panelId} aria-label={`${label}: ${diff.path}`} title={diff.path} onClick={() => setSelectedKey(key)} className={cn('flex w-full items-start gap-2 border-l-2 px-4 py-2 text-left text-xs hover:bg-zinc-800', selected.key === key ? 'border-emerald-400 bg-zinc-800' : 'border-transparent')}>
              <Icon size={16} className={cn('mt-0.5 shrink-0', color)} aria-hidden="true" />
              <span className="min-w-0 flex-1 break-all leading-5">{diff.path.split('/').at(-1)}</span>
              <span className={cn('text-[10px]', color)}>{diff.status === 'modified' ? 'M' : diff.status === 'added' ? 'A' : 'D'}</span>
            </button>
          })}
        </div>)}
      </nav>
      <section id={panelId} aria-label={`Diff for ${selected.diff.path}`} className="min-w-0 md:col-span-8">
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-zinc-700 px-4 py-3">
          <h3 className="break-all font-mono text-xs font-medium">{selected.diff.path}</h3>
          <span className={cn('text-xs', style.color)}>{style.label}</span>
        </header>
        {selected.diff.patch ? <pre className="diff-code m-0 max-h-[32rem] overflow-auto py-3 text-xs leading-6 [tab-size:4]" tabIndex={0} aria-label="Unified patch">
          <code className="block min-w-full w-max">{selected.diff.patch.split('\n').map((line, index, lines) => (
            <span key={index} className={cn('block min-h-6 px-4',
              line.startsWith('+') ? 'bg-emerald-950/40 text-emerald-300'
                : line.startsWith('-') ? 'bg-rose-950/40 text-rose-300'
                : line.startsWith('@@') ? 'bg-indigo-950 text-indigo-300'
                : 'text-zinc-300',
            )}>{line}{index < lines.length - 1 ? '\n' : ''}</span>
          ))}</code>
        </pre> : <p className="p-6 text-sm text-zinc-400">No text patch available.</p>}
      </section>
    </div>
  )
}

export default DiffViewer