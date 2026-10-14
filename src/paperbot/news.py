"""Multi-source crypto-market news aggregation using public RSS/Atom feeds.

This module collects headlines and metadata only. It does not execute trades,
modify strategy code, or treat headlines as verified facts.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

DEFAULT_FEEDS = {
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "Cointelegraph": "https://cointelegraph.com/rss",
    "CryptoSlate": "https://cryptoslate.com/feed/",
    "Bitcoin Magazine": "https://bitcoinmagazine.com/.rss/full/",
}


class NewsError(RuntimeError):
    """Raised when no configured news feed can be retrieved."""


@dataclass(frozen=True)
class NewsItem:
    title: str
    url: str
    source: str
    published_at: str
    summary: str = ""


def _http_text(url: str, timeout: float = 12.0) -> str:
    request = Request(url, headers={"User-Agent": "binance-futures-paperbot/0.1 (+RSS reader)"})
    with urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _first_text(element: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        found = element.find(name)
        if found is not None and found.text:
            return found.text.strip()
    return ""


def _normalise_date(value: str) -> str:
    if not value:
        return ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return ""


def parse_feed(xml_text: str, source: str) -> list[NewsItem]:
    """Parse RSS 2.0, RSS with namespaces, or Atom entries."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise NewsError(f"{source} returned invalid XML") from exc
    entries = root.findall(".//item") + root.findall(".//{http://www.w3.org/2005/Atom}entry")
    output: list[NewsItem] = []
    for entry in entries:
        title = _first_text(entry, ("title", "{http://www.w3.org/2005/Atom}title"))
        link = _first_text(entry, ("link", "{http://www.w3.org/2005/Atom}id"))
        atom_link = entry.find("{http://www.w3.org/2005/Atom}link")
        if atom_link is not None:
            link = atom_link.attrib.get("href", link)
        summary = _first_text(entry, ("description", "summary", "content",
                                      "{http://www.w3.org/2005/Atom}summary",
                                      "{http://www.w3.org/2005/Atom}content"))
        date = _first_text(entry, ("pubDate", "published", "updated",
                                   "{http://www.w3.org/2005/Atom}published",
                                   "{http://www.w3.org/2005/Atom}updated"))
        if title and link:
            output.append(NewsItem(title=title, url=link, source=source,
                                   published_at=_normalise_date(date), summary=summary))
    return output


def _item_time(item: NewsItem) -> float:
    if not item.published_at:
        return 0.0
    try:
        return datetime.fromisoformat(item.published_at).timestamp()
    except ValueError:
        return 0.0


def fetch_latest_news(
    feeds: dict[str, str] | None = None,
    limit: int = 20,
    cache_path: str | Path = ".cache/news/latest.json",
    *,
    fetcher: Callable[[str], str] | None = None,
    now: Callable[[], float] = time.time,
) -> list[NewsItem]:
    """Fetch each feed, merge/deduplicate by URL, and save the latest results.

    Partial source outages are tolerated if at least one source works. Cached
    results are not silently substituted for a completely failed refresh.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    configured = DEFAULT_FEEDS if feeds is None else feeds
    if not configured:
        raise ValueError("at least one news feed is required")
    get_text = fetcher or _http_text
    items: list[NewsItem] = []
    errors: list[str] = []
    for source, url in configured.items():
        try:
            items.extend(parse_feed(get_text(url), source))
        except (HTTPError, URLError, TimeoutError, OSError, NewsError, ValueError) as exc:
            errors.append(f"{source}: {exc}")
    if not items:
        details = "; ".join(errors) if errors else "feeds contained no usable headlines"
        raise NewsError(f"Could not retrieve news from any configured source: {details}")

    unique: dict[str, NewsItem] = {}
    for item in items:
        key = item.url.strip()
        if key and key not in unique:
            unique[key] = item
    result = sorted(unique.values(), key=_item_time, reverse=True)[:limit]
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps([asdict(item) for item in result], ensure_ascii=False, indent=2),
                    encoding="utf-8")
    temp.replace(path)
    return result


def watch_news(
    refresh_seconds: int = 300,
    limit: int = 20,
    cache_path: str | Path = ".cache/news/latest.json",
    *,
    fetcher: Callable[[str], str] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    emit: Callable[[list[NewsItem]], None] | None = None,
    cycles: int | None = None,
) -> None:
    """Refresh the news snapshot repeatedly until interrupted (or cycles in tests)."""
    if not isinstance(refresh_seconds, int) or refresh_seconds < 30:
        raise ValueError("refresh-seconds must be at least 30")
    count = 0
    seen: set[str] = set()
    while cycles is None or count < cycles:
        items = fetch_latest_news(limit=limit, cache_path=cache_path, fetcher=fetcher)
        fresh = [item for item in items if item.url not in seen]
        seen.update(item.url for item in items)
        (emit or print_news)(fresh if fresh else items)
        count += 1
        if cycles is None or count < cycles:
            sleep(refresh_seconds)


def print_news(items: list[NewsItem]) -> None:
    if not items:
        print("No headlines to display.")
        return
    for item in items:
        date = item.published_at or "publication time unavailable"
        print(f"- [{item.source}] {item.title} ({date})\n  {item.url}")
