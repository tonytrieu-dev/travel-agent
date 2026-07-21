import { useCallback, useEffect, useState } from "react"
import { getTripExecution } from "../api/client"
import type { ExecutionPanelOut } from "../api/types"

const IDLE_POLL_MS = 4_000
// Poll fast while the agent is mid-run so tool calls surface as they happen, not 4s late.
const ACTIVE_POLL_MS = 1_200

interface UseTripExecutionOptions {
  tripId: number | null
  enabled: boolean
  isRunActive: boolean
}

interface TripExecutionState {
  panelData: ExecutionPanelOut | null
  errorMessage: string | null
  refresh: () => Promise<void>
}

export function useTripExecution({
  tripId,
  enabled,
  isRunActive,
}: UseTripExecutionOptions): TripExecutionState {
  const [panelData, setPanelData] = useState<ExecutionPanelOut | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    if (tripId == null) return
    try {
      setPanelData(await getTripExecution(tripId))
      setErrorMessage(null)
    } catch {
      setErrorMessage("Could not load execution data.")
    }
  }, [tripId])

  useEffect(() => {
    if (!enabled || tripId == null) return
    refresh()
    const interval = setInterval(refresh, isRunActive ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(interval)
  }, [enabled, tripId, isRunActive, refresh])

  return { panelData, errorMessage, refresh }
}
