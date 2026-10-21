from __future__ import annotations

import hashlib
import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .domain import Event


class MoltbookEventSource:
    """Convert posts from the local moltbook_scraper client into Events."""

    def __init__(self, client: Any, *, limit: int = 100, max_posts: int = 100, focus_terms: tuple[str, ...] = ()) -> None:
        if limit <= 0 or max_posts <= 0:
            raise ValueError("limit and max_posts must be positive")
        self.client = client
        self.limit = limit
        self.max_posts = max_posts
        self.focus_terms = tuple(term.lower().lstrip("@") for term in focus_terms if term.strip())

    def fetch(self) -> tuple[Event, ...]:
        result = self.client.fetch_posts(limit=self.limit)
        events = []
        for post in result.get("posts", [])[: self.max_posts]:
            post_id = str(post.get("id", ""))
            if not post_id:
                continue
            observed_at = _parse_time(post.get("created_at") or post.get("createdAt"))
            if observed_at is None:
                # A missing or unparsable timestamp is dropped rather than
                # fabricated as "now": a wrong event time silently corrupts
                # replay alignment and look-ahead protection.
                continue
            title = str(post.get("title") or "").strip()
            body = str(post.get("content") or "").strip()
            author = post.get("author") or {}
            author_name = author.get("name") if isinstance(author, dict) else None
            entities = tuple(value for value in (author_name, post.get("submolt_name")) if value)
            events.append(Event(
                id=hashlib.sha256(f"moltbook:{post_id}".encode()).hexdigest()[:24],
                observed_at=observed_at,
                source="moltbook",
                title=title or "Moltbook post",
                body=body[:20_000],
                entities=tuple(str(value) for value in entities),
                source_reliability=0.35,
                url=post.get("url"),
            ))
        return tuple(sorted(events, key=self._focus_score, reverse=True))

    def _focus_score(self, event: Event) -> int:
        haystack = " ".join((event.title, event.body, *event.entities)).lower()
        return sum(1 for term in self.focus_terms if term in haystack)


def local_moltbook_client(api_key: str, scraper_root: str | Path) -> Any:
    """Load the existing local client's implementation without copying it."""
    path = Path(scraper_root) / "src" / "client.py"
    if not path.exists():
        raise FileNotFoundError(f"Moltbook client not found: {path}")
    spec = importlib.util.spec_from_file_location("local_moltbook_client", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load Moltbook client from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MoltbookClient(api_key)


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
