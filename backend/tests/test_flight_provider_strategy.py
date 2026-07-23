"""Guards the flight-fetching Strategy seam (Phase 3): USE_LIVE_FLIGHT_API selects the
provider once at composition, the never-called-live RecordedProvider degrades honestly
instead of fabricating an offer when a cassette is missing, and offer parsing is verified
against a real captured SearchApi payload (never a hand-fabricated shape).
"""

from pathlib import Path

import httpx
import pytest

from app.adapters.flights_searchapi import (
    LiveSearchApiProvider,
    RecordedProvider,
    derive_flight_legs,
    get_flight_provider,
)
from app.config import FLIGHT_CASSETTE_DIR, Settings


async def test_live_provider_booking_options_error_includes_the_upstream_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: a bare status code told a developer nothing about *why* SearchApi
    rejected the token (invalid vs expired vs malformed) — the response body is the only place
    that distinction lives."""

    async def _fake_get(self, url, params=None, headers=None) -> httpx.Response:
        return httpx.Response(400, json={"error": "booking_token has expired"})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    provider = LiveSearchApiProvider(api_key="test-key")

    with pytest.raises(RuntimeError, match="booking_token has expired"):
        await provider.fetch_booking_options(
            "some-token", departure_id="JFK", arrival_id="CDG", outbound_date="2026-08-15"
        )


async def test_live_provider_booking_options_sends_route_and_date_params(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression guard: SearchApi's booking-options engine 400s "Missing required parameter
    departure_id" when only booking_token is sent (observed against the live API during a demo).
    departure_id/arrival_id/outbound_date must be forwarded too. Fails red if they're dropped."""
    captured_params: dict[str, object] = {}

    async def _fake_get(self, url, params=None, headers=None) -> httpx.Response:
        captured_params.update(params or {})
        return httpx.Response(200, json={"booking_options": []})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    provider = LiveSearchApiProvider(api_key="test-key")

    await provider.fetch_booking_options(
        "tok-123", departure_id="JFK", arrival_id="CDG", outbound_date="2026-08-15"
    )

    assert captured_params.get("departure_id") == "JFK", (
        f"fetch_booking_options must forward departure_id to SearchApi, got params={captured_params}"
    )
    assert captured_params.get("arrival_id") == "CDG", (
        f"fetch_booking_options must forward arrival_id to SearchApi, got params={captured_params}"
    )
    assert captured_params.get("outbound_date") == "2026-08-15", (
        f"fetch_booking_options must forward outbound_date to SearchApi, got params={captured_params}"
    )


async def test_live_provider_search_offers_unavailable_reason_includes_the_upstream_response_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same gap, the other call: a non-429 non-200 search failure previously discarded the
    upstream body, hiding *why* (malformed params vs an account issue vs anything else)."""

    async def _fake_get(self, url, params=None, headers=None) -> httpx.Response:
        return httpx.Response(400, json={"error": "unsupported currency"})

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)
    provider = LiveSearchApiProvider(api_key="test-key")

    outcome = await provider.search_offers("JFK", "CDG", "2026-08-01", None)

    assert outcome.unavailable_reason is not None and "unsupported currency" in outcome.unavailable_reason, (
        f"unavailable_reason must surface the upstream body, got {outcome.unavailable_reason!r}"
    )


def _settings(use_live_flight_api: bool) -> Settings:
    return Settings(
        cerebras_api_key="test-cerebras-key",
        searchapi_api_key="test-searchapi-key",
        tavily_api_key="test-tavily-key",
        database_url="postgresql+asyncpg://unused/unused",
        use_live_flight_api=use_live_flight_api,
    )


@pytest.mark.parametrize(
    ("use_live_flight_api", "expected_type", "wrong_selection_consequence"),
    [
        (True, LiveSearchApiProvider, "the live demo would silently replay stale cassettes"),
        (False, RecordedProvider, "tests/dev-reloads would burn the one-time 100-search quota"),
    ],
)
def test_use_live_flight_api_selects_the_matching_provider(
    use_live_flight_api: bool, expected_type: type, wrong_selection_consequence: str
) -> None:
    provider = get_flight_provider(_settings(use_live_flight_api=use_live_flight_api))

    assert isinstance(provider, expected_type), (
        f"USE_LIVE_FLIGHT_API={use_live_flight_api} must select {expected_type.__name__}, got "
        f"{type(provider).__name__}; a wrong selection here means {wrong_selection_consequence}."
    )


async def test_recorded_provider_missing_cassette_is_honest_empty_not_fabricated(
    tmp_path: Path,
) -> None:
    provider = RecordedProvider(cassette_dir=tmp_path)

    outcome = await provider.search_offers("JFK", "CDG", "2026-08-01", None)

    assert outcome.offers == [], (
        f"a missing cassette must never fabricate offers, got {outcome.offers}"
    )
    assert outcome.unavailable_reason is not None and "JFK_CDG_2026-08-01" in outcome.unavailable_reason, (
        f"the unavailable_reason must name the missing cache key so a developer can find/capture "
        f"it, got {outcome.unavailable_reason!r}"
    )


async def test_recorded_provider_parses_a_real_captured_round_trip_cassette() -> None:
    """Regression guard for a real schema finding: a round-trip SearchApi response carries
    ``departure_token`` per offer, not ``booking_token`` (that only appears after a second
    call selects the return leg). Requiring ``booking_token`` unconditionally silently
    dropped every offer in this real cassette — this test fails red if that regresses."""
    provider = RecordedProvider(cassette_dir=FLIGHT_CASSETTE_DIR)

    outcome = await provider.search_offers("JFK", "CDG", "2026-08-15", "2026-08-22")

    assert outcome.offers, (
        f"expected offers parsed from the real captured cassette, got none "
        f"(unavailable_reason={outcome.unavailable_reason!r}); a round-trip offer's "
        f"departure_token is being dropped instead of accepted as the booking_token fallback"
    )
    assert all(offer.booking_token for offer in outcome.offers), (
        "every parsed offer must carry a non-empty token (booking_token or departure_token)"
    )


async def test_derive_flight_legs_carries_one_entry_per_flown_segment() -> None:
    """The frontend expand-to-see-stops feature needs the real per-leg breakdown, not just the
    aggregate stops count — this must come from the real captured payload's flights array."""
    provider = RecordedProvider(cassette_dir=FLIGHT_CASSETTE_DIR)
    outcome = await provider.search_offers("JFK", "CDG", "2026-08-15", "2026-08-22")
    assert outcome.offers, "cassette must yield offers for this assertion to be meaningful"
    raw_offer = outcome.offers[0].raw_offer

    legs = derive_flight_legs(raw_offer)

    assert len(legs) == len(raw_offer["flights"]), (
        f"expected one leg per flown segment ({len(raw_offer['flights'])}), got {len(legs)}"
    )
    assert legs[0]["departure_airport"] == raw_offer["flights"][0]["departure_airport"]["id"], (
        f"leg departure_airport must come from the real segment data, got {legs[0]}"
    )


async def test_parsed_offer_departure_carries_its_date_not_just_a_clock_time() -> None:
    """SearchApi returns an airport's ``date`` and ``time`` as separate fields; the adapter must
    join them so ``depart_at`` holds a placeable timestamp. Dropping the date (the original bug)
    left a bare ``06:05`` that the UI rendered as 'Invalid Date'. Fails red if the date is
    dropped again."""
    provider = RecordedProvider(cassette_dir=FLIGHT_CASSETTE_DIR)

    outcome = await provider.search_offers("JFK", "CDG", "2026-08-15", "2026-08-22")

    assert outcome.offers, "cassette must yield offers for this assertion to be meaningful"
    departure_date = outcome.offers[0].raw_offer["flights"][0]["departure_airport"]["date"]
    assert departure_date in outcome.offers[0].depart_at, (
        f"depart_at must include the offer's departure date {departure_date!r} so it parses to a "
        f"real datetime, got {outcome.offers[0].depart_at!r} — the SearchApi date field is dropped"
    )
