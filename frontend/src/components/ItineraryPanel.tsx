import { useState, type FormEvent } from "react"
import type { PlanOut, TripRequestOut } from "../api/types"

export interface ClarificationAnswers {
  origin: string | null
  destination: string | null
  destination_airport: string | null
}

interface ItineraryPanelProps {
  trip: TripRequestOut
  planResult: PlanOut | null
  isLoading: boolean
  errorMessage: string | null
  onRequestPlan: () => void
  onAnswerClarification: (answers: ClarificationAnswers) => void
}

// Age, fitness level, and dates are mandatory at trip intake (see AGENTS.md), so they can never
// be the reason the planner asks a clarifying question — only a genuinely ambiguous origin,
// destination, or destination_airport can (e.g. "Paris" meaning France or Texas). This form's
// fields match that real trigger surface, pre-filled with the trip's current values.
function ClarificationForm({
  trip,
  questions,
  onAnswerClarification,
}: {
  trip: TripRequestOut
  questions: string[]
  onAnswerClarification: (answers: ClarificationAnswers) => void
}) {
  const [origin, setOrigin] = useState(trip.origin)
  const [destination, setDestination] = useState(trip.destination)
  const [destinationAirport, setDestinationAirport] = useState(trip.destination_airport)

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault()
    onAnswerClarification({
      origin: origin.trim().toUpperCase() || null,
      destination: destination.trim() || null,
      destination_airport: destinationAirport.trim().toUpperCase() || null,
    })
  }

  return (
    <div className="rounded-xl border border-indigo-100 bg-indigo-50/60 p-6">
      <h3 className="font-semibold text-slate-900">The planner needs a bit more information</h3>
      <ul className="mt-2 list-inside list-disc space-y-1 text-sm text-slate-600">
        {questions.map((question) => (
          <li key={question}>{question}</li>
        ))}
      </ul>

      <form onSubmit={handleSubmit} className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Origin airport
          <input
            type="text"
            value={origin}
            onChange={(event) => setOrigin(event.target.value.toUpperCase())}
            maxLength={3}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 uppercase tracking-widest text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Destination
          <input
            type="text"
            value={destination}
            onChange={(event) => setDestination(event.target.value)}
            placeholder="Be specific, e.g. Paris, France"
            className="rounded-md border border-slate-300 bg-white px-3 py-2 text-slate-900 placeholder:text-slate-400 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Destination airport
          <input
            type="text"
            value={destinationAirport}
            onChange={(event) => setDestinationAirport(event.target.value.toUpperCase())}
            maxLength={3}
            className="rounded-md border border-slate-300 bg-white px-3 py-2 uppercase tracking-widest text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>

        <button
          type="submit"
          className="col-span-1 mt-1 rounded-md bg-indigo-600 px-4 py-2 font-semibold text-white transition hover:bg-indigo-700 sm:col-span-3"
        >
          Submit answers and re-plan
        </button>
      </form>
    </div>
  )
}

export function ItineraryPanel({
  trip,
  planResult,
  isLoading,
  errorMessage,
  onRequestPlan,
  onAnswerClarification,
}: ItineraryPanelProps) {
  return (
    <section className="space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-slate-900">Itinerary</h2>
        {(!planResult || planResult.status === "needs_clarification") && (
          <button
            type="button"
            onClick={onRequestPlan}
            disabled={isLoading}
            className="rounded-md bg-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
          >
            {isLoading ? "Planning…" : planResult ? "Try planning again" : "Plan itinerary"}
          </button>
        )}
      </div>

      {errorMessage && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {errorMessage}
        </p>
      )}

      {planResult?.status === "needs_clarification" && (
        <ClarificationForm
          trip={trip}
          questions={planResult.questions}
          onAnswerClarification={onAnswerClarification}
        />
      )}

      {planResult?.status === "ready" && (
        <div className="space-y-4">
          {planResult.itinerary.days.map((day) => (
            <div key={day.day_number} className="rounded-lg border border-slate-200 p-4">
              <h3 className="font-semibold text-slate-900">Day {day.day_number}</h3>
              <p className="mt-1 text-sm text-slate-600">{day.summary}</p>
              <ul className="mt-3 space-y-2">
                {day.activities.map((activity) => (
                  <li key={activity.name} className="rounded-md bg-slate-50 p-3">
                    <span className="font-medium text-slate-900">{activity.name}</span>
                    <p className="mt-1 text-sm text-slate-600">{activity.description}</p>
                    <a
                      href={activity.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="mt-1 inline-block text-xs text-indigo-600 hover:underline"
                    >
                      Source
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {!planResult && !isLoading && (
        <p className="text-sm text-slate-500">Run the planner to generate a day-by-day itinerary.</p>
      )}
    </section>
  )
}
