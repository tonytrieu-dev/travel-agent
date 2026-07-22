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
// activity log in the sidebar, not a banner that vanishes between runs.
export function LiveActivity({ tripId, isRunActive }: LiveActivityProps) {
  const { panelData } = useTripExecution({ tripId, enabled: true, isRunActive })

  const events = panelData?.events ?? []
  const recent = events.slice(-10).reverse()

  return (
    <section aria-live="polite" className="flex-1 overflow-y-auto border-t border-slate-200 p-3">
      <div className="flex items-center gap-2 px-2">
        {isRunActive ? (
          <span className="relative flex h-2 w-2">
            <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
            <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
          </span>
        ) : (
          <span className="h-2 w-2 rounded-full bg-slate-300" />
        )}
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          {isRunActive ? "Agent working…" : "Agent activity"}
        </p>
      </div>

      {recent.length === 0 ? (
        <p className="mt-2 px-2 text-xs text-slate-400">
          Nothing yet — tool calls will appear here once the agent runs.
        </p>
      ) : (
        <ol className="mt-2 space-y-1.5">
          {recent.map((event) => (
            <li key={event.seq} className="rounded-md px-2 py-1 text-xs text-slate-700 hover:bg-slate-50">
              <div className="flex items-center gap-1.5">
                <span className="text-indigo-400">→</span>
                <span className="font-medium text-slate-900">{labelFor(event.name)}</span>
                <span className="text-slate-400">{event.status}</span>
              </div>
              <p className="pl-4 text-slate-500">{event.detail}</p>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
