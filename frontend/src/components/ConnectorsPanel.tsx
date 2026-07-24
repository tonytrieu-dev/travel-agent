import { useEffect, useState } from "react"
import { ApiError, getConnectors, setSlackConnectorEnabled } from "../api/client"
import type { ConnectorsOut } from "../api/types"

export function ConnectorsPanel() {
  const [connectors, setConnectors] = useState<ConnectorsOut | null>(null)
  const [isToggling, setIsToggling] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  useEffect(() => {
    getConnectors()
      .then(setConnectors)
      .catch(() => setErrorMessage("Could not load connector status."))
  }, [])

  const handleToggle = async () => {
    if (!connectors) return
    const nextEnabled = !connectors.slack.enabled
    setIsToggling(true)
    setErrorMessage(null)
    try {
      setConnectors(await setSlackConnectorEnabled(nextEnabled))
    } catch (error) {
      setErrorMessage(error instanceof ApiError ? error.message : "Could not update the connector.")
    } finally {
      setIsToggling(false)
    }
  }

  if (!connectors) {
    return (
      <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        Loading connectors…
      </p>
    )
  }

  const { configured, enabled } = connectors.slack

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Connectors</h2>
        <p className="mt-1 text-sm text-slate-500">
          Optional delivery channels for the booking approval gate — the underlying state
          machine is unchanged either way.
        </p>
      </div>

      <div className="flex items-center justify-between rounded-lg border border-slate-200 p-4">
        <div>
          <p className="font-medium text-slate-900">Slack</p>
          <p className="mt-1 text-sm text-slate-500">
            Post a Confirm/Reject message to Slack when a booking needs approval.
          </p>
          {!configured && (
            <p className="mt-1 text-sm text-amber-600">
              Slack credentials not configured on the server.
            </p>
          )}
          {errorMessage && <p className="mt-1 text-sm text-red-600">{errorMessage}</p>}
        </div>
        <button
          type="button"
          onClick={handleToggle}
          disabled={!configured || isToggling}
          className={`min-h-11 rounded-lg border px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${
            enabled
              ? "border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100"
              : "border-slate-200 bg-white text-slate-600 hover:bg-slate-50"
          }`}
        >
          {enabled ? "Enabled" : "Disabled"}
        </button>
      </div>
    </section>
  )
}
