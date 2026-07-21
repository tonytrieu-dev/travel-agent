interface ChatPanelProps {
  onGoToForm: () => void
}

// Conversational mode has no backend endpoint yet (the agent is driven by the structured trip
// form). This is an honest preview of the interface, not a stub that fabricates agent replies.
export function ChatPanel({ onGoToForm }: ChatPanelProps) {
  return (
    <section className="flex h-full flex-col">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Chat with the agent</h2>
        <p className="text-sm text-slate-500">
          Plan and refine your trip in natural language.
        </p>
      </div>

      <div className="mt-4 flex-1 space-y-3 rounded-xl border border-slate-200 bg-white p-4">
        <div className="max-w-md rounded-2xl rounded-tl-sm bg-slate-100 px-4 py-2.5 text-sm text-slate-700">
          Hi! I can find cheap flights and build an itinerary tailored to your age and fitness
          level. Conversational planning is coming soon — for now, use the structured form and I'll
          ask follow-up questions when I need them.
        </div>
      </div>

      <div className="mt-3">
        <div className="flex items-center gap-2 rounded-xl border border-slate-200 bg-slate-50 px-3 py-2">
          <input
            type="text"
            disabled
            placeholder="Conversational planning is coming soon…"
            aria-label="Message the agent (coming soon)"
            className="flex-1 bg-transparent text-sm text-slate-500 outline-none placeholder:text-slate-400"
          />
          <button
            type="button"
            disabled
            className="cursor-not-allowed rounded-md bg-slate-300 px-3 py-1.5 text-sm font-medium text-white"
          >
            Send
          </button>
        </div>
        <p className="mt-2 text-xs text-slate-500">
          Want to plan a trip now?{" "}
          <button
            type="button"
            onClick={onGoToForm}
            className="font-medium text-indigo-600 underline-offset-2 hover:underline"
          >
            Use the trip form
          </button>
          .
        </p>
      </div>
    </section>
  )
}
