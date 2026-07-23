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

// Always polling once a trip exists (not just while a run is active) so this reads as a live
// activity log, not a banner that vanishes between runs.
export function LiveActivity({ tripId, isRunActive }: LiveActivityProps) {
  const { panelData } = useTripExecution({ tripId, enabled: true, isRunActive })

  const events = panelData?.events ?? []
  const recent = [...events].reverse()

  return (
    <section
      aria-live="polite"
      className="space-y-3 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <div className="flex items-center gap-2">
        {isRunActive ? (
          <span className="relative flex h-2.5 w-2.5">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
            <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-500" />
          </span>
        ) : (
          <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
        )}
        <h2 className="text-lg font-semibold text-slate-900">
          {isRunActive ? "Agent working…" : "Agent activity"}
        </h2>
      </div>

      {recent.length === 0 ? (
        <p className="text-sm text-slate-500">
          Nothing yet — tool calls will appear here once the agent runs.
        </p>
      ) : (
        <ol className="max-h-72 space-y-1.5 overflow-y-auto">
          {recent.map((event) => (
            <li key={event.seq} className="rounded-md px-2 py-1.5 text-sm text-slate-700 hover:bg-slate-50">
              <div className="flex items-center gap-2">
                <span className="text-indigo-400">→</span>
                <span className="font-medium text-slate-900">{labelFor(event.name)}</span>
                <span className="text-xs text-slate-400">{event.status}</span>
              </div>
              <p className="pl-5 text-xs text-slate-500">{event.detail}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
