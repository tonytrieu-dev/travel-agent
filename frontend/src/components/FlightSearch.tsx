import type { FlightOfferOut, FlightSearchOut } from "../api/types"

interface FlightSearchProps {
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
  searchResult,
  isLoading,
  errorMessage,
  selectedOfferId,
  onSearch,
  onSelectOffer,
}: FlightSearchProps) {
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">Flights</h2>
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

      {searchResult && searchResult.offers.length > 0 && (
        <ul className="space-y-2">
          {searchResult.offers.map((offer) => (
            <li key={offer.id}>
              <label
                className={`flex cursor-pointer items-center justify-between rounded-lg border p-3 transition ${
                  selectedOfferId === offer.id
                    ? "border-indigo-500 bg-indigo-50"
                    : "border-slate-200 hover:border-slate-300"
                }`}
              >
                <div className="flex items-center gap-3">
                  <input
                    type="radio"
                    name="flight-offer"
                    checked={selectedOfferId === offer.id}
                    onChange={() => onSelectOffer(offer)}
                    className="h-4 w-4"
                  />
                  <div>
                    <p className="font-medium text-slate-900">
                      {offer.carrier} · {offer.stops === 0 ? "Nonstop" : `${offer.stops} stop(s)`}
                    </p>
                    <p className="text-sm text-slate-600">
                      {formatDateTime(offer.depart_at)} → {formatDateTime(offer.arrive_at)}
                    </p>
                  </div>
                </div>
                <div className="text-right">
                  <p className="font-semibold text-slate-900">
                    ${offer.price_usd.toFixed(2)} {offer.currency}
                  </p>
                  <p className="text-xs uppercase text-slate-400">{offer.source}</p>
                </div>
              </label>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
