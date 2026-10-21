from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from .domain import Event


class ScraplingEventSource:
    """Fetch one public page with the locally installed Scrapling package.

    This adapter only collects evidence. It does not bypass authentication,
    publish content, place orders, or infer financial conclusions from text.
    Callers remain responsible for terms of service and robots.txt compliance.
    """

    def __init__(
        self,
        url: str,
        entities: tuple[str, ...],
        *,
        title_selector: str = "title::text",
        body_selector: str = "body::text",
        source_reliability: float = 0.5,
        allowed_domains: tuple[str, ...] = (),
        max_chars: int = 20_000,
        fetcher: Any | None = None,
    ) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("url must be an absolute http(s) URL")
        if allowed_domains and parsed.hostname not in set(allowed_domains):
            raise ValueError("url host is not in allowed_domains")
        if not 0 <= source_reliability <= 1:
            raise ValueError("source_reliability must be between 0 and 1")
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self.url = url
        self.entities = entities
        self.title_selector = title_selector
        self.body_selector = body_selector
        self.source_reliability = source_reliability
        self.max_chars = max_chars
        self.fetcher = fetcher

    def fetch(self) -> tuple[Event, ...]:
        fetcher = self.fetcher
        if fetcher is None:
            from scrapling.fetchers import Fetcher

            fetcher = Fetcher
        page = fetcher.get(self.url)
        title = _first_text(page.css(self.title_selector)) or self.url
        body = " ".join(_text_values(page.css(self.body_selector)))
        body = " ".join(body.split())[: self.max_chars]
        event_id = hashlib.sha256(f"{self.url}\n{title}\n{body}".encode("utf-8")).hexdigest()[:24]
        hostname = urlparse(self.url).hostname or "unknown"
        event = Event(
            id=event_id,
            observed_at=datetime.now(timezone.utc),
            source=f"scrapling:{hostname}",
            title=title[:500],
            body=body,
            entities=self.entities,
            source_reliability=self.source_reliability,
            url=self.url,
        )
        return (event,)


def _text_values(selection: Any) -> list[str]:
    values = selection.getall() if hasattr(selection, "getall") else []
    return [str(value) for value in values if str(value).strip()]


def _first_text(selection: Any) -> str:
    values = _text_values(selection)
    return values[0].strip() if values else ""
