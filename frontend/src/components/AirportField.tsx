import { useEffect, useRef, useState } from "react"
import { AIRPORTS, type Airport } from "../data/airports"

const MAX_SUGGESTIONS = 8

function matchAirports(query: string): Airport[] {
  const normalizedQuery = query.trim().toLowerCase()
  if (!normalizedQuery) return []
  return AIRPORTS.filter(
    (airport) =>
      airport.code.toLowerCase().startsWith(normalizedQuery) ||
      airport.city.toLowerCase().includes(normalizedQuery) ||
      airport.country.toLowerCase().includes(normalizedQuery),
  ).slice(0, MAX_SUGGESTIONS)
}

interface AirportFieldProps {
  label: string
  value: string
  onChange: (value: string) => void
  placeholder: string
}

export function AirportField({ label, value, onChange, placeholder }: AirportFieldProps) {
  const [isOpen, setIsOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!isOpen) return
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setIsOpen(false)
    }
    document.addEventListener("mousedown", closeOnOutsideClick)
    return () => document.removeEventListener("mousedown", closeOnOutsideClick)
  }, [isOpen])

  const suggestions = matchAirports(value)

  return (
    <div ref={containerRef} className="relative">
      <label className="flex flex-col gap-2 text-sm font-medium text-slate-700">
        {label}
        <input
          type="text"
          value={value}
          onChange={(event) => {
            onChange(event.target.value)
            setIsOpen(true)
          }}
          onFocus={() => setIsOpen(true)}
          onKeyDown={(event) => {
            if (event.key === "Escape") setIsOpen(false)
          }}
          placeholder={placeholder}
          required
          autoComplete="off"
          role="combobox"
          aria-expanded={isOpen}
          aria-autocomplete="list"
          className="w-full rounded-md border border-slate-300 px-3 py-2 text-slate-900 focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
        />
      </label>

      {isOpen && value.trim() && (
        <ul
          role="listbox"
          className="absolute top-full left-0 z-10 mt-1 max-h-60 w-full overflow-y-auto rounded-md border border-slate-200 bg-white text-sm shadow-lg"
        >
          {suggestions.length > 0 ? (
            suggestions.map((airport) => (
              <li key={airport.code}>
                <button
                  type="button"
                  role="option"
                  aria-selected={false}
                  onClick={() => {
                    onChange(`${airport.city}, ${airport.country} (${airport.code})`)
                    setIsOpen(false)
                  }}
                  className="block w-full px-3 py-2 text-left text-slate-700 hover:bg-slate-50"
                >
                  {airport.city}, {airport.country} ({airport.code})
                </button>
              </li>
            ))
          ) : (
            <li className="px-3 py-2 text-slate-500">
              No match in our list — you can still enter the exact 3-letter airport code.
            </li>
          )}
        </ul>
      )}
    </div>
  )
}
