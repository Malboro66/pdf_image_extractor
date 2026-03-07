import importlib.util
from unittest.mock import Mock

import pytest

if importlib.util.find_spec("requests") is None:  # pragma: no cover - depende do ambiente
    pytest.skip("requests não instalado no ambiente", allow_module_level=True)

import requests

from pdf_image_extractor.adapters.transport import HttpClient, HttpClientConfig


class _Response:
    def __init__(self, status_code: int, content: bytes = b"ok") -> None:
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400 and self.status_code not in {429, 500, 502, 503, 504}:
            raise requests.HTTPError(f"status={self.status_code}")


def test_fetch_bytes_retries_exception_then_succeeds(monkeypatch) -> None:
    session = Mock()
    session.request.side_effect = [
        requests.Timeout("timeout"),
        _Response(status_code=200, content=b"payload"),
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "pdf_image_extractor.adapters.transport.http_client.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    client = HttpClient(HttpClientConfig(max_retries=2, backoff_base_seconds=0.1), session=session)

    result = client.fetch_bytes("https://example.com/data")

    assert result == b"payload"
    assert session.request.call_count == 2
    assert sleep_calls == [0.1]


def test_fetch_bytes_retries_status_and_returns_none_when_exhausted(monkeypatch) -> None:
    session = Mock()
    session.request.side_effect = [
        _Response(status_code=503),
        _Response(status_code=503),
        _Response(status_code=503),
    ]
    sleep_calls: list[float] = []
    monkeypatch.setattr(
        "pdf_image_extractor.adapters.transport.http_client.time.sleep",
        lambda seconds: sleep_calls.append(seconds),
    )

    client = HttpClient(HttpClientConfig(max_retries=2, backoff_base_seconds=0.25), session=session)

    result = client.fetch_bytes("https://example.com/unavailable")

    assert result is None
    assert session.request.call_count == 3
    assert sleep_calls == [0.25, 0.5]


def test_fetch_bytes_rotates_user_agent() -> None:
    session = Mock()
    session.request.side_effect = [
        _Response(status_code=200, content=b"a"),
        _Response(status_code=200, content=b"b"),
    ]
    user_agents = ("ua-1", "ua-2")
    client = HttpClient(HttpClientConfig(user_agents=user_agents), session=session)

    assert client.fetch_bytes("https://example.com/1") == b"a"
    assert client.fetch_bytes("https://example.com/2") == b"b"

    first_headers = session.request.call_args_list[0].kwargs["headers"]
    second_headers = session.request.call_args_list[1].kwargs["headers"]
    assert first_headers["User-Agent"] == "ua-1"
    assert second_headers["User-Agent"] == "ua-2"
