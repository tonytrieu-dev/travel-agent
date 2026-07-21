import { useEffect, useRef, useState } from "react"
import { ApiError, cancelBooking, confirmBooking, executeBooking, getBooking, requestBooking } from "../api/client"
import type { BookingLogOut, FlightOfferOut, TripRequestOut } from "../api/types"

interface BookingModuleProps {
  trip: TripRequestOut
  selectedOffer: FlightOfferOut | null
  onSearchAgain: () => void
}

const POLL_INTERVAL_MS = 5_000

function formatCountdown(msRemaining: number): string {
  if (msRemaining <= 0) return "expired"
  const totalSeconds = Math.floor(msRemaining / 1000)
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  return `${minutes}:${seconds.toString().padStart(2, "0")}`
}

export function BookingModule({ trip, selectedOffer, onSearchAgain }: BookingModuleProps) {
  const [bookingLog, setBookingLog] = useState<BookingLogOut | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [announcement, setAnnouncement] = useState("")
  const [nowMs, setNowMs] = useState(() => Date.now())
  const previousStateRef = useRef<string | null>(null)

  // Tick every second so the countdown display stays live.
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), 1_000)
    return () => clearInterval(timer)
  }, [])

  // Poll the server for real state while a booking is open and not yet terminal — the panel
  // never fabricates a transition the server hasn't confirmed.
  useEffect(() => {
    if (!bookingLog) return
    const terminal = ["EXECUTED", "CANCELLED", "EXPIRED"]
    if (terminal.includes(bookingLog.state)) return

    const poll = setInterval(async () => {
      try {
        const refreshed = await getBooking(bookingLog.id)
        setBookingLog(refreshed)
      } catch {
        // Transient poll failures are not surfaced — the next successful poll self-corrects.
      }
    }, POLL_INTERVAL_MS)
    return () => clearInterval(poll)
  }, [bookingLog])

  // Announce state transitions for screen readers.
  useEffect(() => {
    if (bookingLog && bookingLog.state !== previousStateRef.current) {
      setAnnouncement(`Booking is now ${bookingLog.state.replaceAll("_", " ").toLowerCase()}.`)
      previousStateRef.current = bookingLog.state
    }
  }, [bookingLog])

  if (!selectedOffer) {
    return (
      <section className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-lg font-semibold text-slate-900">Book this trip</h2>
        <p className="mt-2 text-sm text-slate-500">Select a flight offer above to start booking.</p>
      </section>
    )
  }

  const expiresAtMs = bookingLog ? new Date(bookingLog.expires_at).getTime() : null
  const isPastExpiry = expiresAtMs !== null && nowMs >= expiresAtMs

  const runAction = async (action: () => Promise<BookingLogOut>) => {
    setIsLoading(true)
    setErrorMessage(null)
    try {
      const updated = await action()
      setBookingLog(updated)
    } catch (error) {
      if (error instanceof ApiError) {
        setErrorMessage(error.message)
        if (error.code === "booking_expired" && bookingLog) {
          // Trust the server's definitive verdict over our own clock-based guess.
          try {
            setBookingLog(await getBooking(bookingLog.id))
          } catch {
            // Fall through — errorMessage already reflects the expiry.
          }
        }
      } else {
        setErrorMessage("Something went wrong. Please try again.")
      }
    } finally {
      setIsLoading(false)
    }
  }

  const handleRequest = () =>
    runAction(() => requestBooking(trip.id, { flight_search_result_id: selectedOffer.id }))
  const handleConfirm = () => bookingLog && runAction(() => confirmBooking(bookingLog.id))
  const handleExecute = () => bookingLog && runAction(() => executeBooking(bookingLog.id))
  const handleCancel = () => bookingLog && runAction(() => cancelBooking(bookingLog.id))

  return (
    <section className="rounded-xl border-2 border-indigo-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">Book this trip</h2>
      <p className="mt-1 text-sm text-slate-600">
        {selectedOffer.carrier} · ${selectedOffer.price_usd.toFixed(2)} {selectedOffer.currency}
      </p>

      <div aria-live="polite" className="sr-only">
        {announcement}
      </div>

      {errorMessage && (
        <p role="alert" className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {!bookingLog && (
        <button
          type="button"
          onClick={handleRequest}
          disabled={isLoading}
          className="mt-4 rounded-md bg-indigo-600 px-4 py-2.5 font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isLoading ? "Requesting…" : "Request booking"}
        </button>
      )}

      {bookingLog?.state === "PENDING_USER_CONFIRMATION" && !isPastExpiry && (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-600">
            Price hold expires in <span className="font-mono font-semibold">{formatCountdown(expiresAtMs! - nowMs)}</span>
          </p>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={handleConfirm}
              disabled={isLoading}
              className="rounded-md bg-emerald-600 px-4 py-2.5 font-semibold text-white transition hover:bg-emerald-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isLoading ? "Confirming…" : "I confirm — book this flight"}
            </button>
            <button
              type="button"
              onClick={handleCancel}
              disabled={isLoading}
              className="rounded-md border border-slate-300 px-4 py-2.5 font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {bookingLog?.state === "CONFIRMED" && !isPastExpiry && (
        <div className="mt-4 space-y-3">
          <p className="text-sm text-slate-600">
            Price hold expires in <span className="font-mono font-semibold">{formatCountdown(expiresAtMs! - nowMs)}</span>
          </p>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={handleExecute}
              disabled={isLoading}
              className="rounded-md bg-indigo-600 px-4 py-2.5 font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
            >
              {isLoading ? "Executing…" : "Execute booking"}
            </button>
            <button
              type="button"
              onClick={handleCancel}
              disabled={isLoading}
              className="rounded-md border border-slate-300 px-4 py-2.5 font-medium text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {bookingLog &&
        ["PENDING_USER_CONFIRMATION", "CONFIRMED"].includes(bookingLog.state) &&
        isPastExpiry && (
          <div className="mt-4 space-y-3">
            <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
              This price hold has expired. Please search again for current prices.
            </p>
            <button
              type="button"
              onClick={onSearchAgain}
              className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700"
            >
              Search again
            </button>
          </div>
        )}

      {bookingLog?.state === "EXECUTED" && (
        <div className="mt-4 space-y-3">
          <p className="rounded-md bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-800">
            Booked. Reference: <span className="font-mono">{bookingLog.booking_reference}</span>
          </p>
          {bookingLog.booking_options && bookingLog.booking_options.length > 0 && (
            <ul className="space-y-1">
              {bookingLog.booking_options.map((option, index) => {
                const link = typeof option.link === "string" ? option.link : typeof option.url === "string" ? option.url : null
                const label = typeof option.book_with === "string" ? option.book_with : typeof option.name === "string" ? option.name : `Option ${index + 1}`
                return (
                  <li key={index}>
                    {link ? (
                      <a href={link} target="_blank" rel="noreferrer" className="text-sm text-indigo-600 hover:underline">
                        Book with {label}
                      </a>
                    ) : (
                      <span className="text-sm text-slate-600">{label}</span>
                    )}
                  </li>
                )
              })}
            </ul>
          )}
        </div>
      )}

      {bookingLog?.state === "CANCELLED" && (
        <div className="mt-4 space-y-3">
          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">This booking was cancelled.</p>
          <button
            type="button"
            onClick={onSearchAgain}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700"
          >
            Search again
          </button>
        </div>
      )}

      {bookingLog?.state === "EXPIRED" && (
        <div className="mt-4 space-y-3">
          <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-700">
            This booking expired before it was completed. Please search again for current prices.
          </p>
          <button
            type="button"
            onClick={onSearchAgain}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700"
          >
            Search again
          </button>
        </div>
      )}
    </section>
  )
}
