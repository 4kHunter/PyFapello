import asyncio
import gc
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from utils import downloader as downloader_module


@dataclass
class Reply:
    status: int = 200
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)
    declared_length: int | None = None


Route = Reply | Callable[[BaseHTTPRequestHandler], None]


class LocalServer:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.hits: list[str] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                pass

            def do_GET(self) -> None:
                with outer._lock:
                    outer.hits.append(self.path)

                route = outer.routes.get(self.path, Reply(status=404))

                if callable(route):
                    route(self)
                    return

                length = route.declared_length if route.declared_length is not None else len(route.body)
                self.send_response(route.status)
                for name, value in route.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(length))
                self.end_headers()
                self.wfile.write(route.body)
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join()

    def url(self, path: str = "") -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}{path}"

    def count(self, path: str) -> int:
        with self._lock:
            return self.hits.count(path)


def media(body: bytes = b"data") -> Reply:
    return Reply(body=body, headers={"Content-Type": "image/jpeg"})


@pytest.fixture
def server() -> Iterator[LocalServer]:
    local = LocalServer()
    local.start()
    yield local
    local.stop()


class _AsyncioProxy:
    """Delegates to asyncio, but replaces sleep so only downloader retries are recorded."""

    def __init__(self, delays: list[float]) -> None:
        self._delays = delays

    async def sleep(self, delay: float) -> None:
        self._delays.append(delay)
        await asyncio.sleep(0)

    def __getattr__(self, name: str) -> object:
        return getattr(asyncio, name)


@pytest.fixture(autouse=True)
def no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Retries must never really wait during tests; returns the requested delays."""
    delays: list[float] = []
    monkeypatch.setattr(downloader_module, "asyncio", _AsyncioProxy(delays))
    return delays


@pytest.fixture(autouse=True)
def collect_garbage_after_test() -> Iterator[None]:
    """Surface leaked transports/files in the test that created them instead of a random later one."""
    yield
    gc.collect()
