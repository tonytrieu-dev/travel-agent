export function RunStatusDot({ isActive }: { isActive: boolean }) {
  if (!isActive) return <span className="h-2.5 w-2.5 rounded-full bg-slate-300" />
  return (
    <span className="relative flex h-2.5 w-2.5">
      <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-indigo-400 opacity-75" />
      <span className="relative inline-flex h-2.5 w-2.5 rounded-full bg-indigo-500" />
    </span>
  )
}
