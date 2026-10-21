import pytest

from revenue_agent import LLMClient, ProviderQuotaError
from revenue_agent import llm_client


class Response:
    status_code = 402

    def raise_for_status(self):
        raise AssertionError("quota errors should be classified first")


class Session:
    def post(self, *args, **kwargs):
        return Response()


def test_provider_quota_error_is_explicit() -> None:
    client = LLMClient("key", "https://example.com/v1", "deepseek-v4-flash", session=Session())
    with pytest.raises(ProviderQuotaError, match="quota"):
        client.complete([{"role": "user", "content": "test"}])


def test_full_completions_endpoint_is_normalized() -> None:
    client = LLMClient("key", "https://example.com/v1/chat/completions", "deepseek-v4-flash", session=Session())
    assert client.base_url == "https://example.com/v1"


def _ok_response(content: str = '{"ok": true}') -> object:
    class Ok:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    return Ok()


def _error_response(code: int) -> object:
    class Err:
        status_code = code
        text = f"{code} error"

        def raise_for_status(self):
            import requests

            raise requests.HTTPError(f"{code} Server Error for url")

    return Err()


class FlakySession:
    """Returns queued responses; raises Timeout when the queue is empty."""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def post(self, *args, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(llm_client.time, "sleep", lambda seconds: None)


def test_transient_500_then_success_is_retried() -> None:
    session = FlakySession([_error_response(503), _ok_response()])
    client = LLMClient("key", "https://example.com/v1", "m", session=session, max_retries=2)
    assert client.complete([{"role": "user", "content": "hi"}]) == '{"ok": true}'
    assert session.calls == 2


def test_timeout_then_success_is_retried() -> None:
    import requests

    session = FlakySession([requests.ReadTimeout("slow gateway"), _ok_response()])
    client = LLMClient("key", "https://example.com/v1", "m", session=session, max_retries=1)
    assert client.complete_response([{"role": "user", "content": "hi"}]).content == '{"ok": true}'
    assert session.calls == 2


def test_exhausted_retries_raise_last_error_and_client_errors_are_not_retried() -> None:
    import requests

    session = FlakySession([requests.ConnectionError("down"), requests.ConnectionError("down")])
    client = LLMClient("key", "https://example.com/v1", "m", session=session, max_retries=1)
    with pytest.raises(requests.ConnectionError):
        client.complete([{"role": "user", "content": "hi"}])
    assert session.calls == 2

    http4xx = FlakySession([_error_response(422)])
    client4xx = LLMClient("key", "https://example.com/v1", "m", session=http4xx, max_retries=3)
    with pytest.raises(requests.HTTPError):
        client4xx.complete([{"role": "user", "content": "hi"}])
    assert http4xx.calls == 1
