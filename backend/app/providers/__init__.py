"""Provider package.

Importing this module registers every built-in provider with the registry.
"""

# Import for side effects: each module registers its provider class.
from app.providers.api_football import ApiFootballProvider  # noqa: F401
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderUnavailable,
    available_provider_names,
    build_providers,
    get_provider,
    register_provider,
)
from app.providers.besoccer import BeSoccerProvider  # noqa: F401
from app.providers.football_data_co_uk import FootballDataCoUkProvider  # noqa: F401
from app.providers.football_data_org import FootballDataOrgProvider  # noqa: F401
from app.providers.manual import ManualProvider  # noqa: F401
from app.providers.openfootball import OpenFootballProvider  # noqa: F401
from app.providers.sample import SampleProvider  # noqa: F401
from app.providers.sportmonks import SportmonksProvider  # noqa: F401

__all__ = [
    "Capability",
    "Provider",
    "ProviderCapabilities",
    "ProviderError",
    "ProviderUnavailable",
    "available_provider_names",
    "build_providers",
    "get_provider",
    "register_provider",
]
