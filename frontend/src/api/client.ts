import type {
  BookingLogOut,
  BookingRequestCreate,
  ExecutionPanelOut,
  FlightSearchOut,
  PlanOut,
  ProblemDetail,
  TripRequestCreate,
  TripRequestOut,
  TripSnapshotOut,
  TripRequestUpdate,
} from "./types"

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api"

export class ApiError extends Error {
  code: ProblemDetail["code"]
  status: number

  constructor(problemDetail: ProblemDetail, status: number) {
    super(problemDetail.detail)
    this.code = problemDetail.code
    this.status = status
  }
}

async function request<TResponse>(path: string, options?: RequestInit): Promise<TResponse> {
  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers,
    },
  })

  if (!response.ok) {
    const problemDetail = (await response.json()) as ProblemDetail
    throw new ApiError(problemDetail, response.status)
  }

  return (await response.json()) as TResponse
}

export function createTrip(tripRequestCreate: TripRequestCreate): Promise<TripRequestOut> {
  return request<TripRequestOut>("/trips", {
    method: "POST",
    body: JSON.stringify(tripRequestCreate),
  })
}

const tripSnapshotRequests = new Map<number, Promise<TripSnapshotOut>>()

export function getTripSnapshot(tripId: number): Promise<TripSnapshotOut> {
  const existingRequest = tripSnapshotRequests.get(tripId)
  if (existingRequest) return existingRequest
  const snapshotRequest = request<TripSnapshotOut>(`/trips/${tripId}/snapshot`).finally(() =>
    tripSnapshotRequests.delete(tripId),
  )
  tripSnapshotRequests.set(tripId, snapshotRequest)
  return snapshotRequest
}

export function updateTrip(
  tripId: number,
  tripRequestUpdate: TripRequestUpdate,
): Promise<TripRequestOut> {
  return request<TripRequestOut>(`/trips/${tripId}`, {
    method: "PATCH",
    body: JSON.stringify(tripRequestUpdate),
  })
}

export function searchTripFlights(tripId: number): Promise<FlightSearchOut> {
  return request<FlightSearchOut>(`/trips/${tripId}/flights/search`, {
    method: "POST",
  })
}

export function planTrip(tripId: number): Promise<PlanOut> {
  return request<PlanOut>(`/trips/${tripId}/plan`, {
    method: "POST",
  })
}

export function getTripExecution(tripId: number): Promise<ExecutionPanelOut> {
  return request<ExecutionPanelOut>(`/trips/${tripId}/execution`)
}

export function requestBooking(
  tripId: number,
  bookingRequestCreate: BookingRequestCreate,
): Promise<BookingLogOut> {
  return request<BookingLogOut>(`/trips/${tripId}/booking/request`, {
    method: "POST",
    body: JSON.stringify(bookingRequestCreate),
  })
}

export function getBooking(logId: number): Promise<BookingLogOut> {
  return request<BookingLogOut>(`/bookings/${logId}`)
}

export function confirmBooking(logId: number): Promise<BookingLogOut> {
  return request<BookingLogOut>(`/bookings/${logId}/confirm`, { method: "POST" })
}

export function executeBooking(logId: number): Promise<BookingLogOut> {
  return request<BookingLogOut>(`/bookings/${logId}/execute`, { method: "POST" })
}

export function cancelBooking(logId: number): Promise<BookingLogOut> {
  return request<BookingLogOut>(`/bookings/${logId}/cancel`, { method: "POST" })
}
