import ssl

import pytest
from niquests.exceptions import HTTPError
from urllib3.util.retry import Retry

from tests.conftest import LocalServer, Reply
from utils.http_client import BaseHttpClient, SessionTicketAdapter


class TestSessionLifecycle:
    async def test_context_opens_and_closes_session(self) -> None:
        client = BaseHttpClient()

        async with client as opened:
            assert opened is client
            assert client._session is not None

        assert client._session is None

    async def test_mounts_https_adapter_with_connection_limit_and_retry_policy(self) -> None:
        async with BaseHttpClient(max_connections=7) as client:
            assert client._session is not None
            adapter = client._session.adapters["https://"]

            assert isinstance(adapter, SessionTicketAdapter)
            assert adapter._pool_maxsize == 7
            assert adapter._pool_block is True
            retry = adapter.max_retries
            assert isinstance(retry, Retry)
            assert retry.total == 3
            assert retry.allowed_methods == {"GET"}
            assert 429 in (retry.status_forcelist or ())

    async def test_cannot_be_entered_twice(self) -> None:
        async with BaseHttpClient() as client:
            with pytest.raises(RuntimeError, match="already open"):
                await client.__aenter__()

    async def test_exit_without_session_is_a_noop(self) -> None:
        await BaseHttpClient().__aexit__(None, None, None)

    async def test_session_is_released_even_if_close_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = await BaseHttpClient().__aenter__()
        assert client._session is not None
        real_close = client._session.close

        async def failing_close() -> None:
            await real_close()
            raise OSError("close failed")

        monkeypatch.setattr(client._session, "close", failing_close)

        with pytest.raises(OSError, match="close failed"):
            await client.__aexit__(None, None, None)

        assert client._session is None


class TestSessionTicketAdapter:
    def test_tls_session_tickets_are_not_disabled(self) -> None:
        adapter = SessionTicketAdapter()
        context = adapter.poolmanager.connection_pool_kw["ssl_context"]

        assert context.options & ssl.OP_NO_TICKET == 0


class TestGetResponse:
    async def test_requires_open_session(self) -> None:
        with pytest.raises(RuntimeError, match="async with"):
            await BaseHttpClient().get_response("http://127.0.0.1/")

    async def test_returns_body_text(self, server: LocalServer) -> None:
        server.routes["/ok"] = Reply(body="héllo".encode(), headers={"Content-Type": "text/html; charset=utf-8"})

        async with BaseHttpClient() as client:
            assert await client.get_response(server.url("/ok")) == "héllo"

    async def test_empty_body_returns_empty_string(self, server: LocalServer) -> None:
        server.routes["/empty"] = Reply()

        async with BaseHttpClient() as client:
            assert await client.get_response(server.url("/empty")) == ""

    async def test_http_errors_are_raised(self, server: LocalServer) -> None:
        async with BaseHttpClient() as client:
            with pytest.raises(HTTPError):
                await client.get_response(server.url("/missing"))

    async def test_cloudflare_header_is_rejected(self, server: LocalServer) -> None:
        server.routes["/cf"] = Reply(body=b"x", headers={"cf-mitigated": "challenge"})

        async with BaseHttpClient() as client:
            with pytest.raises(RuntimeError, match="Cloudflare challenge"):
                await client.get_response(server.url("/cf"))

    async def test_cloudflare_challenge_page_is_rejected(self, server: LocalServer) -> None:
        server.routes["/page"] = Reply(body=b'<script src="/cdn-cgi/challenge-platform/h/b"></script>')

        async with BaseHttpClient() as client:
            with pytest.raises(RuntimeError, match="Cloudflare challenge"):
                await client.get_response(server.url("/page"))
