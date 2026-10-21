from datetime import datetime, timezone

from revenue_agent import MoltbookEventSource, YahooFinanceSource


class FakeMoltbook:
    def fetch_posts(self, limit):
        assert limit == 10
        return {"posts": [{"id": "p1", "title": "Need AMD analysis", "content": "Demand is rising", "created_at": "2026-08-08T00:00:00Z", "url": "https://moltbook.com/p1"}]}


def test_moltbook_posts_become_events() -> None:
    events = MoltbookEventSource(FakeMoltbook(), limit=10).fetch()
    assert events[0].id.startswith("moltbook:") is False
    assert events[0].title == "Need AMD analysis"
    assert events[0].url.endswith("/p1")


class FakeHistory:
    def iterrows(self):
        return iter([(datetime(2026, 8, 8, tzinfo=timezone.utc), {"Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 100})])


class FakeTicker:
    def history(self, **kwargs):
        assert kwargs["auto_adjust"] is False
        return FakeHistory()

    @property
    def info(self):
        return {"marketCap": 1000, "revenueGrowth": 0.2, "profitMargins": 0.1, "operatingMargins": 0.15, "trailingPE": 20}


class FakeYahoo:
    def Ticker(self, symbol):
        assert symbol == "AMD"
        return FakeTicker()


def test_yahoo_source_normalizes_bars_and_fundamentals() -> None:
    source = YahooFinanceSource(("AMD",), yf_module=FakeYahoo())
    bars = source.fetch()
    fundamentals = source.fetch_fundamentals()
    assert bars[0].close == 1.5
    assert fundamentals[0].revenue_growth == 0.2
