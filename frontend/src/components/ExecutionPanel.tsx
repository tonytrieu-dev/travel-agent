import { useCallback, useEffect, useState } from "react"
import { getTripExecution } from "../api/client"
import type { ExecutionPanelOut } from "../api/types"

interface ExecutionPanelProps {
  tripId: number
  isOpen: boolean
  onClose: () => void
}

const POLL_INTERVAL_MS = 4_000

export function ExecutionPanel({ tripId, isOpen, onClose }: ExecutionPanelProps) {
  const [panelData, setPanelData] = useState<ExecutionPanelOut | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const data = await getTripExecution(tripId)
      setPanelData(data)
      setErrorMessage(null)
    } catch {
      setErrorMessage("Could not load execution data.")
    }
  }, [tripId])

  useEffect(() => {
    if (!isOpen) return
    refresh()
    const interval = setInterval(refresh, POLL_INTERVAL_MS)
    return () => clearInterval(interval)
  }, [isOpen, refresh])

  if (!isOpen) return null

  const agentRun = panelData?.agent_run ?? null

  return (
    <aside className="fixed inset-y-0 right-0 z-40 w-full max-w-md overflow-y-auto border-l border-slate-200 bg-white p-6 shadow-xl">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">Agent execution</h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={refresh}
            className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Refresh
          </button>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close execution panel"
            className="rounded-md px-2 py-1.5 text-slate-500 hover:bg-slate-100"
          >
            ✕
          </button>
        </div>
      </div>

      {errorMessage && (
        <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {!agentRun && (
        <p className="mt-4 text-sm text-slate-500">No planner run yet for this trip.</p>
      )}

      {agentRun && (
        <div className="mt-4 space-y-4">
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Status</p>
              <p className="font-medium text-slate-900">{agentRun.status}</p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Model</p>
              <p className="font-medium text-slate-900">{agentRun.model}</p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Tokens in / out</p>
              <p className="font-medium text-slate-900">
                {agentRun.total_input_tokens} / {agentRun.total_output_tokens}
              </p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Total time</p>
              <p className="font-medium text-slate-900">{agentRun.total_ms} ms</p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Tool steps</p>
              <p className="font-medium text-slate-900">{agentRun.steps.length}</p>
            </div>
            <div className="rounded-md bg-slate-50 p-3">
              <p className="text-xs uppercase text-slate-400">Budget used</p>
              <p className="font-medium text-slate-900">
                {panelData?.budget_utilization_pct != null ? `${panelData.budget_utilization_pct.toFixed(1)}%` : "—"}
              </p>
            </div>
          </div>

          <div className="rounded-md bg-slate-50 p-3 text-sm">
            <p className="text-xs uppercase text-slate-400">Estimated cost</p>
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
                <li key={step.seq} className="rounded-md border border-slate-200 p-2 text-sm">
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
                  {step.output_summary && <p className="mt-1 text-xs text-slate-600">{step.output_summary}</p>}
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}

      <div className="mt-6">
        <h3 className="text-sm font-semibold text-slate-900">Events</h3>
        {panelData && panelData.events.length === 0 && (
          <p className="mt-2 text-sm text-slate-500">No events recorded yet.</p>
        )}
        <ol className="mt-2 space-y-2">
          {panelData?.events.map((event) => (
            <li key={event.seq} className="rounded-md border border-slate-200 p-2 text-sm">
              <div className="flex items-center justify-between">
                <span className="font-medium text-slate-900">
                  {event.seq}. [{event.kind}] {event.name}
                </span>
                <span className="text-xs text-slate-500">{event.status}</span>
              </div>
              <p className="mt-0.5 text-xs text-slate-600">{event.detail}</p>
              {event.duration_ms != null && <p className="mt-0.5 text-xs text-slate-500">{event.duration_ms} ms</p>}
            </li>
          ))}
        </ol>
      </div>
    </aside>
  )
}
