import { useEffect, useRef, useState } from "react"
import { ChevronDownIcon } from "./ChevronDownIcon"

interface FilterSelectOption {
  value: string
  label: string
}

interface FilterSelectProps {
  value: string
  onChange: (value: string) => void
  options: FilterSelectOption[]
  ariaLabel: string
}

// A native <select>'s popup direction is decided by the browser (it flips upward when there's
// more room above than below), which isn't overridable from CSS. This is a plain button + panel
// instead, so the options always render below the trigger.
export function FilterSelect({ value, onChange, options, ariaLabel }: FilterSelectProps) {
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

  const selectedLabel = options.find((option) => option.value === value)?.label ?? value

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setIsOpen((open) => !open)}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        className="flex items-center gap-2 rounded-lg border border-slate-200 bg-white py-1.5 pr-2.5 pl-3 text-sm text-slate-600"
      >
        {selectedLabel}
        <ChevronDownIcon />
      </button>

      {isOpen && (
        <ul
          role="listbox"
          aria-label={ariaLabel}
          className="absolute top-full left-0 z-10 mt-1 min-w-full overflow-hidden rounded-lg border border-slate-200 bg-white py-1 text-sm shadow-lg"
        >
          {options.map((option) => (
            <li key={option.value}>
              <button
                type="button"
                role="option"
                aria-selected={option.value === value}
                onClick={() => {
                  onChange(option.value)
                  setIsOpen(false)
                }}
                className={`block w-full px-3 py-1.5 text-left whitespace-nowrap ${
                  option.value === value
                    ? "bg-indigo-50 font-medium text-indigo-700"
                    : "text-slate-600 hover:bg-slate-50"
                }`}
              >
                {option.label}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
