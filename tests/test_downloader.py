import asyncio
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from niquests.exceptions import ChunkedEncodingError, ConnectionError as NiquestsConnectionError, HTTPError, Timeout

from tests.conftest import LocalServer, Reply, media
from utils.downloader import Downloader, retry

CHUNK_SIZE = 256 * 1024


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
        error_after: BaseException | None = None,
        block: asyncio.Event | None = None,
        on_chunk: Any = None,
    ) -> None:
        self.chunks = chunks or []
        self.status_code = status
        self.headers = {"Content-Type": "image/jpeg", **(headers or {})}
        self.error_after = error_after
        self.block = block
        self.on_chunk = on_chunk
        self.chunk_sizes: list[int] = []
        self.closed = False

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise HTTPError(f"{self.status_code} error", response=self)

    async def iter_content(self, chunk_size: int) -> AsyncIterator[bytes]:
        self.chunk_sizes.append(chunk_size)
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk
            if self.on_chunk is not None:
                self.on_chunk()
        if self.block is not None:
            await self.block.wait()
        if self.error_after is not None:
            raise self.error_after


class FakeSession:
    def __init__(self, *outcomes: FakeResponse | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def make_downloader(tmp_path: Path, session: FakeSession | None = None, urls: list[str] | None = None) -> Downloader:
    downloader = Downloader(urls or [], tmp_path)
    downloader._session = session  # type: ignore[assignment]
    return downloader


def files_under(folder: Path) -> list[str]:
    return sorted(str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file())


class TestRetryDecorator:
    async def test_rejects_invalid_arguments(self) -> None:
        with pytest.raises(ValueError, match="attempts"):
            retry(attempts=0)
        with pytest.raises(ValueError, match="delay"):
            retry(delay=-1)

    async def test_returns_first_success_without_sleeping(self, no_retry_sleep: list[float]) -> None:
        calls = 0

        @retry(attempts=3, delay=2)
        async def operation() -> str:
            nonlocal calls
            calls += 1
            return "ok"

        assert await operation() == "ok"
        assert calls == 1
        assert no_retry_sleep == []

    async def test_retries_matching_exceptions_until_success(self, no_retry_sleep: list[float]) -> None:
        calls = 0

        @retry(attempts=3, delay=2)
        async def operation() -> str:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise OSError("boom")
            return "ok"

        assert await operation() == "ok"
        assert calls == 3
        assert no_retry_sleep == [2, 2]

    async def test_raises_last_error_after_all_attempts(self, no_retry_sleep: list[float]) -> None:
        calls = 0

        @retry(attempts=3, delay=1)
        async def operation() -> None:
            nonlocal calls
            calls += 1
            raise OSError(f"failure {calls}")

        with pytest.raises(OSError, match="failure 3"):
            await operation()
        assert calls == 3
        assert no_retry_sleep == [1, 1]

    async def test_single_attempt_does_not_retry(self, no_retry_sleep: list[float]) -> None:
        calls = 0

        @retry(attempts=1)
        async def operation() -> None:
            nonlocal calls
            calls += 1
            raise OSError("boom")

        with pytest.raises(OSError, match="boom"):
            await operation()
        assert calls == 1
        assert no_retry_sleep == []

    async def test_does_not_retry_other_exceptions(self, no_retry_sleep: list[float]) -> None:
        calls = 0

        @retry(attempts=3, exceptions=(OSError,))
        async def operation() -> None:
            nonlocal calls
            calls += 1
            raise ValueError("fatal")

        with pytest.raises(ValueError, match="fatal"):
            await operation()
        assert calls == 1
        assert no_retry_sleep == []


class TestDestinationsAndCandidates:
    def test_places_known_media_in_category_folders(self, tmp_path: Path) -> None:
        downloader = Downloader([], tmp_path)

        assert downloader.get_destination("https://h/a/p.JPG?x=1") == tmp_path / "Images" / "p.JPG"
        assert downloader.get_destination("https://h/a/v.mp4") == tmp_path / "Videos" / "v.mp4"
        assert downloader.get_destination("https://h/a/readme.txt") == tmp_path / "readme.txt"

    @pytest.mark.parametrize("url", ["https://h", "https://h/", "https://h/a/.."])
    def test_rejects_urls_without_filename(self, tmp_path: Path, url: str) -> None:
        with pytest.raises(ValueError, match="Missing filename"):
            Downloader([], tmp_path).get_destination(url)

    def test_deduplicates_input_urls_preserving_order(self, tmp_path: Path) -> None:
        downloader = Downloader(["https://h/b.jpg", "https://h/a.jpg", "https://h/b.jpg"], tmp_path)

        assert downloader.urls == ["https://h/b.jpg", "https://h/a.jpg"]

    def test_thumbnail_images_try_full_size_first_and_keep_query(self, tmp_path: Path) -> None:
        downloader = Downloader([], tmp_path)

        assert downloader.get_candidate_urls("https://h/a/pic_300px.jpg?v=2") == [
            "https://h/a/pic.jpg?v=2",
            "https://h/a/pic_300px.jpg?v=2",
        ]

    @pytest.mark.parametrize("url", ["https://h/a/pic.jpg", "https://h/a/clip_300px.mp4", "https://h/a/doc_300px.txt"])
    def test_other_urls_have_a_single_candidate(self, tmp_path: Path, url: str) -> None:
        assert Downloader([], tmp_path).get_candidate_urls(url) == [url]


class TestCollisionPlanning:
    def test_distinct_urls_with_same_filename_get_unique_destinations(self, tmp_path: Path) -> None:
        urls = ["https://h/a/same.jpg", "https://h/b/same.jpg", "https://h/c/same.jpg"]
        downloader = Downloader(urls, tmp_path)

        downloader.plan_destinations()

        assert [downloader._destinations[url].name for url in urls] == ["same.jpg", "same_1.jpg", "same_2.jpg"]
        assert len(set(downloader._destinations.values())) == 3

    def test_generated_name_never_reuses_an_existing_planned_name(self, tmp_path: Path) -> None:
        urls = ["https://h/a/same.jpg", "https://h/b/same.jpg", "https://h/c/same_1.jpg"]
        downloader = Downloader(urls, tmp_path)

        downloader.plan_destinations()

        names = [downloader._destinations[url].name for url in urls]
        assert names == ["same.jpg", "same_1.jpg", "same_1_1.jpg"]

    def test_shared_candidate_url_is_planned_once(self, tmp_path: Path) -> None:
        urls = ["https://h/a/x_300px.jpg", "https://h/a/x.jpg"]
        downloader = Downloader(urls, tmp_path)

        downloader.plan_destinations()

        assert downloader._destinations == {
            "https://h/a/x.jpg": tmp_path / "Images" / "x.jpg",
            "https://h/a/x_300px.jpg": tmp_path / "Images" / "x_300px.jpg",
        }

    def test_temporary_part_names_are_reserved(self, tmp_path: Path) -> None:
        urls = ["https://h/a/x.bin", "https://h/b/x.bin.part"]
        downloader = Downloader(urls, tmp_path)

        downloader.plan_destinations()

        assert downloader._destinations["https://h/b/x.bin.part"] == tmp_path / "x.bin_1.part"

    def test_invalid_url_is_skipped_without_blocking_others(self, tmp_path: Path) -> None:
        downloader = Downloader(["https://h/", "https://h/a.jpg"], tmp_path)

        downloader.plan_destinations()

        assert downloader._destinations == {"https://h/a.jpg": tmp_path / "Images" / "a.jpg"}


class TestDownloadFile:
    async def test_streams_chunks_into_part_file_then_renames(self, tmp_path: Path) -> None:
        destination = tmp_path / "Images" / "a.jpg"
        seen: list[tuple[bool, bool]] = []
        response = FakeResponse(
            [b"aaa", b"bbb", b"c"],
            on_chunk=lambda: seen.append((destination.exists(), destination.with_name("a.jpg.part").exists())),
        )
        session = FakeSession(response)
        downloader = make_downloader(tmp_path, session)

        await downloader.download_file("https://h/a.jpg", destination)

        assert destination.read_bytes() == b"aaabbbc"
        assert files_under(tmp_path) == ["Images/a.jpg"]
        assert seen == [(False, True)] * 3
        assert response.chunk_sizes == [CHUNK_SIZE]
        assert response.closed
        assert session.calls[0][1] == {"timeout": 30, "stream": True}

    async def test_requires_open_session(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="async with"):
            await make_downloader(tmp_path).download_file("https://h/a.jpg", tmp_path / "a.jpg")

    async def test_http_error_is_not_retried_and_leaves_no_files(self, tmp_path: Path) -> None:
        session = FakeSession(FakeResponse(status=500))
        downloader = make_downloader(tmp_path, session)

        with pytest.raises(HTTPError):
            await downloader.download_file("https://h/a.jpg", tmp_path / "a.jpg")

        assert len(session.calls) == 1
        assert files_under(tmp_path) == []

    @pytest.mark.parametrize(
        ("response", "message"),
        [
            (FakeResponse(headers={"cf-mitigated": "challenge"}), "Cloudflare challenge"),
            (FakeResponse(headers={"Content-Type": "text/html; charset=utf-8"}), "received HTML"),
        ],
    )
    async def test_rejects_challenge_and_html_without_retry(
        self, tmp_path: Path, response: FakeResponse, message: str
    ) -> None:
        session = FakeSession(response)
        downloader = make_downloader(tmp_path, session)

        with pytest.raises(RuntimeError, match=message):
            await downloader.download_file("https://h/a.jpg", tmp_path / "a.jpg")

        assert len(session.calls) == 1
        assert files_under(tmp_path) == []

    @pytest.mark.parametrize(
        "error",
        [ChunkedEncodingError("cut"), NiquestsConnectionError("down"), Timeout("slow"), TimeoutError("slow")],
    )
    async def test_transient_errors_are_retried_with_delay(
        self, tmp_path: Path, error: Exception, no_retry_sleep: list[float]
    ) -> None:
        session = FakeSession(error, FakeResponse([b"ok"]))
        downloader = make_downloader(tmp_path, session)

        await downloader.download_file("https://h/a.jpg", tmp_path / "a.jpg")

        assert len(session.calls) == 2
        assert no_retry_sleep == [4]
        assert (tmp_path / "a.jpg").read_bytes() == b"ok"

    async def test_gives_up_after_three_attempts(self, tmp_path: Path, no_retry_sleep: list[float]) -> None:
        session = FakeSession(Timeout("1"), Timeout("2"), Timeout("3"))
        downloader = make_downloader(tmp_path, session)

        with pytest.raises(Timeout, match="3"):
            await downloader.download_file("https://h/a.jpg", tmp_path / "a.jpg")

        assert len(session.calls) == 3
        assert no_retry_sleep == [4, 4]

    async def test_mid_stream_failure_retries_cleans_part_and_keeps_existing_file(self, tmp_path: Path) -> None:
        destination = tmp_path / "a.jpg"
        destination.write_bytes(b"old")
        session = FakeSession(
            *(FakeResponse([b"partial"], error_after=ChunkedEncodingError("cut")) for _ in range(3)),
        )
        downloader = make_downloader(tmp_path, session)

        with pytest.raises(ChunkedEncodingError):
            await downloader.download_file("https://h/a.jpg", destination)

        assert len(session.calls) == 3
        assert files_under(tmp_path) == ["a.jpg"]
        assert destination.read_bytes() == b"old"

    async def test_mid_stream_failure_then_success_overwrites_atomically(self, tmp_path: Path) -> None:
        session = FakeSession(
            FakeResponse([b"bad"], error_after=ChunkedEncodingError("cut")),
            FakeResponse([b"good"]),
        )
        downloader = make_downloader(tmp_path, session)

        await downloader.download_file("https://h/a.jpg", tmp_path / "a.jpg")

        assert (tmp_path / "a.jpg").read_bytes() == b"good"
        assert files_under(tmp_path) == ["a.jpg"]

    async def test_cancellation_removes_part_file(self, tmp_path: Path) -> None:
        started = asyncio.Event()
        response = FakeResponse([b"partial"], block=asyncio.Event(), on_chunk=started.set)
        downloader = make_downloader(tmp_path, FakeSession(response))
        destination = tmp_path / "Images" / "a.jpg"

        task = asyncio.create_task(downloader.download_file("https://h/a.jpg", destination))
        await asyncio.wait_for(started.wait(), 5)
        assert destination.with_name("a.jpg.part").exists()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert files_under(tmp_path) == []
        assert response.closed


class TestDownloadMedia:
    async def test_prefers_full_size_and_stops_on_success(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/a/p.jpg"] = media(b"full")
        server.routes["/a/p_300px.jpg"] = media(b"thumb")

        async with Downloader([], tmp_path) as downloader:
            final = await downloader.download_media(server.url("/a/p_300px.jpg"))

        assert final == server.url("/a/p.jpg")
        assert server.hits == ["/a/p.jpg"]
        assert (tmp_path / "Images" / "p.jpg").read_bytes() == b"full"

    async def test_falls_back_to_original_url_only_on_404(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/a/p_300px.jpg"] = media(b"thumb")

        async with Downloader([], tmp_path) as downloader:
            final = await downloader.download_media(server.url("/a/p_300px.jpg"))

        assert final == server.url("/a/p_300px.jpg")
        assert server.hits == ["/a/p.jpg", "/a/p_300px.jpg"]
        assert (tmp_path / "Images" / "p_300px.jpg").read_bytes() == b"thumb"
        assert not (tmp_path / "Images" / "p.jpg").exists()

    @pytest.mark.parametrize("status", [403, 500])
    async def test_no_fallback_on_other_http_errors(self, tmp_path: Path, server: LocalServer, status: int) -> None:
        server.routes["/a/p.jpg"] = Reply(status=status)
        server.routes["/a/p_300px.jpg"] = media()

        async with Downloader([], tmp_path) as downloader:
            with pytest.raises(HTTPError):
                await downloader.download_media(server.url("/a/p_300px.jpg"))

        assert server.hits == ["/a/p.jpg"]
        assert files_under(tmp_path) == []

    async def test_error_without_response_is_not_treated_as_404(self, tmp_path: Path) -> None:
        downloader = make_downloader(tmp_path)
        calls: list[str] = []

        async def failing(url: str, destination: Path) -> None:
            calls.append(url)
            raise HTTPError("no response")

        downloader.download_file = failing

        with pytest.raises(HTTPError):
            await downloader.download_media("https://h/p_300px.jpg")

        assert calls == ["https://h/p.jpg"]

    async def test_404_on_last_candidate_is_raised(self, tmp_path: Path, server: LocalServer) -> None:
        async with Downloader([], tmp_path) as downloader:
            with pytest.raises(HTTPError):
                await downloader.download_media(server.url("/a/p_300px.jpg"))

        assert server.hits == ["/a/p.jpg", "/a/p_300px.jpg"]

    async def test_video_urls_are_downloaded_unchanged(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/v/clip_300px.mp4"] = Reply(body=b"video", headers={"Content-Type": "video/mp4"})

        async with Downloader([], tmp_path) as downloader:
            final = await downloader.download_media(server.url("/v/clip_300px.mp4"))

        assert final == server.url("/v/clip_300px.mp4")
        assert (tmp_path / "Videos" / "clip_300px.mp4").read_bytes() == b"video"


class TestDownloadAll:
    async def test_requires_open_session(self, tmp_path: Path) -> None:
        with pytest.raises(RuntimeError, match="async with"):
            await Downloader(["https://h/a.jpg"], tmp_path).download_all()

    async def test_downloads_everything_and_reports_final_urls(self, tmp_path: Path, server: LocalServer) -> None:
        big = b"x" * (CHUNK_SIZE * 2 + 17)
        server.routes["/a/one_300px.jpg"] = media(b"thumb-one")
        server.routes["/a/one.jpg"] = media(big)
        server.routes["/a/two_300px.jpg"] = media(b"thumb-two")
        server.routes["/v/clip.mp4"] = Reply(body=b"video", headers={"Content-Type": "video/mp4"})
        urls = [server.url(p) for p in ("/a/one_300px.jpg", "/a/two_300px.jpg", "/v/clip.mp4")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert sorted(result) == sorted(
            [server.url("/a/one.jpg"), server.url("/a/two_300px.jpg"), server.url("/v/clip.mp4")]
        )
        assert (tmp_path / "Images" / "one.jpg").read_bytes() == big
        assert (tmp_path / "Images" / "two_300px.jpg").read_bytes() == b"thumb-two"
        assert (tmp_path / "Videos" / "clip.mp4").read_bytes() == b"video"
        assert not [name for name in files_under(tmp_path) if name.endswith(".part")]

    async def test_failures_do_not_stop_other_downloads(
        self, tmp_path: Path, server: LocalServer, capsys: pytest.CaptureFixture[str]
    ) -> None:
        server.routes["/a/ok.jpg"] = media(b"ok")
        server.routes["/a/forbidden.jpg"] = Reply(status=403)
        server.routes["/a/html.jpg"] = Reply(body=b"<html>", headers={"Content-Type": "text/html"})
        urls = [server.url(p) for p in ("/a/forbidden.jpg", "/a/html.jpg", "/", "/a/ok.jpg")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert result == [server.url("/a/ok.jpg")]
        assert files_under(tmp_path) == ["Images/ok.jpg"]
        out = capsys.readouterr().out
        assert "403" in out
        assert "received HTML" in out
        assert "Missing filename" in out

    async def test_same_filename_from_different_urls_is_not_overwritten(
        self, tmp_path: Path, server: LocalServer
    ) -> None:
        for folder in ("a", "b", "c"):
            server.routes[f"/{folder}/same.jpg"] = media(f"from-{folder}".encode())
        server.routes["/d/other.jpg"] = media(b"other")
        urls = [server.url(f"/{p}/same.jpg") for p in "abc"] + [server.url("/d/other.jpg")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert sorted(result) == sorted(urls)
        assert files_under(tmp_path) == [
            "Images/other.jpg",
            "Images/same.jpg",
            "Images/same_1.jpg",
            "Images/same_2.jpg",
        ]
        contents = {p.read_bytes() for p in (tmp_path / "Images").glob("same*.jpg")}
        assert contents == {b"from-a", b"from-b", b"from-c"}

    async def test_shared_candidate_is_downloaded_once(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/a/x.jpg"] = media(b"full")
        urls = [server.url("/a/x_300px.jpg"), server.url("/a/x.jpg")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert result == [server.url("/a/x.jpg")]
        assert server.hits == ["/a/x.jpg"]
        assert files_under(tmp_path) == ["Images/x.jpg"]

    async def test_shared_candidate_failure_is_shared_and_fallback_still_works(
        self, tmp_path: Path, server: LocalServer
    ) -> None:
        server.routes["/a/x_300px.jpg"] = media(b"thumb")
        urls = [server.url("/a/x_300px.jpg"), server.url("/a/x.jpg")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert result == [server.url("/a/x_300px.jpg")]
        assert server.count("/a/x.jpg") == 1
        assert files_under(tmp_path) == ["Images/x_300px.jpg"]

    async def test_duplicate_input_urls_are_downloaded_once(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/a/x.jpg"] = media(b"x")
        url = server.url("/a/x.jpg")

        async with Downloader([url, url], tmp_path) as downloader:
            assert await downloader.download_all() == [url]

        assert server.hits == ["/a/x.jpg"]

    async def test_truncated_download_is_retried_then_reported_and_cleaned(
        self, tmp_path: Path, server: LocalServer, no_retry_sleep: list[float]
    ) -> None:
        server.routes["/a/cut.jpg"] = Reply(
            body=b"x" * 10, headers={"Content-Type": "image/jpeg"}, declared_length=1000
        )
        server.routes["/a/ok.jpg"] = media(b"ok")
        urls = [server.url("/a/cut.jpg"), server.url("/a/ok.jpg")]

        async with Downloader(urls, tmp_path) as downloader:
            result = await downloader.download_all()

        assert result == [server.url("/a/ok.jpg")]
        assert server.count("/a/cut.jpg") == 3
        assert no_retry_sleep == [4, 4]
        assert files_under(tmp_path) == ["Images/ok.jpg"]

    async def test_can_be_run_twice_on_the_same_instance(self, tmp_path: Path, server: LocalServer) -> None:
        server.routes["/a/x.jpg"] = media(b"x")
        url = server.url("/a/x.jpg")

        async with Downloader([url], tmp_path) as downloader:
            assert await downloader.download_all() == [url]
            assert await downloader.download_all() == [url]

        assert server.hits == ["/a/x.jpg", "/a/x.jpg"]


class TestCancellation:
    async def test_cancelling_download_all_stops_tasks_and_removes_part_files(
        self, tmp_path: Path, server: LocalServer
    ) -> None:
        release = threading.Event()

        def stall(handler: Any) -> None:
            handler.send_response(200)
            handler.send_header("Content-Type", "image/jpeg")
            handler.send_header("Content-Length", "1000")
            handler.end_headers()
            handler.wfile.write(b"x" * 10)
            handler.wfile.flush()
            release.wait(10)

        server.routes["/a/slow1.jpg"] = stall
        server.routes["/a/slow2.jpg"] = stall
        urls = [server.url("/a/slow1.jpg"), server.url("/a/slow2.jpg")]
        parts = [tmp_path / "Images" / "slow1.jpg.part", tmp_path / "Images" / "slow2.jpg.part"]

        try:
            async with Downloader(urls, tmp_path) as downloader:
                task = asyncio.create_task(downloader.download_all())

                # Both responses are streaming once their temporary files exist.
                for _ in range(500):
                    if all(part.exists() for part in parts):
                        break
                    await asyncio.sleep(0.01)
                assert all(part.exists() for part in parts)

                task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await task

                assert len(downloader._downloads) == 2
                assert all(item.cancelled() for item in downloader._downloads.values())
        finally:
            release.set()

        assert files_under(tmp_path) == []

    async def test_cancelling_before_response_headers_cleans_up_request(
        self, tmp_path: Path, server: LocalServer
    ) -> None:
        headers_waiting = threading.Event()
        release_headers = threading.Event()

        def stall_before_headers(handler: Any) -> None:
            headers_waiting.set()
            release_headers.wait(10)

            try:
                handler.send_response(200)
                handler.send_header("Content-Type", "image/jpeg")
                handler.send_header("Content-Length", "4")
                handler.end_headers()
                handler.wfile.write(b"late")
            except OSError:
                pass

        server.routes["/a/no-headers.jpg"] = stall_before_headers
        url = server.url("/a/no-headers.jpg")

        try:
            async with Downloader([url], tmp_path) as downloader:
                task = asyncio.create_task(downloader.download_all())
                assert await asyncio.wait_for(asyncio.to_thread(headers_waiting.wait, 5), 6)

                task.cancel()

                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 5)

                assert len(downloader._downloads) == 1
                assert all(request.cancelled() for request in downloader._downloads.values())
        finally:
            release_headers.set()

        assert files_under(tmp_path) == []
