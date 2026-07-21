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
import { ExecutionPanel } from "./components/ExecutionPanel"
import { FlightSearch } from "./components/FlightSearch"
import { Footer } from "./components/Footer"
import { ItineraryPanel, type ClarificationAnswers } from "./components/ItineraryPanel"
import { Questionnaire } from "./components/Questionnaire"

function extractErrorMessage(error: unknown): string {
  return error instanceof ApiError ? error.message : "Something went wrong. Please try again."
}

function App() {
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

  const [isExecutionPanelOpen, setIsExecutionPanelOpen] = useState(false)

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
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-6 py-4">
          <h1 className="text-xl font-semibold text-slate-900">Travel Agent</h1>
          {trip && (
            <button
              type="button"
              onClick={() => setIsExecutionPanelOpen(true)}
              className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
            >
              Agent execution
            </button>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-3xl space-y-6 px-6 py-8">
        {!trip && (
          <Questionnaire
            onSubmit={handleCreateTrip}
            isSubmitting={isCreatingTrip}
            errorMessage={createTripError}
          />
        )}

        {trip && (
          <>
            <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm text-slate-600 shadow-sm">
              Trip #{trip.id}: {trip.origin} → {trip.destination} ({trip.destination_airport}),{" "}
              {trip.depart_date}
              {trip.return_date ? ` – ${trip.return_date}` : ""}
            </div>

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
      </main>

      <Footer />

      {trip && (
        <ExecutionPanel
          tripId={trip.id}
          isOpen={isExecutionPanelOpen}
          onClose={() => setIsExecutionPanelOpen(false)}
        />
      )}
    </div>
  )
}

export default App
