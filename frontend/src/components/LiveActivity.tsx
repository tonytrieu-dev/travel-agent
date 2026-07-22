import { useTripExecution } from "../hooks/useTripExecution"

interface LiveActivityProps {
  tripId: number
  isRunActive: boolean
}

const STEP_LABELS: Record<string, string> = {
  search_flights: "Searching flights",
  web_search: "Researching activities",
}

function labelFor(name: string): string {
  return STEP_LABELS[name] ?? name
}

export function LiveActivity({ tripId, isRunActive }: LiveActivityProps) {
  const { panelData } = useTripExecution({ tripId, enabled: isRunActive, isRunActive })

  if (!isRunActive) return null

  const events = panelData?.events ?? []
  const recent = events.slice(-5)

  return (
    <section
      aria-live="polite"
      className="rounded-xl border border-indigo-200 bg-indigo-50/60 p-4 shadow-sm"
    >
      <div className="flex items-center gap-2">
        <span className="relative flex h-2.5 w-2.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
          <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-500" />
        </span>
        <p className="text-sm font-medium text-indigo-900">The agent is working…</p>
      </div>

      {recent.length === 0 ? (
        <p className="mt-2 text-sm text-indigo-700">Starting up — the first tool calls will appear here.</p>
      ) : (
        <ol className="mt-3 space-y-1.5">
          {recent.map((event) => (
            <li key={event.seq} className="flex items-center gap-2 text-sm text-indigo-800">
              <span className="text-indigo-400">→</span>
              <span className="font-medium">{labelFor(event.name)}</span>
              <span className="text-xs text-indigo-500">{event.detail}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
