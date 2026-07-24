from types import SimpleNamespace

from app.adapters.activities_tavily import (
    RecordedActivityProvider,
    TavilyActivityProvider,
)
from app.adapters.flights_searchapi import LiveSearchApiProvider, RecordedProvider
from evals import run
from tests.db_helpers import run_db


def test_run_metadata_names_planner_and_judge_models() -> None:
    assert run.build_run_metadata() == {
        "model": "gpt-oss-120b",
        "judge_model": "gemini-3.6-flash",
        "provider_mode": "recorded",
    }


def test_recorded_dependencies_ignore_live_flight_setting() -> None:
    dependencies = run._planner_deps("Fitness level: low.", "recorded")

    assert isinstance(dependencies.flight_provider, RecordedProvider)
    assert isinstance(dependencies.activity_provider, RecordedActivityProvider)


def test_live_smoke_dependencies_are_live(monkeypatch) -> None:
    settings = SimpleNamespace(
        searchapi_api_key=SimpleNamespace(get_secret_value=lambda: "search"),
        tavily_api_key=SimpleNamespace(get_secret_value=lambda: "tavily"),
    )
    monkeypatch.setattr(run, "get_settings", lambda: settings)

    dependencies = run._planner_deps("Fitness level: high.", "live-smoke")

    assert isinstance(dependencies.flight_provider, LiveSearchApiProvider)
    assert isinstance(dependencies.activity_provider, TavilyActivityProvider)


def test_eval_trip_matches_dataset_route() -> None:
    metadata = run.dataset.cases[0].metadata
    assert metadata is not None and metadata["flight_search"] is not None
    route = metadata["flight_search"]

    async def open_trip(session):
        trip_id = await run._open_eval_trip(session)
        return await session.get(run.TripRequest, trip_id)

    trip = run_db(open_trip)

    assert trip is not None
    assert (
        trip.origin,
        trip.destination,
        trip.destination_airport,
        trip.depart_date,
        trip.return_date,
    ) == (
        route["departure_id"],
        "San Diego",
        route["arrival_id"],
        route["outbound_date"],
        route["return_date"],
    )


def test_live_smoke_selects_first_case_once(monkeypatch) -> None:
    assert len(run._selected_dataset("live-smoke").cases) == 1
    captured = {}
    report = SimpleNamespace(print=lambda: None)
    selected_dataset = SimpleNamespace(
        evaluate_sync=lambda task, **kwargs: captured.update(kwargs) or report
    )
    monkeypatch.setattr(run, "_selected_dataset", lambda provider_mode: selected_dataset)

    run.main(repeat=4, live_smoke=True)

    assert captured["repeat"] == 1
    assert captured["max_concurrency"] == 1
