from types import SimpleNamespace

from app.adapters.activities_tavily import (
    RecordedActivityProvider,
    TavilyActivityProvider,
)
from app.adapters.flights_searchapi import LiveSearchApiProvider, RecordedProvider
from evals import run
from evals.evaluators import FitnessAppropriateness
from tests.db_helpers import run_db


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
    assert len(run._selected_dataset("live-smoke", with_judge=False).cases) == 1
    captured = {}
    printed = {}
    report = SimpleNamespace(print=lambda **kwargs: printed.update(kwargs))
    selected_dataset = SimpleNamespace(
        evaluate_sync=lambda task, **kwargs: captured.update(kwargs) or report
    )
    monkeypatch.setattr(
        run, "_selected_dataset", lambda provider_mode, *, with_judge: selected_dataset
    )

    run.main(repeat=4, live_smoke=True)

    assert captured["repeat"] == 1
    assert captured["max_concurrency"] == 1
    assert printed == {"include_reasons": True}


def _record_sleeps(monkeypatch, *, now: float) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(run, "asyncio", SimpleNamespace(sleep=fake_sleep))
    monkeypatch.setattr(run, "time", SimpleNamespace(monotonic=lambda: now))
    return slept


async def test_back_to_back_cases_wait_out_the_cerebras_token_window(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch, now=120.0)
    monkeypatch.setattr(run, "_previous_case_finished_at", 100.0)

    await run._wait_out_previous_case_token_window()

    assert slept == [40.0], (
        "a case starting 20s after the previous one finished must wait out the remaining 40s of "
        f"Cerebras's 60s token window or the run 429s; slept {slept}"
    )


async def test_first_case_and_cases_outliving_the_window_never_wait(monkeypatch) -> None:
    slept = _record_sleeps(monkeypatch, now=200.0)
    monkeypatch.setattr(run, "_previous_case_finished_at", None)

    await run._wait_out_previous_case_token_window()
    assert slept == [], f"the first case has no predecessor to wait on; slept {slept}"

    monkeypatch.setattr(run, "_previous_case_finished_at", 100.0)
    await run._wait_out_previous_case_token_window()
    assert slept == [], (
        f"a case that itself ran longer than the 60s window already drained it; slept {slept}"
    )


def test_default_eval_dataset_is_deterministic_and_judge_is_opt_in() -> None:
    default_evaluators = run._selected_dataset("recorded", with_judge=False).evaluators
    judged_evaluators = run._selected_dataset("recorded", with_judge=True).evaluators

    assert not any(isinstance(evaluator, FitnessAppropriateness) for evaluator in default_evaluators)
    assert any(isinstance(evaluator, FitnessAppropriateness) for evaluator in judged_evaluators)
