import { useTripExecution } from "../hooks/useTripExecution"

interface ExecutionPanelProps {
  tripId: number
  isRunActive: boolean
}

export function ExecutionPanel({ tripId, isRunActive }: ExecutionPanelProps) {
  const { panelData, errorMessage, refresh } = useTripExecution({
    tripId,
    enabled: true,
    isRunActive,
  })

  const agentRun = panelData?.agent_run ?? null

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Agent execution</h2>
          <p className="text-sm text-slate-500">
            Live trace of every model and tool step for this trip.
          </p>
        </div>
        <button
          type="button"
          onClick={refresh}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          Refresh
        </button>
      </div>

      {errorMessage && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {!agentRun && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
          No planner run yet for this trip. Generate an itinerary to see the agent work here.
        </p>
      )}

      {agentRun && (
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
            <MetricTile label="Status" value={agentRun.status} />
            <MetricTile label="Model" value={agentRun.model} />
            <MetricTile
              label="Tokens in / out"
              value={`${agentRun.total_input_tokens} / ${agentRun.total_output_tokens}`}
            />
            <MetricTile label="Total time" value={`${agentRun.total_ms} ms`} />
            <MetricTile label="Tool steps" value={String(agentRun.steps.length)} />
            <MetricTile
              label="Budget used"
              value={
                panelData?.budget_utilization_pct != null
                  ? `${panelData.budget_utilization_pct.toFixed(1)}%`
                  : "—"
              }
            />
          </div>

          <div className="rounded-lg border border-slate-200 bg-white p-3 text-sm">
            <p className="text-xs uppercase tracking-wide text-slate-400">Estimated cost</p>
            <p className="font-medium text-slate-900">
              {panelData?.estimated_cost_usd != null
                ? `$${panelData.estimated_cost_usd.toFixed(4)} estimated — actual $0, free tier`
                : "actual $0 — free tier"}
            </p>
          </div>

          <div>
            <h3 className="text-sm font-semibold text-slate-900">Steps</h3>
            <ol className="mt-2 space-y-2">
              {agentRun.steps.map((step) => (
                <li key={step.seq} className="rounded-lg border border-slate-200 bg-white p-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-900">
                      {step.seq}. [{step.kind}] {step.name}
                    </span>
                    <span className="text-xs text-slate-500">{step.status}</span>
                  </div>
                  {(step.duration_ms != null || step.tokens != null) && (
                    <p className="mt-0.5 text-xs text-slate-500">
                      {step.duration_ms != null && `${step.duration_ms} ms`}
                      {step.duration_ms != null && step.tokens != null && " · "}
                      {step.tokens != null && `${step.tokens} tokens`}
                    </p>
                  )}
                  {step.output_summary && (
                    <p className="mt-1 text-xs text-slate-600">{step.output_summary}</p>
                  )}
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      <div>
        <h3 className="text-sm font-semibold text-slate-900">Events</h3>
        {panelData && panelData.events.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">No events recorded yet.</p>
        )}
        <ol className="mt-2 space-y-2">
          {panelData?.events.map((event) => (
            <li key={event.seq} className="rounded-lg border border-slate-200 bg-white p-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-900">
                  {event.seq}. [{event.kind}] {event.name}
                </span>
                <span className="text-xs text-slate-500">{event.status}</span>
              </div>
              <p className="mt-0.5 text-xs text-slate-600">{event.detail}</p>
              {event.duration_ms != null && (
                <p className="mt-0.5 text-xs text-slate-500">{event.duration_ms} ms</p>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="font-medium text-slate-900">{value}</p>
    </div>
  )
}
