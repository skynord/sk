"""Application configuration.

All runtime configuration comes from environment variables (optionally loaded
from a ``.env`` file next to the backend).  See ``.env.example`` for the full
list and the README for which providers need keys.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = BACKEND_DIR.parent
DATA_DIR = PROJECT_DIR / "data"


class Settings(BaseSettings):
    """Typed, validated settings object."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_DIR / ".env", BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        env_prefix="FA_",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General ---------------------------------------------------------
    app_name: str = "Sky"
    environment: str = Field(default="development")
    debug: bool = False
    api_prefix: str = "/api"

    # --- Database --------------------------------------------------------
    # SQLite by default; set to a postgresql+psycopg:// URL for Postgres.
    database_url: str = f"sqlite:///{(DATA_DIR / 'football_analyst.db').as_posix()}"
    sql_echo: bool = False

    # --- HTTP client -----------------------------------------------------
    http_timeout_seconds: float = 20.0
    http_max_retries: int = 3
    http_backoff_seconds: float = 1.0
    http_user_agent: str = (
        "Sky/0.1 (+https://localhost; contact: the operator of this "
        "self-hosted instance)"
    )
    # Requests per minute per provider host (politeness / rate limiting).
    http_rate_limit_per_minute: int = 30

    # --- Cache -----------------------------------------------------------
    cache_dir: Path = DATA_DIR / "cache"
    cache_ttl_seconds: int = 3600

    # --- Data providers --------------------------------------------------
    # Only providers that are explicitly enabled are used.
    #  - football_data_co_uk: reads CSVs you downloaded by hand into data/raw
    #  - manual: reads data/raw/manual_*.json you maintain yourself
    #  - openfootball: reads public-domain results JSON (no key) over HTTPS
    #  - sample: SYNTHETIC demo data (labelled; never present as real)
    enabled_providers: str = "football_data_co_uk,manual,openfootball"

    # Public-domain openfootball mirror (https://github.com/openfootball/football.json)
    openfootball_base_url: str = (
        "https://raw.githubusercontent.com/openfootball/football.json/master"
    )
    # Competition codes and seasons to read, e.g. "en.1,es.1,de.1,it.1,fr.1".
    # Domestic leagues use the European "2024-25" season style; the UEFA cups
    # (uefa.cl/el/ecl) are filed under the same season folders.
    openfootball_leagues: str = "en.1,es.1,de.1,it.1,fr.1,uefa.cl,uefa.el,uefa.ecl"
    openfootball_seasons: str = "2024-25,2025-26,2026-27"

    api_football_key: str | None = None
    # API-Football dashboard host; RapidAPI users override this.
    api_football_base_url: str = "https://v3.football.api-sports.io"
    api_football_season: int | None = None

    sportmonks_token: str | None = None
    sportmonks_base_url: str = "https://api.sportmonks.com/v3/football"

    football_data_org_key: str | None = None
    football_data_org_base_url: str = "https://api.football-data.org/v4"

    # Which provider to use for live scores / upcoming fixtures.  Left empty,
    # the app picks the first available one (api_football, football_data_org,
    # sportmonks).  All of these need an API key.
    live_provider: str | None = None

    # Opt-in HTML scraping. Off by default; when on, robots.txt is honoured.
    enable_besoccer_scraping: bool = False
    respect_robots_txt: bool = True

    # Where manually downloaded CSVs (e.g. football-data.co.uk) are placed.
    raw_data_dir: Path = DATA_DIR / "raw"

    # --- Models ----------------------------------------------------------
    monte_carlo_simulations: int = 10_000
    time_decay_halflife_days: float = 180.0
    min_matches_for_model: int = 20
    max_goals_grid: int = 12
    #: L2 penalty on attack/defence strengths.  Teams with little evidence
    #: (promoted sides, season openers, cup-only entrants with one or two matches)
    #: are otherwise free to swing to the parameter bounds, which both overturns
    #: the 1X2 forecast and blows up expected goals.  A small positive value
    #: shrinks them toward the league average; the walk-forward backtest on the
    #: domestic leagues ranks 1.0 best (lower log-loss than 0.0), so it is the
    #: default.
    model_regularization: float = 1.0
    #: Competition "country" labels that mark a cross-border cup rather than a
    #: domestic league (openfootball files the UEFA competitions under
    #: "Europe").  A fixture in one of these pools the two clubs' domestic-league
    #: history and fits a per-competition intercept, so strengths drawn from
    #: different leagues stay comparable.
    international_competition_countries: str = "Europe,World,International"

    # --- Prediction bot --------------------------------------------------
    bot_default_days_ahead: int = 14
    bot_default_limit: int = 50
    bot_loop_seconds: int = 3600

    # --- Chat language model (optional) ----------------------------------
    # Any OpenAI-compatible endpoint works (including free tiers).  Without a
    # key the Ask Sky chat answers from its own composer, which is fully
    # grounded in the model output above.
    llm_api_key: str | None = None
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 20.0

    # --- CORS ------------------------------------------------------------
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("cache_dir", "raw_data_dir", mode="before")
    @classmethod
    def _expand_path(cls, value: object) -> object:
        if isinstance(value, str):
            return Path(value).expanduser()
        return value

    @field_validator(
        "api_football_key",
        "football_data_org_key",
        "sportmonks_token",
        "live_provider",
        "llm_api_key",
        mode="before",
    )
    @classmethod
    def _blank_to_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def provider_list(self) -> list[str]:
        return [p.strip() for p in self.enabled_providers.split(",") if p.strip()]

    @property
    def openfootball_league_list(self) -> list[str]:
        return [c.strip() for c in self.openfootball_leagues.split(",") if c.strip()]

    @property
    def openfootball_season_list(self) -> list[str]:
        return [s.strip() for s in self.openfootball_seasons.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def international_country_list(self) -> list[str]:
        return [
            c.strip().casefold()
            for c in self.international_competition_countries.split(",")
            if c.strip()
        ]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.cache_dir.mkdir(parents=True, exist_ok=True)
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    return settings
