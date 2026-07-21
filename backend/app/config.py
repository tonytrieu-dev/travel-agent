"""Application configuration, loaded once from the environment (fail-fast).

Secrets are wrapped in ``SecretStr`` so they never appear in logs or ``repr`` output.
Every tunable the agent and adapters rely on lives here as a named constant — no magic
numbers scattered through the codebase.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# The Gemini model driving the planner. gemini-3-flash is free-tier eligible with a plain
# AI Studio key (~10 requests/min, ~1500/day, 1M-token context window).
GEMINI_MODEL = "google-gla:gemini-3-flash"

# SearchApi.io's Google Flights engine: one endpoint, engine=google_flights for offers and
# engine=google_flights (with booking_token) for booking options.
SEARCHAPI_BASE_URL = "https://www.searchapi.io/api/v1/search"

# Where RecordedProvider replays real-captured SearchApi payloads from, and where the
# quota-aware capture script writes them. One JSON file per (departure, arrival, outbound_date,
# return_date) cache key — never hand-fabricated, only ever real captured responses.
FLIGHT_CASSETTE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "recorded" / "flights"

# Agent guardrails. We self-impose a context budget far below the model's real 1M window so
# runs never approach the region where answer quality degrades, and a hard tool-call ceiling
# so the ReAct loop cannot spin forever.
MAX_TOOL_STEPS = 8
MAX_CONTEXT_TOKENS = 100_000
MAX_REQUESTS_PER_RUN = MAX_TOOL_STEPS + 3

# A single tool result is truncated to this many characters before it enters the message
# history, so one oversized web page cannot flood the context window ("clamp at the door").
MAX_TOOL_RESULT_CHARS = 6_000

# A flight offer is only bookable for a short window because airfares are volatile. After this,
# the booking is marked EXPIRED and the user must re-search rather than book a stale price.
BOOKING_TTL_MINUTES = 30

# Reuse a real flight search for the same route+dates for this long instead of spending another
# unit of the scarce one-time 100-search SearchApi quota.
FLIGHT_CACHE_TTL_HOURS = 24

# Gemini list prices (USD per million tokens) for the *estimated* cost shown in the execution
# panel. Actual cost on the free tier is $0; the panel labels the estimate honestly.
GEMINI_INPUT_PRICE_PER_MILLION_TOKENS = 0.50
GEMINI_OUTPUT_PRICE_PER_MILLION_TOKENS = 3.00


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    gemini_api_key: SecretStr
    searchapi_api_key: SecretStr
    tavily_api_key: SecretStr
    database_url: str

    use_live_flight_api: bool = True
    frontend_origin: str = "http://localhost:5173"
    logfire_token: SecretStr | None = None

    @computed_field
    @property
    def dbos_database_url(self) -> str:
        """DBOS speaks the plain (psycopg/sync) Postgres URL, not the asyncpg dialect."""
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    Fails loudly at startup if a required key is missing, rather than at the first request.
    """
    return Settings()  # pyright: ignore[reportCallIssue]  # values come from the environment
