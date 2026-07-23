import { useTripExecution } from "../hooks/useTripExecution"
import type { AgentRunOut, AgentRunStepOut } from "../api/types"

interface ExecutionPanelProps {
  tripId: number
  isRunActive: boolean
}

const STEP_KIND_STYLES: Record<string, string> = {
  model: "bg-slate-100 text-slate-700",
  tool: "bg-indigo-100 text-indigo-700",
}

const STATUS_LABELS: Record<string, string> = {
  ok: "Successful",
  completed: "Completed",
  failed: "Failed",
  no_result: "No result",
  unavailable: "Unavailable",
}

function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status
}

function statusStyles(status: string): string {
  if (status === "ok" || status === "completed") return "bg-emerald-100 text-emerald-700"
  if (status === "failed") return "bg-red-100 text-red-700"
  if (status === "no_result" || status === "unavailable") return "bg-amber-100 text-amber-700"
  return "bg-slate-100 text-slate-700"
}

function countByKind(steps: AgentRunStepOut[], kind: string): number {
  return steps.filter((step) => step.kind === kind).length
}

function MetricTile({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-slate-50 p-3">
      <p className="text-xs uppercase tracking-wide text-slate-400">{label}</p>
      <p className="font-medium text-slate-900">{value}</p>
    </div>
  )
}

function AgentRunCard({ run }: { run: AgentRunOut }) {
  return (
    <div className="space-y-4 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="font-semibold text-slate-900">Run #{run.id}</p>
          <p className="text-xs text-slate-500">{new Date(run.started_at).toLocaleString()}</p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${statusStyles(run.status)}`}
        >
          {statusLabel(run.status)}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
        <MetricTile label="Model" value={run.model} />
        <MetricTile label="Tool calls" value={String(countByKind(run.steps, "tool"))} />
        <MetricTile label="Model calls" value={String(countByKind(run.steps, "model"))} />
        <MetricTile label="Total time" value={`${run.total_ms} ms`} />
        <MetricTile
          label="Tokens in / out"
          value={`${run.total_input_tokens} / ${run.total_output_tokens}`}
        />
        <MetricTile
          label="Budget used"
          value={run.budget_utilization_pct != null ? `${run.budget_utilization_pct.toFixed(1)}%` : "—"}
        />
        <MetricTile
          label="Est. cost"
          value={run.estimated_cost_usd != null ? `$${run.estimated_cost_usd.toFixed(4)}` : "—"}
        />
        <MetricTile label="Total steps" value={String(run.steps.length)} />
      </div>

      <div>
        <h4 className="text-sm font-semibold text-slate-900">Steps</h4>
        <ol className="mt-2 space-y-2">
          {run.steps.map((step) => (
            <li key={step.seq} className="rounded-lg border border-slate-200 bg-white p-2 text-sm">
              <div className="flex items-center justify-between gap-2">
                <span className="flex items-center gap-2">
                  <span className="text-xs text-slate-400">{step.seq}.</span>
                  <span
                    className={`rounded px-1.5 py-0.5 text-xs font-semibold uppercase tracking-wide ${
                      STEP_KIND_STYLES[step.kind] ?? "bg-slate-100 text-slate-700"
                    }`}
                  >
                    {step.kind}
                  </span>
                  <span className="font-medium text-slate-900">{step.name}</span>
                </span>
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${statusStyles(step.status)}`}
                >
                  {statusLabel(step.status)}
                </span>
              </div>
              {(step.duration_ms != null || step.tokens != null) && (
                <p className="mt-1 text-xs text-slate-500">
                  {step.duration_ms != null && `${step.duration_ms} ms`}
                  {step.duration_ms != null && step.tokens != null && " · "}
                  {step.tokens != null && `${step.tokens} tokens`}
                </p>
              )}
              {step.input_summary && (
                <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap text-slate-600">
                  {step.input_summary}
                </pre>
              )}
              {step.output_summary && (
                <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap text-slate-600">
                  {step.output_summary}
                </pre>
              )}
            </li>
          ))}
        </ol>
      </div>
    </div>
  )
}

export function ExecutionPanel({ tripId, isRunActive }: ExecutionPanelProps) {
  const { panelData, errorMessage, refresh } = useTripExecution({
    tripId,
    enabled: true,
    isRunActive,
  })

  const runs = panelData?.agent_runs ?? []

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Agent execution history</h2>
          <p className="text-sm text-slate-500">
            Every planner run for this trip, newest first — model/tool steps, tokens, timing, cost.
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

      {runs.length === 0 && (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
          No planner run yet for this trip. Generate an itinerary to see the agent work here.
        </p>
      )}

      {runs.map((run) => (
        <AgentRunCard key={run.id} run={run} />
      ))}

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
                <span
                  className={`rounded px-1.5 py-0.5 text-xs font-medium ${statusStyles(event.status)}`}
                >
                  {statusLabel(event.status)}
                </span>
              </div>
              <p className="mt-0.5 text-xs text-slate-600">{event.detail}</p>
              {event.duration_ms != null && (
                <p className="mt-0.5 text-xs text-slate-500">{event.duration_ms} ms</p>
              )}
              {event.data != null && (
                <details className="mt-1">
                  <summary className="cursor-pointer text-xs text-slate-500 hover:text-slate-700">
                    Raw event data
                  </summary>
                  <pre className="mt-1 overflow-x-auto rounded bg-slate-50 p-2 text-xs whitespace-pre-wrap text-slate-600">
                    {JSON.stringify(event.data, null, 2)}
                  </pre>
                </details>
              )}
            </li>
          ))}
        </ol>
      </div>
    </section>
  )
}
