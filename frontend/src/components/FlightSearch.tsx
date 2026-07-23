import type { FlightOfferOut, FlightSearchOut, TripRequestOut } from "../api/types"

interface FlightSearchProps {
  trip: TripRequestOut
  searchResult: FlightSearchOut | null
  isLoading: boolean
  errorMessage: string | null
  selectedOfferId: number | null
  onSearch: () => void
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

export function FlightSearch({
  trip,
  searchResult,
  isLoading,
  errorMessage,
  selectedOfferId,
  onSearch,
  onSelectOffer,
}: FlightSearchProps) {
  // Only non-stop flights are offered — drop any itinerary with a layover.
  const nonStopOffers = searchResult?.offers.filter((offer) => offer.stops === 0) ?? []

  // Backend returns offers ascending by price_usd, but derive the cheapest defensively.
  const cheapestOfferId = nonStopOffers.reduce<number | null>((cheapestId, offer) => {
    if (cheapestId === null) return offer.id
    const cheapest = nonStopOffers.find((candidate) => candidate.id === cheapestId)
    return cheapest && offer.price_usd < cheapest.price_usd ? offer.id : cheapestId
  }, null)

  // Every offer for a given search shares the same source (live vs cached).
  const resultSource = nonStopOffers[0]?.source ?? null

  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="flex items-center gap-2">
            <h2 className="text-lg font-semibold text-slate-900">Flights</h2>
            {resultSource && (
              <span className="text-xs text-slate-400">
                {resultSource === "live" ? "live results" : "cached results"}
              </span>
            )}
          </div>
          <p className="text-sm text-slate-500">
            Trip #{trip.id} · {trip.origin} → {trip.destination_airport} · {trip.depart_date}
            {trip.return_date ? ` – ${trip.return_date}` : ""}
          </p>
        </div>
        <button
          type="button"
          onClick={onSearch}
          disabled={isLoading}
          className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {isLoading ? "Searching…" : searchResult ? "Search again" : "Search flights"}
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

      {searchResult && nonStopOffers.length > 0 && (
        <ul className="space-y-2">
          {nonStopOffers.map((offer) => (
            <li key={offer.id}>
              <div
                className={`flex items-center justify-between rounded-lg border p-3 transition ${
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
                    className="h-4 w-4"
                  />
                  <div>
                    <p className="flex items-center gap-2 font-medium text-slate-900">
                      {offer.carrier}
                      {offer.id === cheapestOfferId && (
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-800">
                          Cheapest
                        </span>
                      )}
                    </p>
                    <p className="text-sm text-slate-600">
                      {formatDateTime(offer.depart_at)} → {formatDateTime(offer.arrive_at)}
                    </p>
                  </div>
                </label>
                <div className="text-right">
                  <p className="font-semibold text-slate-900">
                    ${offer.price_usd.toFixed(2)} {offer.currency}
                  </p>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
