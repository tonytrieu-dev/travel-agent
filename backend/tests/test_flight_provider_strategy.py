"""Guards the flight-fetching Strategy seam (Phase 3): USE_LIVE_FLIGHT_API selects the
provider once at composition, the never-called-live RecordedProvider degrades honestly
instead of fabricating an offer when a cassette is missing, and offer parsing is verified
against a real captured SearchApi payload (never a hand-fabricated shape).
"""

from pathlib import Path

from app.adapters.flights_searchapi import (
    LiveSearchApiProvider,
    RecordedProvider,
    get_flight_provider,
)
from app.config import FLIGHT_CASSETTE_DIR, Settings


def _settings(use_live_flight_api: bool) -> Settings:
    return Settings(
        gemini_api_key="test-gemini-key",
        searchapi_api_key="test-searchapi-key",
        tavily_api_key="test-tavily-key",
        database_url="postgresql+asyncpg://unused/unused",
        use_live_flight_api=use_live_flight_api,
    )


def test_use_live_flight_api_true_selects_the_live_provider() -> None:
    provider = get_flight_provider(_settings(use_live_flight_api=True))

    assert isinstance(provider, LiveSearchApiProvider), (
        f"USE_LIVE_FLIGHT_API=true must select LiveSearchApiProvider, got {type(provider).__name__}; "
        "a wrong selection here means the live demo would silently replay stale cassettes."
    )


def test_use_live_flight_api_false_selects_the_recorded_provider() -> None:
    provider = get_flight_provider(_settings(use_live_flight_api=False))

    assert isinstance(provider, RecordedProvider), (
        f"USE_LIVE_FLIGHT_API=false must select RecordedProvider, got {type(provider).__name__}; "
        "a wrong selection here means tests/dev-reloads would burn the one-time 100-search quota."
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
