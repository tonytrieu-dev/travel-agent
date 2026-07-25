import { useCallback, useEffect, useRef, useState } from "react"
import { getAllExecution } from "../api/client"
import type { AgentRunOut } from "../api/types"

const IDLE_POLL_MS = 4_000
const ACTIVE_POLL_MS = 700

interface UseAllExecutionOptions {
  isRunActive: boolean
}

interface AllExecutionState {
  runs: AgentRunOut[]
  errorMessage: string | null
}

// Backs the global "Agent execution history" tab: every run across every trip, not just the
// one currently active — mirrors useTripExecution's polling shape but with no tripId to scope by.
export function useAllExecution({ isRunActive }: UseAllExecutionOptions): AllExecutionState {
  const [runs, setRuns] = useState<AgentRunOut[]>([])
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const isFetchingRef = useRef(false)

  const refresh = useCallback(async () => {
    if (isFetchingRef.current) return
    isFetchingRef.current = true
    try {
      setRuns((await getAllExecution()).agent_runs)
      setErrorMessage(null)
    } catch {
      setErrorMessage("Could not load execution data.")
    } finally {
      isFetchingRef.current = false
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, isRunActive ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(interval)
  }, [isRunActive, refresh])

  return { runs, errorMessage }
}
