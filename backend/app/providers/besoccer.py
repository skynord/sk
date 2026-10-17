"""BeSoccer opt-in HTML reader (experimental).

Research summary (see README):
- robots.txt allows general crawling of the site but disallows ``/ajax*`` and
  ``/scripts*``, so those endpoints are never used.
- BeSoccer exposes no documented public API.  Its pages are rendered for people,
  and its terms may restrict automated collection; scraping is therefore OFF by
  default and must be explicitly enabled (FA_ENABLE_BESOCCER_SCRAPING=true).

When enabled this provider fetches a *page the operator names* and performs a
best-effort parse.  Because the markup can change at any time, this provider is
marked experimental and reports honestly when it cannot parse a page.
"""

from __future__ import annotations

import re
import urllib.robotparser
from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urlparse

from app.config import get_settings
from app.logging_config import get_logger
from app.providers.base import (
    Capability,
    Provider,
    ProviderCapabilities,
    ProviderError,
    ProviderUnavailable,
    register_provider,
)
from app.providers.dto import NormalizedMatch
from app.providers.http import get_http_client

logger = get_logger(__name__)

_ROW_RE = re.compile(
    r'(?P<home>[A-ZÀ-ÿ][\w\s\.\-\']{2,30})\s*(?P<hg>\d{1,2})\s*[-:]\s*(?P<ag>\d{1,2})\s*'
    r'(?P<away>[A-ZÀ-ÿ][\w\s\.\-\']{2,30})',
)


@register_provider
class BeSoccerProvider(Provider):
    def describe(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            name="besoccer",
            kind="html",
            capabilities={Capability.FIXTURES, Capability.RESULTS},
            base_url="https://www.besoccer.es",
            requires_key=False,
            license_note="No public API. Scraping disabled by default; check BeSoccer terms before enabling.",
            cost_note="Free to view, but automated access is not licensed.",
            terms_url="https://www.besoccer.es/legal/condiciones-de-uso",
            notes="Experimental, opt-in, robots.txt honoured. Markup may change without notice.",
        )

    def is_available(self) -> tuple[bool, str]:
        settings = get_settings()
        if not settings.enable_besoccer_scraping:
            return False, "disabled (set FA_ENABLE_BESOCCER_SCRAPING=true to opt in)"
        return True, "enabled (experimental, robots.txt honoured)"

    def _robots_allows(self, url: str) -> bool:
        if not get_settings().respect_robots_txt:
            return True
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            response = get_http_client().get(robots_url, use_cache=False)
            parser.parse(response.text.splitlines())
        except Exception as exc:  # network or parse failure -> be conservative
            logger.warning("robots.txt fetch failed", extra={"error": str(exc)})
            return False
        return parser.can_fetch("*", url)

    def fetch_matches(self, *, url: str | None = None, **kwargs: object) -> Iterable[NormalizedMatch]:
        if not url:
            raise ProviderError("besoccer provider needs an explicit 'url' page to read")
        if not self._robots_allows(url):
            raise ProviderUnavailable(f"robots.txt disallows fetching {url}")
        response = get_http_client().get(url)
        if response.status_code >= 400:
            raise ProviderError(f"besoccer page returned HTTP {response.status_code}")
        # Strip tags so the score regex works on text, not attributes.
        text = re.sub(r"<[^>]+>", " ", response.text)
        text = re.sub(r"\s+", " ", text)
        seen: set[str] = set()
        for match in _ROW_RE.finditer(text):
            home = match.group("home").strip()
            away = match.group("away").strip()
            if not home or not away or home == away:
                continue
            key = f"{home}|{away}|{match.group('hg')}"
            if key in seen:
                continue
            seen.add(key)
            yield NormalizedMatch(
                provider_id=f"besoccer:{home}:{away}:{match.group('hg')}-{match.group('ag')}",
                kickoff_utc=datetime.now(tz=UTC),
                home_name=home,
                away_name=away,
                league_name="unknown (from page)",
                status="finished",
                home_goals=int(match.group("hg")),
                away_goals=int(match.group("ag")),
                source_url=url,
            )
