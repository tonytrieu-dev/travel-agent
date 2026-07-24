"""Manual, quota-spending capture of a real SearchApi response into tests/fixtures/recorded/.
Never run by pytest/CI.

Usage:
    uv run python -m scripts.capture_flight_cassette JFK CDG 2026-08-01 \\
        --return-date 2026-08-08 --confirm-quota-spend
"""

import argparse
import asyncio
import json
import sys

import httpx

from app.adapters.flights_searchapi import cache_key
from app.config import FLIGHT_CASSETTE_DIR, SEARCHAPI_BASE_URL, get_settings


async def _capture(
    departure_id: str, arrival_id: str, outbound_date: str, return_date: str | None
) -> None:
    settings = get_settings()

    params = {
        "engine": "google_flights",
        "departure_id": departure_id,
        "arrival_id": arrival_id,
        "outbound_date": outbound_date,
        "currency": "USD",
    }
    if return_date:
        params["return_date"] = return_date

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            SEARCHAPI_BASE_URL,
            params=params,
            headers={"Authorization": f"Bearer {settings.searchapi_api_key.get_secret_value()}"},
        )

    if response.status_code != 200:
        print(f"capture failed: HTTP {response.status_code}: {response.text[:500]}")
        sys.exit(1)

    key = cache_key(departure_id, arrival_id, outbound_date, return_date)
    FLIGHT_CASSETTE_DIR.mkdir(parents=True, exist_ok=True)
    cassette_path = FLIGHT_CASSETTE_DIR / f"{key}.json"
    cassette_path.write_text(json.dumps(response.json(), indent=2))
    print(f"captured real SearchApi response to {cassette_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("departure_id", help="Origin IATA code, e.g. JFK")
    parser.add_argument("arrival_id", help="Destination IATA code, e.g. CDG")
    parser.add_argument("outbound_date", help="ISO date, e.g. 2026-08-01")
    parser.add_argument("--return-date", default=None, help="ISO date; omit for one-way")
    parser.add_argument(
        "--confirm-quota-spend",
        action="store_true",
        help="Required: this call spends one unit of the one-time 100-search quota.",
    )
    args = parser.parse_args()

    if not args.confirm_quota_spend:
        print(
            "Refusing to spend SearchApi quota without --confirm-quota-spend "
            "(the 100-search allowance is one-time and non-renewable)."
        )
        sys.exit(1)

    asyncio.run(_capture(args.departure_id, args.arrival_id, args.outbound_date, args.return_date))


if __name__ == "__main__":
    main()
