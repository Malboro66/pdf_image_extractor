from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from itertools import cycle
import requests

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpClientConfig:
    timeout_seconds: float = 10.0
    max_retries: int = 3
    backoff_base_seconds: float = 0.5
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)
    user_agents: tuple[str, ...] = (
        "pdf-image-extractor/1.0",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    )


class HttpClient:
    """HTTP transport with retry/backoff and deterministic user-agent rotation."""

    def __init__(self, config: HttpClientConfig, session: requests.Session | None = None) -> None:
        self._config = config
        self._session = session or requests.Session()
        self._ua_cycle = cycle(config.user_agents)
        self._ua_lock = threading.Lock()

    def _next_user_agent(self) -> str:
        with self._ua_lock:
            return next(self._ua_cycle)

    def fetch_bytes(self, url: str, *, extra_headers: dict[str, str] | None = None) -> bytes | None:
        headers = {"User-Agent": self._next_user_agent()}
        if extra_headers:
            headers.update(extra_headers)

        for attempt in range(1, self._config.max_retries + 2):
            try:
                response = self._session.request(
                    method="GET",
                    url=url,
                    headers=headers,
                    timeout=self._config.timeout_seconds,
                )
            except requests.RequestException as exc:
                if attempt > self._config.max_retries:
                    LOGGER.error("HTTP request failed after retries", extra={"url": url, "attempt": attempt, "error": str(exc)})
                    return None
                self._sleep_before_retry(url=url, attempt=attempt, reason=f"exception:{type(exc).__name__}")
                continue

            if response.status_code in self._config.retry_statuses:
                if attempt > self._config.max_retries:
                    LOGGER.error(
                        "HTTP request returned retryable status after retries",
                        extra={"url": url, "attempt": attempt, "status_code": response.status_code},
                    )
                    return None
                self._sleep_before_retry(url=url, attempt=attempt, reason=f"status:{response.status_code}")
                continue

            response.raise_for_status()
            return response.content

        return None

    def _sleep_before_retry(self, *, url: str, attempt: int, reason: str) -> None:
        delay = self._config.backoff_base_seconds * (2 ** (attempt - 1))
        LOGGER.warning(
            "HTTP request retry scheduled",
            extra={"url": url, "attempt": attempt, "delay_seconds": delay, "reason": reason},
        )
        time.sleep(delay)
