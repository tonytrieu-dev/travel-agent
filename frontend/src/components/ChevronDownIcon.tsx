interface ChevronDownIconProps {
  className?: string
}

export function ChevronDownIcon({ className = "h-4 w-4 text-slate-400" }: ChevronDownIconProps) {
  return (
    <svg aria-hidden="true" viewBox="0 0 20 20" fill="none" className={className}>
      <path
        d="M5.5 7.5L10 12L14.5 7.5"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  )
}
