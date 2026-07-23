import { useCallback, useEffect, useRef, useState } from "react"
import { getTripExecution } from "../api/client"
import type { ExecutionPanelOut } from "../api/types"

const IDLE_POLL_MS = 4_000
const ACTIVE_POLL_MS = 700

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
  // Skip a tick if a previous (possibly slow) request is still in flight, so the ~700ms active
  // poll can never stack overlapping /execution requests.
  const isFetchingRef = useRef(false)

  const refresh = useCallback(async () => {
    if (tripId == null || isFetchingRef.current) return
    isFetchingRef.current = true
    try {
      setPanelData(await getTripExecution(tripId))
      setErrorMessage(null)
    } catch {
      setErrorMessage("Could not load execution data.")
    } finally {
      isFetchingRef.current = false
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
