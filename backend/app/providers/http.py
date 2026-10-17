"""Rate-limited, retrying, caching HTTP client.

Politeness is a hard requirement: we identify ourselves honestly, space
requests to a per-host limit, honour ``Retry-After``, and never attempt to solve
CAPTCHAs or defeat anti-bot measures.  Responses are cached on disk with a TTL
so repeated analysis runs do not re-hit the same source.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import get_settings
from app.logging_config import get_logger
from app.providers.base import ProviderError

logger = get_logger(__name__)


@dataclass
class CachedResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]
    from_cache: bool

    def json(self) -> Any:
        return json.loads(self.content.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx as _httpx

            raise _httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=None,  # type: ignore[arg-type]
                response=None,  # type: ignore[arg-type]
            )


class HttpClient:
    def __init__(self, settings=None) -> None:
        self.settings = settings or get_settings()
        self._client = httpx.Client(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": self.settings.http_user_agent},
            follow_redirects=True,
        )
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self.cache_dir: Path = self.settings.cache_dir

    # -- cache -----------------------------------------------------------
    def _cache_key(self, url: str, params: dict[str, Any] | None) -> str:
        return url + ("?" + json.dumps(params, sort_keys=True) if params else "")

    def _cache_path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        return self.cache_dir / f"{digest}.json"

    def purge(self, url: str, *, params: dict[str, Any] | None = None) -> None:
        """Delete any cached response for this request (e.g. a cached error)."""
        path = self._cache_path(self._cache_key(url, params))
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.warning("cache purge failed", extra={"path": str(path)})

    def _read_cache(self, key: str, ttl: int) -> CachedResponse | None:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        fetched = datetime.fromisoformat(payload["fetched_at"])
        age = (datetime.now(tz=UTC) - fetched).total_seconds()
        if age > ttl:
            return None
        return CachedResponse(
            status_code=payload["status_code"],
            content=payload["content"].encode("utf-8"),
            headers=payload.get("headers", {}),
            from_cache=True,
        )

    def _write_cache(self, key: str, response: CachedResponse) -> None:
        path = self._cache_path(key)
        payload = {
            "fetched_at": datetime.now(tz=UTC).isoformat(),
            "status_code": response.status_code,
            "headers": response.headers,
            "content": response.content.decode("utf-8", errors="replace"),
        }
        try:
            path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError:
            logger.warning("cache write failed", extra={"path": str(path)})

    # -- rate limit ------------------------------------------------------
    def _throttle(self, host: str) -> None:
        limit = self.settings.http_rate_limit_per_minute
        now = time.monotonic()
        bucket = self._hits[host]
        while bucket and now - bucket[0] > 60.0:
            bucket.popleft()
        if len(bucket) >= limit:
            sleep_for = 60.0 - (now - bucket[0])
            if sleep_for > 0:
                logger.info("throttling", extra={"host": host, "sleep": round(sleep_for, 1)})
                time.sleep(sleep_for)
        bucket.append(time.monotonic())

    # -- request ---------------------------------------------------------
    def get(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        use_cache: bool = True,
        ttl: int | None = None,
    ) -> CachedResponse:
        key = self._cache_key(url, params)
        ttl = ttl if ttl is not None else self.settings.cache_ttl_seconds
        if use_cache:
            cached = self._read_cache(key, ttl)
            if cached is not None:
                return cached

        host = httpx.URL(url).host or "unknown"
        last_error: Exception | None = None
        for attempt in range(1, self.settings.http_max_retries + 1):
            self._throttle(host)
            try:
                response = self._client.get(url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("http error", extra={"url": url, "attempt": attempt,
                                                    "error": str(exc)})
                time.sleep(self.settings.http_backoff_seconds * attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                retry_after = response.headers.get("Retry-After")
                delay = self.settings.http_backoff_seconds * attempt
                if retry_after:
                    try:
                        delay = max(delay, float(retry_after))
                    except ValueError:
                        pass
                logger.warning(
                    "retryable status",
                    extra={"url": url, "status": response.status_code, "delay": round(delay, 1)},
                )
                time.sleep(delay)
                last_error = httpx.HTTPStatusError(
                    f"HTTP {response.status_code}", request=response.request, response=response
                )
                continue

            cached = CachedResponse(
                status_code=response.status_code,
                content=response.content,
                headers=dict(response.headers),
                from_cache=False,
            )
            if use_cache and response.status_code < 400:
                self._write_cache(key, cached)
            return cached

        raise ProviderError(f"request to {url} failed after {self.settings.http_max_retries} attempts: {last_error}")

    def close(self) -> None:
        self._client.close()


_shared: HttpClient | None = None


def get_http_client() -> HttpClient:
    global _shared
    if _shared is None:
        _shared = HttpClient()
    return _shared
