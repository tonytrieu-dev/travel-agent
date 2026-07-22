"""Application configuration, loaded once from the environment (fail-fast).

Secrets are wrapped in ``SecretStr`` so they never appear in logs or ``repr`` output.
Every tunable the agent and adapters rely on lives here as a named constant — no magic
numbers scattered through the codebase.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Groq free tier: 1K req/day, 12K tokens/min. Alternatives: openai/gpt-oss-120b (smarter, tighter
# token/min), llama-3.1-8b-instant (14.4K req/day, weaker reasoning).
GROQ_MODEL = "llama-3.3-70b-versatile"

SEARCHAPI_BASE_URL = "https://www.searchapi.io/api/v1/search"

# RecordedProvider replays real-captured payloads from here; never hand-fabricated.
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
# unit of the scarce one-time 100-search SearchApi quota. Deliberately short relative to that
# quota concern: flight prices reprice multiple times a day, and a cached result must not
# outlive BOOKING_TTL_MINUTES (30) by much, or a "fresh" search could already hand out a token
# that's dead by the time a human confirms it. A renewable-quota production deployment should
# revalidate even more often than this.
FLIGHT_CACHE_TTL_MINUTES = 15

# Groq list prices (USD/M tokens) for the panel's estimated cost only; actual is $0 on free tier.
LLM_INPUT_PRICE_PER_MILLION_TOKENS = 0.59
LLM_OUTPUT_PRICE_PER_MILLION_TOKENS = 0.79

# No auth yet: every request acts as this one fixed user. get_current_user() is the single seam
# that will start resolving a real identity later — route handlers never read this directly.
DEMO_USER_EMAIL = "demo@travel-agent.local"

# Cap concurrent agent runs so a burst can't blow through Groq's 30 RPM / 12K tokens-per-minute.
MAX_CONCURRENT_AGENT_RUNS = 2

# Per-IP request cap on the expensive routes (/plan, /flights/search) — both spend real,
# scarce third-party quota (Groq RPD, the one-time SearchApi search allotment).
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    groq_api_key: SecretStr
    searchapi_api_key: SecretStr
    tavily_api_key: SecretStr
    database_url: str

    use_live_flight_api: bool = True
    frontend_origin: str = "http://localhost:5173"

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
