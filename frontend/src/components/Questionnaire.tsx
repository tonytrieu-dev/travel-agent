import { useState, type FormEvent } from "react"
import type { FitnessLevel, TripRequestCreate } from "../api/types"

const IATA_CODE_PATTERN = /^[A-Z]{3}$/

// Budget is presented as a select per the UI spec, but the backend field (budget_usd) is a
// plain number — each band maps to a representative dollar figure sent to the API.
const BUDGET_BANDS: { label: string; valueUsd: number | null }[] = [
  { label: "No preference", valueUsd: null },
  { label: "Under $1,000", valueUsd: 1000 },
  { label: "$1,000 - $2,500", valueUsd: 2500 },
  { label: "$2,500 - $5,000", valueUsd: 5000 },
  { label: "$5,000 - $10,000", valueUsd: 10000 },
  { label: "$10,000+", valueUsd: 15000 },
]

interface QuestionnaireProps {
  onSubmit: (tripRequestCreate: TripRequestCreate) => Promise<void>
  isSubmitting: boolean
  errorMessage: string | null
}

export function Questionnaire({ onSubmit, isSubmitting, errorMessage }: QuestionnaireProps) {
  const [origin, setOrigin] = useState("")
  const [destination, setDestination] = useState("")
  const [destinationAirport, setDestinationAirport] = useState("")
  const [departDate, setDepartDate] = useState("")
  const [returnDate, setReturnDate] = useState("")
  const [age, setAge] = useState("")
  const [fitnessLevel, setFitnessLevel] = useState<FitnessLevel | "">("")
  const [budgetLabel, setBudgetLabel] = useState(BUDGET_BANDS[0].label)
  const [validationMessage, setValidationMessage] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setValidationMessage(null)

    const normalizedOrigin = origin.trim().toUpperCase()
    const normalizedDestinationAirport = destinationAirport.trim().toUpperCase()

    if (!IATA_CODE_PATTERN.test(normalizedOrigin)) {
      setValidationMessage("Origin must be a 3-letter IATA airport code, e.g. JFK.")
      return
    }
    if (!IATA_CODE_PATTERN.test(normalizedDestinationAirport)) {
      setValidationMessage("Destination airport must be a 3-letter IATA airport code, e.g. NRT.")
      return
    }
    if (!destination.trim()) {
      setValidationMessage("Destination is required.")
      return
    }
    if (!departDate) {
      setValidationMessage("Departure date is required.")
      return
    }

    const selectedBudgetBand = BUDGET_BANDS.find((band) => band.label === budgetLabel)

    await onSubmit({
      origin: normalizedOrigin,
      destination: destination.trim(),
      destination_airport: normalizedDestinationAirport,
      depart_date: departDate,
      return_date: returnDate || null,
      age: age ? Number(age) : null,
      fitness_level: fitnessLevel || null,
      budget_usd: selectedBudgetBand?.valueUsd ?? null,
    })
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5 rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold text-slate-900">Plan a trip</h2>

      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Origin airport (IATA)
          <input
            type="text"
            value={origin}
            onChange={(event) => setOrigin(event.target.value.toUpperCase())}
            maxLength={3}
            placeholder="JFK"
            required
            className="rounded-md border border-slate-300 px-3 py-2 uppercase tracking-widest text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Destination airport (IATA)
          <input
            type="text"
            value={destinationAirport}
            onChange={(event) => setDestinationAirport(event.target.value.toUpperCase())}
            maxLength={3}
            placeholder="NRT"
            required
            className="rounded-md border border-slate-300 px-3 py-2 uppercase tracking-widest text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
      </div>

      <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
        Destination
        <input
          type="text"
          value={destination}
          onChange={(event) => setDestination(event.target.value)}
          placeholder="Tokyo, Japan"
          required
          className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </label>

      <div className="grid grid-cols-2 gap-4">
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Depart date
          <input
            type="date"
            value={departDate}
            onChange={(event) => setDepartDate(event.target.value)}
            required
            className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Return date (optional)
          <input
            type="date"
            value={returnDate}
            onChange={(event) => setReturnDate(event.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>
      </div>

      <div className="grid grid-cols-3 gap-4">
        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Age (optional)
          <input
            type="number"
            min={0}
            max={130}
            value={age}
            onChange={(event) => setAge(event.target.value)}
            placeholder="—"
            className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          />
        </label>

        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Fitness level (optional)
          <select
            value={fitnessLevel}
            onChange={(event) => setFitnessLevel(event.target.value as FitnessLevel | "")}
            className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            <option value="">—</option>
            <option value="low">Low</option>
            <option value="moderate">Moderate</option>
            <option value="high">High</option>
          </select>
        </label>

        <label className="flex flex-col gap-1 text-sm font-medium text-slate-700">
          Budget (optional)
          <select
            value={budgetLabel}
            onChange={(event) => setBudgetLabel(event.target.value)}
            className="rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
          >
            {BUDGET_BANDS.map((band) => (
              <option key={band.label} value={band.label}>
                {band.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {(validationMessage || errorMessage) && (
        <p role="alert" className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {validationMessage ?? errorMessage}
        </p>
      )}

      <button
        type="submit"
        disabled={isSubmitting}
        className="w-full rounded-md bg-indigo-600 px-4 py-2.5 font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:bg-slate-300"
      >
        {isSubmitting ? "Creating trip…" : "Create trip"}
      </button>
    </form>
  )
}
