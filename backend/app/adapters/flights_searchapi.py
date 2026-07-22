"""SearchApi.io Google Flights adapter. Strategy pattern: FlightProvider Protocol, Live vs
Recorded selected by USE_LIVE_FLIGHT_API. Tolerant — errors/empty results return
unavailable_reason, never a fabricated offer.
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import FLIGHT_CASSETTE_DIR, SEARCHAPI_BASE_URL, Settings


@dataclass
class NormalizedFlightOffer:
    """One flight offer, normalized from SearchApi's ``best_flights``/``other_flights`` shape
    into the fields ``FlightSearchResult`` persists."""

    carrier: str
    price_usd: float
    currency: str
    depart_at: str
    arrive_at: str
    stops: int
    booking_token: str
    raw_offer: dict[str, Any]


@dataclass
class FlightSearchOutcome:
    """Real offers, or an honest empty state naming why there are none."""

    offers: list[NormalizedFlightOffer] = field(default_factory=list)
    unavailable_reason: str | None = None


class FlightProvider(Protocol):
    async def search_offers(
        self,
        departure_id: str,
        arrival_id: str,
        outbound_date: str,
        return_date: str | None,
    ) -> FlightSearchOutcome: ...

    async def fetch_booking_options(
        self, booking_token: str, *, departure_id: str, arrival_id: str, outbound_date: str
    ) -> list[dict[str, Any]]: ...


def cache_key(
    departure_id: str, arrival_id: str, outbound_date: str, return_date: str | None
) -> str:
    return f"{departure_id}_{arrival_id}_{outbound_date}_{return_date or 'oneway'}"


def _airport_datetime(airport: dict[str, Any]) -> str:
    # SearchApi returns date and time as separate fields; join them into one ISO datetime so the
    # value is a real timestamp (the field's name promises), not a bare "06:05" that can't be
    # placed on a calendar.
    date = airport.get("date", "")
    time = airport.get("time", "")
    if date and time:
        return f"{date}T{time}"
    return time or date


def _parse_offers(payload: dict[str, Any]) -> list[NormalizedFlightOffer]:
    # Round-trip offers carry departure_token, not booking_token (confirmed against a real payload).
    offers: list[NormalizedFlightOffer] = []
    for raw_offer in [*payload.get("best_flights", []), *payload.get("other_flights", [])]:
        flights = raw_offer.get("flights", [])
        token = raw_offer.get("booking_token") or raw_offer.get("departure_token")
        if not flights or "price" not in raw_offer or not token:
            continue
        first_leg, last_leg = flights[0], flights[-1]
        offers.append(
            NormalizedFlightOffer(
                carrier=first_leg.get("airline", "Unknown"),
                price_usd=float(raw_offer["price"]),
                currency="USD",
                depart_at=_airport_datetime(first_leg.get("departure_airport", {})),
                arrive_at=_airport_datetime(last_leg.get("arrival_airport", {})),
                stops=len(flights) - 1,
                booking_token=token,
                raw_offer=raw_offer,
            )
        )
    return offers


class LiveSearchApiProvider:
    """Calls the real SearchApi.io Google Flights engine (spends the one-time 100-search quota)."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def search_offers(
        self,
        departure_id: str,
        arrival_id: str,
        outbound_date: str,
        return_date: str | None,
    ) -> FlightSearchOutcome:
        params = {
            "engine": "google_flights",
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "currency": "USD",
        }
        if return_date:
            params["return_date"] = return_date
        else:
            params["type"] = "2"  # one-way; SearchApi defaults to round-trip and 400s without return_date

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    SEARCHAPI_BASE_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as error:
            return FlightSearchOutcome(
                unavailable_reason=(
                    f"searchapi flight search failed: network error ({error!r}) "
                    f"for {cache_key(departure_id, arrival_id, outbound_date, return_date)}"
                )
            )

        if response.status_code == 429:
            return FlightSearchOutcome(
                unavailable_reason=(
                    "searchapi flight search failed: HTTP 429 quota exhausted "
                    f"(departure_id={departure_id} arrival_id={arrival_id} "
                    f"outbound_date={outbound_date})"
                )
            )
        if response.status_code != 200:
            return FlightSearchOutcome(
                unavailable_reason=(
                    f"searchapi flight search failed: HTTP {response.status_code} "
                    f"(departure_id={departure_id} arrival_id={arrival_id} "
                    f"outbound_date={outbound_date}): {response.text}"
                )
            )

        offers = _parse_offers(response.json())
        if not offers:
            return FlightSearchOutcome(
                unavailable_reason=(
                    f"searchapi flight search returned no offers for {departure_id}->"
                    f"{arrival_id} on {outbound_date}"
                )
            )
        return FlightSearchOutcome(offers=offers)

    async def fetch_booking_options(
        self, booking_token: str, *, departure_id: str, arrival_id: str, outbound_date: str
    ) -> list[dict[str, Any]]:
        params = {
            "engine": "google_flights",
            "booking_token": booking_token,
            "departure_id": departure_id,
            "arrival_id": arrival_id,
            "outbound_date": outbound_date,
            "currency": "USD",
        }
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(
                    SEARCHAPI_BASE_URL,
                    params=params,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
        except httpx.HTTPError as error:
            raise RuntimeError(
                f"searchapi booking-options fetch failed: network error ({error!r}) "
                f"for booking_token={booking_token}"
            ) from error

        if response.status_code != 200:
            raise RuntimeError(
                f"searchapi booking-options fetch failed: HTTP {response.status_code} "
                f"for booking_token={booking_token}: {response.text}"
            )
        return list(response.json().get("booking_options", []))


class RecordedProvider:
    """Replays a real-captured cassette — never a hand-fabricated shape."""

    def __init__(self, cassette_dir: Path) -> None:
        self._cassette_dir = cassette_dir

    def _cassette_path(self, key: str) -> Path:
        return self._cassette_dir / f"{key}.json"

    async def search_offers(
        self,
        departure_id: str,
        arrival_id: str,
        outbound_date: str,
        return_date: str | None,
    ) -> FlightSearchOutcome:
        key = cache_key(departure_id, arrival_id, outbound_date, return_date)
        cassette_path = self._cassette_path(key)
        if not cassette_path.exists():
            return FlightSearchOutcome(
                unavailable_reason=f"no recorded cassette for {key} at {cassette_path}"
            )
        payload = json.loads(cassette_path.read_text())
        offers = _parse_offers(payload)
        if not offers:
            return FlightSearchOutcome(
                unavailable_reason=f"recorded cassette {key} contains no offers"
            )
        return FlightSearchOutcome(offers=offers)

    async def fetch_booking_options(
        self, booking_token: str, *, departure_id: str, arrival_id: str, outbound_date: str
    ) -> list[dict[str, Any]]:
        cassette_path = self._cassette_path(f"booking_{booking_token}")
        if not cassette_path.exists():
            raise RuntimeError(f"no recorded booking-options cassette at {cassette_path}")
        payload = json.loads(cassette_path.read_text())
        return list(payload.get("booking_options", []))


def get_flight_provider(settings: Settings) -> FlightProvider:
    """Strategy selection: the toggle is read exactly once, here."""
    if settings.use_live_flight_api:
        return LiveSearchApiProvider(api_key=settings.searchapi_api_key.get_secret_value())
    return RecordedProvider(cassette_dir=FLIGHT_CASSETTE_DIR)
