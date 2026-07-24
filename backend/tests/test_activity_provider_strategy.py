import json
from pathlib import Path

from app.adapters.activities_tavily import (
    NormalizedActivityResult,
    RecordedActivityProvider,
)


async def test_recorded_activity_provider_reads_normalized_results_and_honors_limit(
    tmp_path: Path,
) -> None:
    cassette_path = tmp_path / "paris.json"
    cassette_path.write_text(
        json.dumps(
            [
                {
                    "title": "Seine cruise",
                    "url": "https://example.test/seine",
                    "content": "A gentle river cruise.",
                    "score": 0.9,
                },
                {
                    "title": "Louvre",
                    "url": "https://example.test/louvre",
                    "content": "Museum visitor information.",
                    "score": 0.8,
                },
            ]
        )
    )

    results = await RecordedActivityProvider(cassette_path).search("things to do in Paris", 1)

    assert results == [
        NormalizedActivityResult(
            title="Seine cruise",
            url="https://example.test/seine",
            content="A gentle river cruise.",
            score=0.9,
        )
    ]
