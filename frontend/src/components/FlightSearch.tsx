import type { FlightLegOut, FlightOfferOut, FlightSearchOut, TripRequestOut } from "../api/types"

interface FlightSearchProps {
  trip: TripRequestOut
  searchResult: FlightSearchOut | null
  isLoading: boolean
  errorMessage: string | null
  selectedOfferId: number | null
  onSearchFlights: () => void
  onSearchNewFlight: () => void
  onSelectOffer: (offer: FlightOfferOut) => void
}

function formatDateTime(isoString: string): string {
  return new Date(isoString).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function searchButtonLabel(isLoading: boolean, searchResult: FlightSearchOut | null): string {
  if (isLoading) return "Searching…"
  if (searchResult?.is_stale) return "Refresh prices"
  if (searchResult) return "Search for new flight"
  return "Search flights"
}

function FlightDirection({ label, legs }: { label: string; legs: FlightLegOut[] }) {
  const firstLeg = legs[0]
  const lastLeg = legs.at(-1)
  if (!firstLeg || !lastLeg) return null
  return (
    <div className="grid grid-cols-[5rem_1fr] gap-x-3 gap-y-0.5 text-sm">
      <span className="font-medium text-slate-500">{label}</span>
      <span className="font-medium text-slate-800">
        {firstLeg.departure_airport} → {lastLeg.arrival_airport}
      </span>
      <span />
      <span className="text-slate-600">
        {formatDateTime(firstLeg.depart_at)} → {formatDateTime(lastLeg.arrive_at)}
      </span>
    </div>
  )
}

export function FlightSearch({
  trip,
  searchResult,
  isLoading,
  errorMessage,
  selectedOfferId,
  onSearchFlights,
  onSearchNewFlight,
  onSelectOffer,
}: FlightSearchProps) {
  // Only non-stop flights are offered — drop any itinerary with a layover.
  const nonStopOffers = searchResult?.offers.filter((offer) => offer.stops === 0) ?? []

  return (
    <section className="space-y-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 className="text-lg font-semibold text-slate-900">Flights</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            Trip: {trip.origin} → {trip.destination_airport} · {trip.depart_date}
            {trip.return_date ? ` – ${trip.return_date}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={!searchResult || searchResult.is_stale ? onSearchFlights : onSearchNewFlight}
          disabled={isLoading}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {searchButtonLabel(isLoading, searchResult)}
        </button>
      </div>

      {errorMessage && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {searchResult?.unavailable_reason && (
        <p className="rounded-md bg-slate-50 px-3 py-2 text-sm text-slate-600">
          {searchResult.unavailable_reason}
        </p>
      )}

      {searchResult?.is_stale && (
        <p role="alert" className="rounded-md bg-amber-50 px-3 py-2 text-sm text-amber-800">
          These saved prices may be outdated. Refresh prices before continuing to airline booking.
        </p>
      )}

      {searchResult && nonStopOffers.length > 0 && (
        <ul className="space-y-3">
          {nonStopOffers.map((offer) => {
            const outboundEnd = offer.legs.findIndex(
              (leg) => leg.arrival_airport === trip.destination_airport,
            )
            const outboundLegs = offer.legs.slice(0, outboundEnd + 1)
            const returnLegs = offer.legs.slice(outboundEnd + 1)
            return (
              <li key={offer.id}>
                <div
                  className={`flex items-center justify-between gap-4 rounded-lg border p-4 transition ${
                    selectedOfferId === offer.id
                      ? "border-indigo-500 bg-indigo-50"
                      : "border-slate-200 hover:border-slate-300"
                  }`}
                >
                  <label className="flex flex-1 cursor-pointer items-center gap-3">
                    <input
                      type="radio"
                      name="flight-offer"
                      checked={selectedOfferId === offer.id}
                      onChange={() => onSelectOffer(offer)}
                      disabled={searchResult.is_stale}
                      className="h-4 w-4"
                    />
                    <div className="space-y-3">
                      <p className="font-semibold text-slate-900">{offer.carrier}</p>
                      {trip.return_date ? (
                        <>
                          <FlightDirection label="Outbound" legs={outboundLegs} />
                          <FlightDirection label="Return" legs={returnLegs} />
                        </>
                      ) : (
                        <p className="text-sm text-slate-600">
                          {formatDateTime(offer.depart_at)} → {formatDateTime(offer.arrive_at)}
                        </p>
                      )}
                    </div>
                  </label>
                  <div className="text-right">
                    <p className="font-semibold whitespace-nowrap text-slate-900">
                      ${offer.price_usd.toFixed(2)} {offer.currency}
                    </p>
                  </div>
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
