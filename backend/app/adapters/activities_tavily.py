"""Tavily activity-research adapter. Tolerant: errors/empty results return [], never fabricated."""

import asyncio
import logging
from dataclasses import dataclass

from tavily import TavilyClient

from app.config import EXCLUDED_ACTIVITY_SEARCH_DOMAINS

logger = logging.getLogger(__name__)


@dataclass
class NormalizedActivityResult:
    """One Tavily search result — ``url`` is the citation that grounds an itinerary activity."""

    title: str
    url: str
    content: str
    score: float


class TavilyActivityProvider:
    """Wraps the synchronous ``tavily-python`` SDK behind the adapter's async interface."""

    def __init__(self, api_key: str) -> None:
        self._client = TavilyClient(api_key=api_key)

    async def search(self, query: str, max_results: int = 5) -> list[NormalizedActivityResult]:
        try:
            response = await asyncio.to_thread(
                self._client.search,
                query=query,
                max_results=max_results,
                search_depth="advanced",
                exclude_domains=EXCLUDED_ACTIVITY_SEARCH_DOMAINS,
            )
        except Exception as error:  # tavily-python raises its own exception hierarchy
            logger.warning("tavily web_search failed: %r for query=%r", error, query)
            return []

        return [
            NormalizedActivityResult(
                title=result.get("title", ""),
                url=result.get("url", ""),
                content=result.get("content", "").strip(),
                score=float(result.get("score", 0.0)),
            )
            for result in response.get("results", [])
        ]
