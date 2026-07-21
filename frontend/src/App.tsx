import { useState } from "react"
import { ApiError, createTrip, planTrip, searchTripFlights, updateTrip } from "./api/client"
import type {
  FlightOfferOut,
  FlightSearchOut,
  PlanOut,
  TripRequestCreate,
  TripRequestOut,
} from "./api/types"
import { BookingModule } from "./components/BookingModule"
import { ChatPanel } from "./components/ChatPanel"
import { ExecutionPanel } from "./components/ExecutionPanel"
import { FlightSearch } from "./components/FlightSearch"
import { Footer } from "./components/Footer"
import { ItineraryPanel, type ClarificationAnswers } from "./components/ItineraryPanel"
import { LiveActivity } from "./components/LiveActivity"
import { Questionnaire } from "./components/Questionnaire"

function extractErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again."
}

type TabKey = "trip" | "chat" | "execution"

const TABS: { key: TabKey; label: string; icon: string }[] = [
  { key: "trip", label: "Plan a trip", icon: "🧭" },
  { key: "chat", label: "Chat", icon: "💬" },
  { key: "execution", label: "Agent execution", icon: "📊" },
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

  const handleCreateTrip = async (tripRequestCreate: TripRequestCreate) => {
    setIsCreatingTrip(true)
    setCreateTripError(null)
    try {
      setTrip(await createTrip(tripRequestCreate))
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

  const handleRequestPlan = async () => {
    if (!trip) return
    setIsPlanning(true)
    setPlanError(null)
    try {
      setPlanResult(await planTrip(trip.id))
    } catch (error) {
      setPlanError(extractErrorMessage(error))
    } finally {
      setIsPlanning(false)
    }
  }

  const handleAnswerClarification = async (answers: ClarificationAnswers) => {
    if (!trip) return
    setIsPlanning(true)
    setPlanError(null)
    try {
      const updatedTrip = await updateTrip(trip.id, answers)
      setTrip(updatedTrip)
      setPlanResult(await planTrip(trip.id))
    } catch (error) {
      setPlanError(extractErrorMessage(error))
    } finally {
      setIsPlanning(false)
    }
  }

  const handleSearchAgain = () => {
    setSelectedOffer(null)
  }

  return (
    <div className="flex min-h-screen bg-slate-50 text-slate-900">
      <aside className="sticky top-0 flex h-screen w-64 shrink-0 flex-col border-r border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-5 py-5">
          <p className="text-base font-semibold text-slate-900">Travel Agent</p>
          <p className="text-xs text-slate-500">AI trip planner with human-in-the-loop booking</p>
        </div>

        <nav className="flex-1 space-y-1 p-3" aria-label="Primary">
          {TABS.map((tab) => {
            const isActive = activeTab === tab.key
            return (
              <button
                key={tab.key}
                type="button"
                onClick={() => setActiveTab(tab.key)}
                aria-current={isActive ? "page" : undefined}
                className={`flex w-full items-center justify-between rounded-lg px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? "bg-indigo-50 text-indigo-700"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                <span className="flex items-center gap-2.5">
                  <span aria-hidden="true">{tab.icon}</span>
                  {tab.label}
                </span>
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

        {trip && (
          <div className="border-t border-slate-200 px-5 py-4 text-xs text-slate-500">
            <p className="font-medium text-slate-700">Trip #{trip.id}</p>
            <p>
              {trip.origin} → {trip.destination_airport}
            </p>
            <p>
              {trip.depart_date}
              {trip.return_date ? ` – ${trip.return_date}` : ""}
            </p>
          </div>
        )}
      </aside>

      <div className="flex min-h-screen flex-1 flex-col">
        <main className="mx-auto w-full max-w-3xl flex-1 px-6 py-8">
          {activeTab === "trip" && (
            <div className="space-y-6">
              {!trip && (
                <Questionnaire
                  onSubmit={handleCreateTrip}
                  isSubmitting={isCreatingTrip}
                  errorMessage={createTripError}
                />
              )}

              {trip && (
                <>
                  <LiveActivity tripId={trip.id} isRunActive={isRunActive} />

                  <FlightSearch
                    searchResult={flightSearchResult}
                    isLoading={isSearchingFlights}
                    errorMessage={flightSearchError}
                    selectedOfferId={selectedOffer?.id ?? null}
                    onSearch={handleSearchFlights}
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
                </>
              )}
            </div>
          )}

          {activeTab === "chat" && <ChatPanel onGoToForm={() => setActiveTab("trip")} />}

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
