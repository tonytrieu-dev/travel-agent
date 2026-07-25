import { useMemo, useState } from "react"
import { listBookings } from "../api/client"
import type { BookingLogOut, BookingState, TripRequestOut } from "../api/types"
import { usePolledResource } from "../hooks/usePolledResource"
import { FilterSelect } from "./FilterSelect"

interface ApprovalHistoryPanelProps {
  trips: TripRequestOut[]
  isRunActive: boolean
}

const STATE_LABELS: Record<BookingState, string> = {
  PENDING_USER_CONFIRMATION: "Awaiting human approval",
  CONFIRMED: "Approved",
  EXECUTED: "Handed off to airline",
  CANCELLED: "Cancelled",
  EXPIRED: "Expired",
}

function stateStyles(state: BookingState): string {
  if (state === "CONFIRMED" || state === "EXECUTED") return "bg-emerald-100 text-emerald-700"
  if (state === "PENDING_USER_CONFIRMATION") return "bg-amber-100 text-amber-700"
  return "bg-slate-100 text-slate-600"
}

function stateLabel(state: string): string {
  return state.replaceAll("_", " ").toLowerCase()
}

/** One booking's append-only transition trail. A null actor is the system expiring a stale fare
 * rather than a person deciding, which is the distinction an auditor cares about. */
function ApprovalTrail({ booking, routeLabel }: { booking: BookingLogOut; routeLabel: string }) {
  return (
    <div className="space-y-3 rounded-xl border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <p className="font-semibold text-slate-900">Booking #{booking.id}</p>
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-500">
              {routeLabel}
            </span>
          </div>
          <p className="text-xs text-slate-500">
            Requested {new Date(booking.created_at).toLocaleString()}
          </p>
        </div>
        <span
          className={`rounded-full px-2.5 py-1 text-xs font-medium ${stateStyles(booking.state)}`}
        >
          {STATE_LABELS[booking.state]}
        </span>
      </div>

      {booking.transitions.length === 0 ? (
        <p className="text-sm text-slate-500">
          No approval decisions recorded yet — this booking is still awaiting a human.
        </p>
      ) : (
        <ol className="space-y-2">
          {booking.transitions.map((transition) => (
            <li
              key={`${transition.created_at}-${transition.to_state}`}
              className="rounded-lg border border-slate-200 bg-slate-50 p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-medium text-slate-900">
                  {stateLabel(transition.from_state)} → {stateLabel(transition.to_state)}
                </span>
                <span className="rounded bg-white px-1.5 py-0.5 text-xs font-medium text-slate-600">
                  {transition.reason}
                </span>
              </div>
              <p className="mt-1 text-xs text-slate-500">
                {new Date(transition.created_at).toLocaleString()} ·{" "}
                {transition.actor_user_id != null ? `user ${transition.actor_user_id}` : "system"}
              </p>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

export function ApprovalHistoryPanel({ trips, isRunActive }: ApprovalHistoryPanelProps) {
  const [stateFilter, setStateFilter] = useState<string>("all")
  const { data, errorMessage } = usePolledResource<BookingLogOut[]>({
    fetcher: listBookings,
    isRunActive,
    errorText: "Could not load approval history.",
  })
  // Memoized so the `?? []` fallback doesn't hand useMemo a fresh array on every render.
  const bookings = useMemo(() => data ?? [], [data])

  const routeLabels = useMemo(
    () => new Map(trips.map((trip) => [trip.id, `${trip.origin} → ${trip.destination_airport}`])),
    [trips],
  )

  const stateOptions = useMemo(
    () => [
      { value: "all", label: "All states" },
      ...[...new Set(bookings.map((booking) => booking.state))].sort().map((state) => ({
        value: state,
        label: STATE_LABELS[state],
      })),
    ],
    [bookings],
  )

  const visibleBookings =
    stateFilter === "all"
      ? bookings
      : bookings.filter((booking) => booking.state === stateFilter)

  if (errorMessage) {
    return (
      <p role="alert" className="rounded-xl bg-red-50 p-6 text-sm text-red-700">
        {errorMessage}
      </p>
    )
  }

  if (bookings.length === 0) {
    return (
      <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
        No approvals yet — request a booking from a trip to see its human-approval trail here.
      </p>
    )
  }

  return (
    <section className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Approval history</h2>
          <p className="text-sm text-slate-500">
            Every human decision on a booking, from the append-only audit trail.
          </p>
        </div>
        <FilterSelect
          value={stateFilter}
          onChange={setStateFilter}
          ariaLabel="Filter by booking state"
          options={stateOptions}
        />
      </div>

      {visibleBookings.length === 0 ? (
        <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
          No bookings in this state.
        </p>
      ) : (
        <div className="space-y-3">
          {visibleBookings.map((booking) => (
            <ApprovalTrail
              key={booking.id}
              booking={booking}
              routeLabel={routeLabels.get(booking.trip_request_id) ?? `trip ${booking.trip_request_id}`}
            />
          ))}
        </div>
      )}
    </section>
  )
}
