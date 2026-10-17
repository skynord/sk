"""Provider interfaces, capabilities and the registry.

A provider is any source of football data: an official REST API, a CSV archive
the operator downloads by hand, an opt-in HTML reader, or manual entry.  Each
one advertises its capabilities and its licensing/cost situation so the app can
report honestly what is and is not available.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.providers.dto import NormalizedMatch, NormalizedOdd, NormalizedStat


class Capability:
    FIXTURES = "fixtures"
    RESULTS = "results"
    STATS = "stats"
    XG = "xg"
    ODDS = "odds"
    LINEUPS = "lineups"
    INJURIES = "injuries"


@dataclass
class ProviderCapabilities:
    name: str
    kind: str  # api | csv | html | manual
    capabilities: set[str] = field(default_factory=set)
    base_url: str | None = None
    requires_key: bool = False
    license_note: str = ""
    cost_note: str = ""
    terms_url: str | None = None
    notes: str = ""


class ProviderError(RuntimeError):
    """Raised when a provider cannot fulfil a request."""


class ProviderUnavailable(ProviderError):
    """Raised when a provider is not configured or not reachable."""


class Provider(ABC):
    """Base class every data source implements."""

    def __init__(self) -> None:
        self.capabilities = self.describe()

    @abstractmethod
    def describe(self) -> ProviderCapabilities:
        """Return static metadata about this provider."""

    def is_available(self) -> tuple[bool, str]:
        """Return (available, reason). Default: always available."""
        return True, "available"

    def fetch_matches(
        self,
        *,
        league_name: str | None = None,
        season_label: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> Iterable[NormalizedMatch]:
        raise ProviderUnavailable(f"{self.capabilities.name} does not supply fixtures")

    def fetch_stats(self, match: NormalizedMatch) -> Iterable[NormalizedStat]:
        raise ProviderUnavailable(f"{self.capabilities.name} does not supply stats")

    def fetch_odds(self, match: NormalizedMatch) -> Iterable[NormalizedOdd]:
        raise ProviderUnavailable(f"{self.capabilities.name} does not supply odds")


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------
_REGISTRY: dict[str, type[Provider]] = {}


def register_provider(cls: type[Provider]) -> type[Provider]:
    name = cls().describe().name  # providers are cheap to construct
    _REGISTRY[name] = cls
    return cls


def available_provider_names() -> list[str]:
    return sorted(_REGISTRY)


def get_provider(name: str) -> Provider:
    if name not in _REGISTRY:
        raise ProviderUnavailable(f"unknown provider '{name}'")
    return _REGISTRY[name]()


def build_providers(enabled: list[str]) -> dict[str, Provider]:
    providers: dict[str, Provider] = {}
    for name in enabled:
        try:
            providers[name] = get_provider(name)
        except ProviderUnavailable:
            continue
    return providers
