import { useCallback, useEffect, useRef, useState } from "react"

const IDLE_POLL_MS = 4_000
const ACTIVE_POLL_MS = 700

interface PolledResourceOptions<T> {
  fetcher: (() => Promise<T>) | null
  isRunActive: boolean
  enabled?: boolean
  /** Shown verbatim on failure, so each caller names what it was actually loading. */
  errorText: string
}

interface PolledResourceState<T> {
  data: T | null
  errorMessage: string | null
  refresh: () => Promise<void>
}

/** A null fetcher means nothing to poll yet (no trip selected), distinct from `enabled: false`. */
export function usePolledResource<T>({
  fetcher,
  isRunActive,
  enabled = true,
  errorText,
}: PolledResourceOptions<T>): PolledResourceState<T> {
  const [data, setData] = useState<T | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  // Skip a tick if a request is still in flight, so the 700ms poll can't stack overlaps.
  const isFetchingRef = useRef(false)

  const refresh = useCallback(async () => {
    if (fetcher === null || isFetchingRef.current) return
    isFetchingRef.current = true
    try {
      setData(await fetcher())
      setErrorMessage(null)
    } catch {
      setErrorMessage(errorText)
    } finally {
      isFetchingRef.current = false
    }
  }, [fetcher, errorText])

  useEffect(() => {
    if (!enabled || fetcher === null) return
    refresh()
    const interval = setInterval(refresh, isRunActive ? ACTIVE_POLL_MS : IDLE_POLL_MS)
    return () => clearInterval(interval)
  }, [enabled, fetcher, isRunActive, refresh])

  return { data, errorMessage, refresh }
}
