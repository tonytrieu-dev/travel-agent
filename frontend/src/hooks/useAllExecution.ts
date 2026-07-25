import { useMemo } from "react"
import { getAllExecution } from "../api/client"
import type { AgentRunOut } from "../api/types"
import { usePolledResource } from "./usePolledResource"

interface UseAllExecutionOptions {
  isRunActive: boolean
}

interface AllExecutionState {
  runs: AgentRunOut[]
  errorMessage: string | null
}

// Backs the global "Agent execution history" tab: every run across every trip.
export function useAllExecution({ isRunActive }: UseAllExecutionOptions): AllExecutionState {
  const fetcher = useMemo(() => async () => (await getAllExecution()).agent_runs, [])
  const { data, errorMessage } = usePolledResource<AgentRunOut[]>({
    fetcher,
    isRunActive,
    errorText: "Could not load execution data.",
  })

  return { runs: data ?? [], errorMessage }
}
