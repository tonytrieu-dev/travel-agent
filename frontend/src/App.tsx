import { useEffect, useState } from "react"
import { ApiError, createTrip, getTrip, planTrip, searchTripFlights, updateTrip } from "./api/client"
import type {
  FlightOfferOut,
  FlightSearchOut,
  PlanOut,
  TripRequestCreate,
  TripRequestOut,
} from "./api/types"
import { BookingModule } from "./components/BookingModule"
import { ExecutionPanel } from "./components/ExecutionPanel"
import { FlightSearch } from "./components/FlightSearch"
import { Footer } from "./components/Footer"
import { ItineraryPanel, type ClarificationAnswers } from "./components/ItineraryPanel"
import { LiveActivity } from "./components/LiveActivity"
import { Questionnaire } from "./components/Questionnaire"

function extractErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again."
}

const ACTIVE_TRIP_ID_STORAGE_KEY = "travel-agent.activeTripId"

type TabKey = "trip" | "execution"

const TABS: { key: TabKey; label: string }[] = [
  { key: "trip", label: "Plan a trip" },
  { key: "execution", label: "Agent execution history" },
]

function App() {
  const [activeTab, setActiveTab] = useState<TabKey>("trip")

  const [trip, setTrip] = useState<TripRequestOut | null>(null)
  const [isCreatingTrip, setIsCreatingTrip] = useState(false)
  const [createTripError, setCreateTripError] = useState<string | null>(null)

  const [flightSearchResult, setFlightSearchResult] = useState<FlightSearchOut | null>(null)
  const [isSearchingFlights, setIsSearchingFlights] = useState(false)
  const [flightSearchError, setFlightSearchError] = useState<string | null>(null)
  const [selectedOffer, setSelectedOffer] = useState<FlightOfferOut | null>(null)

  const [planResult, setPlanResult] = useState<PlanOut | null>(null)
  const [isPlanning, setIsPlanning] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)

  const isRunActive = isSearchingFlights || isPlanning

  // Restore the active trip after a hard refresh so the execution history survives — React state
  // alone would lose the trip id and leave the ExecutionPanel with nothing to re-fetch.
  useEffect(() => {
    const storedTripId = localStorage.getItem(ACTIVE_TRIP_ID_STORAGE_KEY)
    if (!storedTripId) return
    getTrip(Number(storedTripId))
      .then(setTrip)
      .catch(() => localStorage.removeItem(ACTIVE_TRIP_ID_STORAGE_KEY))
  }, [])

  const handleCreateTrip = async (tripRequestCreate: TripRequestCreate) => {
    setIsCreatingTrip(true)
    setCreateTripError(null)
    try {
      const createdTrip = await createTrip(tripRequestCreate)
      setTrip(createdTrip)
      localStorage.setItem(ACTIVE_TRIP_ID_STORAGE_KEY, String(createdTrip.id))
    } catch (error) {
      setCreateTripError(extractErrorMessage(error))
    } finally {
      setIsCreatingTrip(false)
    }
  }

  const handleSearchFlights = async () => {
    if (!trip) return
    setIsSearchingFlights(true)
    setFlightSearchError(null)
    setSelectedOffer(null)
    try {
      setFlightSearchResult(await searchTripFlights(trip.id))
    } catch (error) {
      setFlightSearchError(extractErrorMessage(error))
    } finally {
      setIsSearchingFlights(false)
    }
  }

  // Drops the current trip entirely so the Questionnaire renders fresh — the only way back to
  // the input form once a trip exists, for a traveler who wants to search a different flight.
  const handleStartNewSearch = () => {
    localStorage.removeItem(ACTIVE_TRIP_ID_STORAGE_KEY)
    setTrip(null)
    clearFlightAndBookingState()
    setPlanResult(null)
    setPlanError(null)
  }

  const handleRequestPlan = async () => {
    if (!trip) return
    setIsPlanning(true)
    setPlanError(null)
    try {
      const planOutcome = await planTrip(trip.id)
      setPlanResult(planOutcome)
      if (planOutcome.status === "needs_clarification") clearFlightAndBookingState()
    } catch (error) {
      setPlanError(extractErrorMessage(error))
    } finally {
      setIsPlanning(false)
    }
  }

  // Restoring `trip` only refetches the trip row itself; rehydrate what it says already exists.
  useEffect(() => {
    if (!trip) return
    if (trip.status === "flights_searched" || trip.status === "itinerary_ready") {
      handleSearchFlights()
    }
    if (trip.status === "itinerary_ready") {
      handleRequestPlan()
    }
  }, [trip?.id])

  const handleAnswerClarification = async (answers: ClarificationAnswers) => {
    if (!trip) return
    setIsPlanning(true)
    setPlanError(null)
    try {
      const updatedTrip = await updateTrip(trip.id, answers)
      setTrip(updatedTrip)
      localStorage.setItem(ACTIVE_TRIP_ID_STORAGE_KEY, String(updatedTrip.id))
      const planOutcome = await planTrip(trip.id)
      setPlanResult(planOutcome)
      if (planOutcome.status === "needs_clarification") clearFlightAndBookingState()
    } catch (error) {
      setPlanError(extractErrorMessage(error))
    } finally {
      setIsPlanning(false)
    }
  }

  const handleSearchAgain = () => {
    setSelectedOffer(null)
  }

  // When a re-plan comes back needing clarification, the trip has effectively changed — drop any
  // flight results, selection, and (via BookingModule's remount key) booking state so the UI never
  // shows offers or a booking tied to the now-stale trip.
  const clearFlightAndBookingState = () => {
    setFlightSearchResult(null)
    setSelectedOffer(null)
    setFlightSearchError(null)
  }

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900 md:flex-row">
      <aside className="flex w-full shrink-0 flex-col border-b border-slate-200 bg-white md:sticky md:top-0 md:h-screen md:w-80 md:border-b-0 md:border-r">
        <div className="border-b border-slate-200 px-6 py-7">
          <h1 className="cursor-default text-3xl font-bold tracking-tight text-slate-900">
            Travel Agent
          </h1>
          <p className="mt-3 text-sm leading-6 text-slate-500">
            AI trip planner with human-in-the-loop booking
          </p>
        </div>

        <nav className="space-y-2 p-4" aria-label="Primary">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                aria-current={isActive ? "page" : undefined}
                className={`flex min-h-11 w-full items-center justify-between rounded-lg border px-4 py-2.5 text-left text-sm font-medium transition ${
                  isActive
                    ? "border-indigo-100 bg-indigo-50 text-indigo-700"
                    : "border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                {tab.label}
                {tab.key === "execution" && isRunActive && (
                  <span className="relative flex h-2 w-2" aria-label="run in progress">
                    <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
                    <span className="relative inline-flex h-2 w-2 rounded-full bg-indigo-500" />
                  </span>
                )}
              </button>
            )
          })}
        </nav>

        <div className="flex-1" />
      </aside>

      <div className="flex min-h-screen flex-1 flex-col">
        <main className="mx-auto w-full max-w-3xl flex-1 space-y-6 px-6 py-8">
          {activeTab === "trip" && (
            <>
              {!trip && (
                <Questionnaire
                  onSubmit={handleCreateTrip}
                  isSubmitting={isCreatingTrip}
                  errorMessage={createTripError}
                />
              )}

              {trip && (
                <>
                  <FlightSearch
                    trip={trip}
                    searchResult={flightSearchResult}
                    isLoading={isSearchingFlights}
                    errorMessage={flightSearchError}
                    selectedOfferId={selectedOffer?.id ?? null}
                    onSearchFlights={handleSearchFlights}
                    onSearchNewFlight={handleStartNewSearch}
                    onSelectOffer={setSelectedOffer}
                  />

                  <BookingModule
                    key={selectedOffer ? selectedOffer.id : "none"}
                    trip={trip}
                    selectedOffer={selectedOffer}
                    onSearchAgain={handleSearchAgain}
                  />

                  <ItineraryPanel
                    trip={trip}
                    planResult={planResult}
                    isLoading={isPlanning}
                    errorMessage={planError}
                    onRequestPlan={handleRequestPlan}
                    onAnswerClarification={handleAnswerClarification}
                  />

                  <LiveActivity tripId={trip.id} isRunActive={isRunActive} />
                </>
              )}
            </>
          )}

          {activeTab === "execution" &&
            (trip ? (
              <ExecutionPanel tripId={trip.id} isRunActive={isRunActive} />
            ) : (
              <p className="rounded-xl border border-dashed border-slate-300 bg-white p-6 text-sm text-slate-500">
                Create a trip first — the agent's execution trace will appear here once it runs.
              </p>
            ))}
        </main>

        <Footer />
      </div>
    </div>
  )
}

export default App
