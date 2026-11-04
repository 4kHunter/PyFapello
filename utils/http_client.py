import ssl
from types import TracebackType
from typing import Any, Self

import niquests
from niquests.adapters import AsyncHTTPAdapter
from niquests.packages.urllib3.util.ssl_ import create_urllib3_context  # pyright: ignore[reportMissingImports]
from urllib3.util.retry import Retry


class SessionTicketAdapter(AsyncHTTPAdapter):
    def init_poolmanager(self, *args: Any, **kwargs: Any) -> None:
        context = create_urllib3_context()
        context.options &= ~ssl.OP_NO_TICKET

        kwargs["ssl_context"] = context
        super().init_poolmanager(*args, **kwargs)


class CloudflareChallengeError(RuntimeError):
    pass


class BaseHttpClient:
    def __init__(
        self,
        timeout: float = 30,
        max_connections: int = 10,
    ) -> None:
        self.timeout = timeout
        self.max_connections = max_connections
        self._session: niquests.AsyncSession | None = None

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise RuntimeError("HTTP client is already open")

        retry = Retry(
            total=3,
            backoff_factor=1,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods={"GET"},
            respect_retry_after_header=True,
        )

        self._session = niquests.AsyncSession()
        self._session.mount(
            "https://",
            SessionTicketAdapter(
                max_retries=retry,
                disable_http2=True,
                disable_http3=True,
                pool_maxsize=self.max_connections,
                pool_block=True,
            ),
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._session is not None:
            try:
                await self._session.close()
            finally:
                self._session = None

    async def get_response(self, url: str) -> str:
        if self._session is None:
            raise RuntimeError("Use the client inside 'async with'")

        response = await self._session.get(url, timeout=self.timeout)

        if response.headers.get("cf-mitigated") == "challenge":
            raise CloudflareChallengeError(f"Cloudflare challenge: {url}")

        response.raise_for_status()
        html = response.text or ""

        if "/cdn-cgi/challenge-platform/" in html:
            raise CloudflareChallengeError(f"Cloudflare challenge: {url}")

        return html
