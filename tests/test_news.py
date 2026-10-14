from pathlib import Path
from urllib.error import URLError

import pytest

from paperbot.news import NewsError, fetch_latest_news, parse_feed, watch_news


RSS = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Bitcoin market update</title><link>https://example.test/a</link>
<pubDate>Wed, 14 Oct 2026 10:00:00 GMT</pubDate><description>Market summary</description></item>
<item><title>Ethereum update</title><link>https://example.test/b</link>
<pubDate>Wed, 14 Oct 2026 09:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_parse_rss_and_normalise_dates():
    items = parse_feed(RSS, "Fixture")
    assert len(items) == 2
    assert items[0].source == "Fixture"
    assert items[0].published_at == "2026-10-14T10:00:00+00:00"


def test_parse_atom_feed():
    atom = """<feed xmlns="http://www.w3.org/2005/Atom">
    <entry><title>BTC headline</title><link href="https://example.test/atom"/>
    <updated>2026-10-14T10:00:00Z</updated><summary>summary</summary></entry></feed>"""
    items = parse_feed(atom, "Atom")
    assert len(items) == 1
    assert items[0].url == "https://example.test/atom"


def test_aggregator_deduplicates_sorts_and_caches(tmp_path):
    feeds = {"A": "https://feed.test/a", "B": "https://feed.test/b"}
    def fetch(url):
        if url.endswith("/a"):
            return RSS
        return RSS.replace("Fixture", "B").replace("</channel>", """<item>
        <title>Newest story</title><link>https://example.test/new</link>
        <pubDate>Wed, 14 Oct 2026 11:00:00 GMT</pubDate></item></channel>""")
    path = tmp_path / "news.json"
    items = fetch_latest_news(feeds, limit=10, cache_path=path, fetcher=fetch)
    assert items[0].title == "Newest story"
    assert len([item for item in items if item.url == "https://example.test/a"]) == 1
    assert path.exists()


def test_partial_source_failure_keeps_working_source(tmp_path):
    def fetch(url):
        if url.endswith("/bad"):
            raise URLError("offline")
        return RSS
    items = fetch_latest_news({"bad": "https://feed.test/bad",
                               "good": "https://feed.test/good"},
                              cache_path=tmp_path / "news.json", fetcher=fetch)
    assert items
    assert all(item.source == "good" for item in items)


def test_all_sources_failure_is_explicit(tmp_path):
    with pytest.raises(NewsError, match="any configured source"):
        fetch_latest_news({"bad": "https://feed.test/bad"},
                          cache_path=tmp_path / "news.json",
                          fetcher=lambda _: (_ for _ in ()).throw(URLError("offline")))


def test_watch_refreshes_and_reports_new_items(tmp_path):
    outputs = []
    sleeps = []
    watch_news(refresh_seconds=30, limit=5, cache_path=tmp_path / "news.json",
               fetcher=lambda _: RSS, sleep=sleeps.append, emit=outputs.append, cycles=2)
    assert len(outputs) == 2
    assert len(sleeps) == 1
    assert len(outputs[1]) == 2
