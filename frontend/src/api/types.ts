// Mirrors backend/specs/openapi.yaml — the source of truth for this contract.
// Keep field names and shapes exactly in sync with that file, not with assumptions.

export type FitnessLevel = "low" | "moderate" | "high"

export type TripStatus = "created" | "flights_searched" | "itinerary_ready"

export type BookingState =
  | "PENDING_USER_CONFIRMATION"
  | "CONFIRMED"
  | "EXECUTED"
  | "CANCELLED"
  | "EXPIRED"

export type ErrorCode =
  | "booking_not_found"
  | "trip_not_found"
  | "flight_not_found"
  | "booking_expired"
  | "invalid_transition"
  | "booking_options_unavailable"
  | "validation_error"
  | "rate_limit_exceeded"

export interface ProblemDetail {
  code: ErrorCode
  detail: string
}

export interface TripRequestCreate {
  origin: string
  destination: string
  destination_airport: string
  depart_date: string
  return_date?: string | null
  age: number
  fitness_level: FitnessLevel
  budget_usd?: number | null
}

export interface TripRequestUpdate {
  origin?: string | null
  destination?: string | null
  destination_airport?: string | null
  depart_date?: string | null
  return_date?: string | null
  age?: number | null
  fitness_level?: FitnessLevel | null
  budget_usd?: number | null
}

export interface TripRequestOut {
  id: number
  user_id: number
  origin: string
  destination: string
  destination_airport: string
  depart_date: string
  return_date?: string | null
  age?: number | null
  fitness_level?: FitnessLevel | null
  budget_usd?: number | null
  status: TripStatus
  created_at: string
}

export interface FlightOfferOut {
  id: number
  offer_index: number
  carrier: string
  price_usd: number
  currency: string
  depart_at: string
  arrive_at: string
  stops: number
  source: "live" | "cached"
}

export interface FlightSearchOut {
  offers: FlightOfferOut[]
  unavailable_reason?: string | null
}

export interface ActivityOut {
  name: string
  description: string
  intensity: string
  source_url: string
}

export interface ItineraryDayOut {
  day_number: number
  summary: string
  activities: ActivityOut[]
}

export interface ItineraryOut {
  days: ItineraryDayOut[]
}

export interface PlanReadyOut {
  status: "ready"
  itinerary: ItineraryOut
}

export interface PlanNeedsClarificationOut {
  status: "needs_clarification"
  questions: string[]
}

export type PlanOut = PlanReadyOut | PlanNeedsClarificationOut

export type AgentStepKind = "model" | "tool"

export type ExecutionEventKind = "api_call" | "db_query" | "protocol" | "hitl"

export interface AgentRunStepOut {
  seq: number
  kind: AgentStepKind
  name: string
  status: string
  duration_ms?: number | null
  input_summary?: string | null
  output_summary?: string | null
  tokens?: number | null
}

export interface AgentRunOut {
  id: number
  status: string
  model: string
  total_input_tokens: number
  total_output_tokens: number
  total_ms: number
  started_at: string
  finished_at?: string | null
  steps: AgentRunStepOut[]
}

export interface ExecutionEventOut {
  seq: number
  kind: ExecutionEventKind
  name: string
  status: string
  detail: string
  duration_ms?: number | null
  created_at: string
}

export interface ExecutionPanelOut {
  trip_request_id: number
  agent_run: AgentRunOut | null
  events: ExecutionEventOut[]
  estimated_cost_usd?: number | null
  budget_utilization_pct?: number | null
}

export interface BookingRequestCreate {
  flight_search_result_id: number
}

export interface BookingTransitionOut {
  from_state: BookingState
  to_state: BookingState
  reason: string
  actor_user_id?: number | null
  created_at: string
}

export interface BookingLogOut {
  id: number
  trip_request_id: number
  flight_search_result_id: number
  state: BookingState
  booking_reference?: string | null
  booking_options?: Record<string, unknown>[] | null
  expires_at: string
  confirmed_at?: string | null
  executed_at?: string | null
  created_at: string
  transitions: BookingTransitionOut[]
}
