from revenue_agent import ScraplingEventSource


class FakeSelection:
    def __init__(self, values):
        self.values = values

    def getall(self):
        return self.values


class FakePage:
    def css(self, selector):
        if selector == "title::text":
            return FakeSelection(["AMD expands AI accelerator ecosystem"])
        return FakeSelection(["A public report describes lower prices and broader software support."])


class FakeFetcher:
    @staticmethod
    def get(url):
        assert url == "https://example.com/report"
        return FakePage()


def test_scrapling_source_converts_page_to_timestamped_event() -> None:
    source = ScraplingEventSource(
        "https://example.com/report",
        ("AMD",),
        allowed_domains=("example.com",),
        fetcher=FakeFetcher,
    )
    event = tuple(source.fetch())[0]
    assert event.source == "scrapling:example.com"
    assert event.url == "https://example.com/report"
    assert "lower prices" in event.body


def test_scrapling_source_rejects_unapproved_host() -> None:
    try:
        ScraplingEventSource("https://other.example/report", (), allowed_domains=("example.com",))
    except ValueError as error:
        assert "allowed_domains" in str(error)
    else:
        raise AssertionError("unapproved host was accepted")
