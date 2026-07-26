import { useState, type ChangeEvent, type FormEvent, type ReactNode } from "react"
import type { FitnessLevel, TripRequestCreate } from "../api/types"
import { ChevronDownIcon } from "./ChevronDownIcon"
import { AIRPORTS } from "../data/airports"

const AIRPORT_OPTIONS_ID = "airport-options"

// Accepts either a bare code ("JFK") or a picked suggestion ("Tokyo, Japan (NRT)") by matching
// the trailing 3-letter code either way.
function normalizeAirportCode(rawValue: string): string | null {
  const match = rawValue.trim().match(/([A-Za-z]{3})\)?\s*$/)
  return match ? match[1].toUpperCase() : null
}

function deriveDestination(airportCode: string): string {
  const airport = AIRPORTS.find((candidate) => candidate.code === airportCode)
  return airport ? `${airport.city}, ${airport.country}` : airportCode
}

// Every label/control in this form shares one set of classes so spacing and focus styling stay
// identical across text, number, date, and select fields.
const FIELD_LABEL = "flex flex-col gap-2 text-sm font-medium text-slate-700"
const FIELD_BASE =
  "rounded-md border border-slate-300 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
const FIELD_INPUT = `${FIELD_BASE} px-3`
// pl/pr split rather than px, so pr-10 can reserve room for the chevron without fighting px-3.
const FIELD_SELECT = `${FIELD_BASE} w-full appearance-none pl-3 pr-10`

interface SelectFieldProps {
  label: string
  value: string
  onChange: (event: ChangeEvent<HTMLSelectElement>) => void
  required?: boolean
  children: ReactNode
}

function SelectField({ label, value, onChange, required, children }: SelectFieldProps) {
  return (
    <label className={FIELD_LABEL}>
      {label}
      <div className="relative">
        <select value={value} onChange={onChange} required={required} className={FIELD_SELECT}>
          {children}
        </select>
        <ChevronDownIcon className="pointer-events-none absolute top-1/2 right-3 h-4 w-4 -translate-y-1/2 text-slate-400" />
      </div>
    </label>
  )
}

interface QuestionnaireProps {
  onSubmit: (tripRequestCreate: TripRequestCreate) => Promise<void>
  isSubmitting: boolean
  errorMessage: string | null
}

export function Questionnaire({ onSubmit, isSubmitting, errorMessage }: QuestionnaireProps) {
  const [tripType, setTripType] = useState<"round-trip" | "one-way">("round-trip")
  const [origin, setOrigin] = useState("")
  const [destinationAirport, setDestinationAirport] = useState("")
  const [departDate, setDepartDate] = useState("")
  const [returnDate, setReturnDate] = useState("")
  const [age, setAge] = useState("")
  const [fitnessLevel, setFitnessLevel] = useState<FitnessLevel | "">("")
  const [validationMessage, setValidationMessage] = useState<string | null>(null)

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setValidationMessage(null)

    const normalizedOrigin = normalizeAirportCode(origin)
    const normalizedDestinationAirport = normalizeAirportCode(destinationAirport)

    if (!normalizedOrigin) {
      setValidationMessage("The departure airport must be a valid 3-letter airport code, like JFK.")
      return
    }
    if (!normalizedDestinationAirport) {
      setValidationMessage("The arrival airport must be a valid 3-letter airport code, like NRT.")
      return
    }
    if (!departDate) {
      setValidationMessage("Departure date is required.")
      return
    }
    if (tripType === "round-trip" && !returnDate) {
      setValidationMessage("Return date is required for a round-trip.")
      return
    }
    if (!age) {
      setValidationMessage("Age is required.")
      return
    }
    if (!fitnessLevel) {
      setValidationMessage("Fitness level is required.")
      return
    }

    await onSubmit({
      origin: normalizedOrigin,
      destination: deriveDestination(normalizedDestinationAirport),
      destination_airport: normalizedDestinationAirport,
      depart_date: departDate,
      return_date: returnDate || null,
      age: Number(age),
      fitness_level: fitnessLevel,
    })
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="space-y-6 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <h2 className="text-lg font-semibold text-slate-900">Plan a trip</h2>

      <SelectField
        label="Trip type"
        value={tripType}
        onChange={(event) => {
          const value = event.target.value as "round-trip" | "one-way"
          setTripType(value)
          if (value === "one-way") setReturnDate("")
        }}
      >
        <option value="round-trip">Round-trip</option>
        <option value="one-way">One-way</option>
      </SelectField>

      <datalist id={AIRPORT_OPTIONS_ID}>
        {AIRPORTS.map((airport) => (
          <option key={airport.code} value={`${airport.city}, ${airport.country} (${airport.code})`} />
        ))}
      </datalist>

      <div className="grid grid-cols-2 gap-5">
        <label className={FIELD_LABEL}>
          Depart
          <input
            type="text"
            value={origin}
            onChange={(event) => setOrigin(event.target.value)}
            list={AIRPORT_OPTIONS_ID}
            placeholder="JFK or New York"
            required
            className={FIELD_INPUT}
          />
        </label>

        <label className={FIELD_LABEL}>
          Arrive
          <input
            type="text"
            value={destinationAirport}
            onChange={(event) => setDestinationAirport(event.target.value)}
            list={AIRPORT_OPTIONS_ID}
            placeholder="NRT or Tokyo"
            required
            className={FIELD_INPUT}
          />
        </label>
      </div>

      <div className={`grid gap-5 ${tripType === "one-way" ? "grid-cols-1" : "grid-cols-2"}`}>
        <label className={FIELD_LABEL}>
          Depart date
          <input
            type="date"
            value={departDate}
            onChange={(event) => setDepartDate(event.target.value)}
            required
            className={FIELD_INPUT}
          />
        </label>

        {tripType === "round-trip" && (
          <label className={FIELD_LABEL}>
            Return date
            <input
              type="date"
              value={returnDate}
              onChange={(event) => setReturnDate(event.target.value)}
              required
              className={FIELD_INPUT}
            />
          </label>
        )}
      </div>

      <div className="space-y-3 border-t border-slate-200 pt-5">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Itinerary preferences</h3>
          <p className="mt-1 text-xs text-slate-500">
            Age and fitness level tailor which activities show up in your itinerary.
          </p>
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
          <label className={FIELD_LABEL}>
            Age
            <input
              type="number"
              min={0}
              max={130}
              value={age}
              onChange={(event) => setAge(event.target.value)}
              required
              className={`${FIELD_INPUT} [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none`}
            />
          </label>

          <SelectField
            label="Fitness level"
            value={fitnessLevel}
            onChange={(event) => setFitnessLevel(event.target.value as FitnessLevel | "")}
            required
          >
            <option value="">—</option>
            <option value="low">Low</option>
            <option value="moderate">Moderate</option>
            <option value="high">High</option>
          </SelectField>
        </div>
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
