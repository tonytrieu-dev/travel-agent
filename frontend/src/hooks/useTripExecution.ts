import { useMemo } from "react"
import { getTripExecution } from "../api/client"
import type { ExecutionPanelOut } from "../api/types"
import { usePolledResource } from "./usePolledResource"

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
  const fetcher = useMemo(
    () => (tripId == null ? null : () => getTripExecution(tripId)),
    [tripId],
  )
  const { data, errorMessage, refresh } = usePolledResource<ExecutionPanelOut>({
    fetcher,
    isRunActive,
    enabled,
  })

  return { panelData: data, errorMessage, refresh }
}
